"""
Shared config, Telegram client, Groq helpers, and storage functions used
by main.py (the API), poll.py (the scheduled Telegram check), and
backfill.py (the one-time history sweep).
"""

import os
import json
import logging
from datetime import datetime, timedelta, timezone


import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import UserAlreadyParticipantError, InviteHashExpiredError
from supabase import create_client
from pywebpush import webpush, WebPushException

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("job-radar")

# ---------- Config (all from environment variables) ----------
API_ID = os.environ.get("TG_API_ID")
API_HASH = os.environ.get("TG_API_HASH")
SESSION_STRING = os.environ.get("TG_SESSION_STRING")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

VAPID_PUBLIC_KEY = os.environ["VAPID_PUBLIC_KEY"]
VAPID_PRIVATE_KEY = os.environ["VAPID_PRIVATE_KEY"]
VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_CLAIMS_EMAIL", "mailto:you@example.com")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")  # optional — search/tagging degrade to plain-text if unset
GROQ_MODEL = "llama-3.3-70b-versatile"

PUBLIC_CHANNELS = ["careeropportunitiesinghana", "job_linkk", "joblyghana", "jobs_in_ghana", "ghjobbank", "BrainyCareerConnection"]
UNLISTED_CHANNEL_LINK = "https://t.me/uWZDpI7x1KZkOGY0"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

tg = None
if API_ID and API_HASH and SESSION_STRING:
    tg = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)


# ---------- Groq (free-tier LLM) — job tagging + query expansion ----------

def _groq_json(prompt: str) -> dict:
    if not GROQ_API_KEY:
        return {}
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": "Respond with ONLY a JSON object, no other text."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 200,
            },
            timeout=10,
        )
        content = resp.json()["choices"][0]["message"]["content"]
        content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(content)
    except Exception as e:
        log.warning("Groq call failed, falling back to plain text: %s", e)
        return {}


def classify_job(text: str) -> dict:
    """Reads a Telegram post and decides whether it's a genuine job/vacancy
    listing at all (filtering out ads, chatter, motivational/inspirational
    posts, and off-topic posts), and if so, generates a clean short job
    title plus category tags."""
    result = _groq_json(
        "You are filtering a feed of Telegram messages down to genuine "
        "job/vacancy/internship postings only. Read the message and return "
        'JSON like {"is_job": true or false, "title": "a short clean job '
        'title such as \'Sales Associate\' or \'Accountant\', or null if not '
        'a job posting", "tags": ["3-6 short lowercase role/field keywords"]}. '
        "A genuine job posting names a specific role/position and typically "
        "includes at least one of: qualifications, how to apply, location, "
        "salary, or contact details. Set is_job to false for anything else — "
        "e.g. general chatter, motivational or inspirational messages (even "
        "from career-focused channels), ads for products or CV-writing "
        "services, investment pitches, or unrelated announcements. Do not "
        "default to true when unsure — only mark is_job true if the message "
        "clearly describes an actual role someone could apply for.\n\n"
        f"MESSAGE:\n{text[:1500]}"
    )
    is_job = bool(result.get("is_job", False))  # strict default: exclude if Groq call fails or is unclear
    title = result.get("title") or None
    tags = result.get("tags", [])
    tags = [t.lower() for t in tags if isinstance(t, str)]
    return {"is_job": is_job, "title": title, "tags": tags}


def expand_query(query: str) -> list[str]:
    result = _groq_json(
        "A user is searching a job board for this role or keyword: "
        f'"{query}". Return JSON like {{"terms": ["5-8 closely related lowercase '
        'job-search terms, including the original word and common synonyms/titles"]}}.'
    )
    terms = result.get("terms", [])
    terms = [t.lower() for t in terms if isinstance(t, str)]
    return terms or [query.lower()]


# ---------- Telegram channel resolution ----------

async def resolve_unlisted_channel():
    """Handles both public-style and invite-hash-style links so the third
    channel works whether it's actually public-but-unlisted or a private
    invite link."""
    link = UNLISTED_CHANNEL_LINK.rstrip("/")
    tail = link.split("/")[-1]

    try:
        entity = await tg.get_entity(tail)
        log.info("Resolved unlisted channel as public username: %s", tail)
        return entity
    except Exception as e:
        log.info("Not a plain username (%s) — trying invite-hash join", e)

    invite_hash = tail.lstrip("+")
    try:
        updates = await tg(ImportChatInviteRequest(invite_hash))
        chat = updates.chats[0]
        log.info("Joined via invite hash, chat id=%s", chat.id)
        return chat
    except UserAlreadyParticipantError:
        async for dialog in tg.iter_dialogs():
            if getattr(dialog.entity, "id", None) and invite_hash.lower() in (dialog.name or "").lower():
                return dialog.entity
        raise RuntimeError(
            "Already joined but couldn't re-resolve the chat automatically. "
            "Check your dialogs list to confirm the channel name."
        )
    except InviteHashExpiredError:
        raise RuntimeError(
            "This invite link has expired. Get a fresh invite link for the "
            "third channel and update UNLISTED_CHANNEL_LINK."
        )


# ---------- Storage ----------

def store_job(channel_name: str, text: str, posted_at: datetime, link: str | None, tg_key: str | None = None):
    classification = classify_job(text)
    if not classification["is_job"]:
        log.info("Skipped non-job post (%s)", tg_key)
        return None

    row = {
        "channel": channel_name,
        "text": text,
        "posted_at": posted_at.isoformat(),
        "link": link,
        "title": classification["title"],
        "tags": classification["tags"],
        "tg_key": tg_key,
    }
    try:
        supabase.table("jobs").insert(row).execute()
        log.info("Stored: %s", tg_key)
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            log.info("Skipped duplicate post (%s)", tg_key)
        else:
            raise
    return row


def notify_matching_subscribers(job_text: str):
    subs = supabase.table("subscriptions").select("*").execute().data
    lowered = job_text.lower()
    for sub in subs:
        keywords = sub.get("keywords") or []
        if keywords and not any(kw.lower() in lowered for kw in keywords):
            continue
        try:
            webpush(
                subscription_info=json.loads(sub["push_subscription"]),
                data=json.dumps({
                    "title": "New job posted",
                    "body": job_text[:150],
                }),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CLAIMS_EMAIL},
            )
        except WebPushException as e:
            log.warning("Push failed for a subscriber (removing if expired): %s", e)
            if e.response is not None and e.response.status_code in (404, 410):
                supabase.table("subscriptions").delete().eq("id", sub["id"]).execute()


# ---------- Sync state (tracks last message id seen per channel) ----------

def get_last_message_id(channel_id: int) -> int:
    row = supabase.table("sync_state").select("last_message_id").eq("channel_id", channel_id).execute().data
    return row[0]["last_message_id"] if row else 0


def set_last_message_id(channel_id: int, message_id: int):
    supabase.table("sync_state").upsert({"channel_id": channel_id, "last_message_id": message_id}).execute()

# ---------- Retention: keep only the last 30 days ----------

RETENTION_DAYS = 30


def retention_cutoff_iso() -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    return cutoff.isoformat()


def purge_old_jobs():
    """Deletes jobs older than RETENTION_DAYS. Call this periodically
    (poll.py does, on every scheduled run) so the table doesn't grow
    forever and old posts drop out of the feed."""
    cutoff = retention_cutoff_iso()
    result = supabase.table("jobs").delete().lt("posted_at", cutoff).execute()
    removed = len(result.data) if result.data else 0
    if removed:
        log.info("Purged %d job(s) older than %d days", removed, RETENTION_DAYS)
    return removed
