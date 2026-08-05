"""
Job Radar backend
------------------
1. Logs into Telegram as YOU (via a saved session string) and listens to
   three channels for new messages.
2. Stores every message as a "job" row in Supabase.
3. Checks each new message against everyone's saved search keywords and
   sends a Web Push notification to matching subscribers.
4. Exposes a tiny HTTP API for the PWA: search jobs, save a push
   subscription + keywords, fetch the VAPID public key.

Run with:  uvicorn main:app --host 0.0.0.0 --port 8000
(Render/Railway will set $PORT for you — see start command in README)
"""

import os
import json
import asyncio
import logging
import requests
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.errors import UserAlreadyParticipantError, InviteHashExpiredError

from supabase import create_client
from pywebpush import webpush, WebPushException

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("job-radar")

# ---------- Config (all from environment variables) ----------
API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION_STRING = os.environ["TG_SESSION_STRING"]

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

VAPID_PUBLIC_KEY = os.environ["VAPID_PUBLIC_KEY"]
VAPID_PRIVATE_KEY = os.environ["VAPID_PRIVATE_KEY"]
VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_CLAIMS_EMAIL", "mailto:you@example.com")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")  # optional — search/tagging degrade to plain-text if unset
GROQ_MODEL = "llama-3.3-70b-versatile"

# The three channels. Public ones use their @username. The unlisted one
# is handled by CHANNEL_INVITE_LINK below (see README for how this
# resolves whichever link form Telegram gave you).
PUBLIC_CHANNELS = ["careeropportunitiesinghana", "job_linkk"]
UNLISTED_CHANNEL_LINK = "https://t.me/uWZDpI7x1KZkOGY0"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
tg = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

app = FastAPI(title="Job Radar API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your GitHub Pages URL once deployed
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Groq (free-tier LLM) — job tagging + query expansion ----------

def _groq_json(prompt: str) -> dict:
    """Calls Groq's chat endpoint and parses a JSON object from the reply.
    Returns {} on any failure so callers can fall back to plain-text behavior."""
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


def tag_job(text: str) -> list[str]:
    """Extracts a short list of role/category keywords for a job post so
    related searches (e.g. 'accounting' matching a 'bookkeeper' post) work."""
    result = _groq_json(
        "Read this job posting and return JSON like "
        '{"tags": ["role or field keywords, 3-6 short lowercase terms"]}. '
        "Include the job title, its general field, and seniority if stated.\n\n"
        f"POSTING:\n{text[:1500]}"
    )
    tags = result.get("tags", [])
    return [t.lower() for t in tags if isinstance(t, str)]


def expand_query(query: str) -> list[str]:
    """Expands a search term into related role/keyword variants so search
    understands meaning, not just literal substrings."""
    result = _groq_json(
        "A user is searching a job board for this role or keyword: "
        f'"{query}". Return JSON like {{"terms": ["5-8 closely related lowercase '
        'job-search terms, including the original word and common synonyms/titles"]}}.'
    )
    terms = result.get("terms", [])
    terms = [t.lower() for t in terms if isinstance(t, str)]
    return terms or [query.lower()]


# ---------- Telegram side ----------

async def resolve_unlisted_channel():
    """Handles both public-style and invite-hash-style links so the third
    channel works whether it's actually public-but-unlisted or a private
    invite link."""
    link = UNLISTED_CHANNEL_LINK.rstrip("/")
    tail = link.split("/")[-1]

    # Case 1: looks like a normal @username channel
    try:
        entity = await tg.get_entity(tail)
        log.info("Resolved unlisted channel as public username: %s", tail)
        return entity
    except Exception as e:
        log.info("Not a plain username (%s) — trying invite-hash join", e)

    # Case 2: treat it as an invite hash (covers t.me/joinchat/HASH and t.me/+HASH too)
    invite_hash = tail.lstrip("+")
    try:
        updates = await tg(ImportChatInviteRequest(invite_hash))
        chat = updates.chats[0]
        log.info("Joined via invite hash, chat id=%s", chat.id)
        return chat
    except UserAlreadyParticipantError:
        # Already joined in a previous run — just resolve normally
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


def store_job(channel_name: str, text: str, posted_at: datetime, link: str | None):
    row = {
        "channel": channel_name,
        "text": text,
        "posted_at": posted_at.isoformat(),
        "link": link,
        "tags": tag_job(text),
    }
    supabase.table("jobs").insert(row).execute()
    return row


def notify_matching_subscribers(job_text: str):
    subs = supabase.table("subscriptions").select("*").execute().data
    lowered = job_text.lower()
    for sub in subs:
        keywords = sub.get("keywords") or []
        if keywords and not any(kw.lower() in lowered for kw in keywords):
            continue  # subscriber wants only specific roles and none matched
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


async def telegram_worker():
    await tg.start()
    log.info("Telegram client connected as you.")

    entities = []
    for username in PUBLIC_CHANNELS:
        entities.append(await tg.get_entity(username))
    entities.append(await resolve_unlisted_channel())

    @tg.on(events.NewMessage(chats=entities))
    async def handler(event):
        text = event.raw_text.strip()
        if not text:
            return
        channel = getattr(event.chat, "title", None) or getattr(event.chat, "username", "unknown")
        username = getattr(event.chat, "username", None)
        link = f"https://t.me/{username}/{event.id}" if username else None
        posted_at = event.date.astimezone(timezone.utc)

        store_job(channel, text, posted_at, link)
        notify_matching_subscribers(text)
        log.info("Stored + checked new post from %s", channel)

    log.info("Listening on: %s", [getattr(e, "title", getattr(e, "username", "?")) for e in entities])
    await tg.run_until_disconnected()


@app.on_event("startup")
async def startup():
    asyncio.create_task(telegram_worker())


# ---------- API for the PWA ----------

@app.get("/vapid-public-key")
def vapid_public_key():
    return {"key": VAPID_PUBLIC_KEY}


@app.get("/jobs")
def search_jobs(q: str = ""):
    query = supabase.table("jobs").select("*").order("posted_at", desc=True).limit(200)
    data = query.execute().data
    if q:
        terms = expand_query(q)  # e.g. "accounting" -> accounting, bookkeeping, finance, accountant...

        def matches(job):
            haystack = job["text"].lower() + " " + " ".join(job.get("tags") or [])
            return any(term in haystack for term in terms)

        data = [j for j in data if matches(j)]
    return data


class SubscribeBody(BaseModel):
    push_subscription: dict
    keywords: list[str] = []


@app.post("/subscribe")
def subscribe(body: SubscribeBody):
    endpoint = body.push_subscription.get("endpoint")
    existing = (
        supabase.table("subscriptions")
        .select("id")
        .eq("endpoint", endpoint)
        .execute()
        .data
    )
    row = {
        "endpoint": endpoint,
        "push_subscription": json.dumps(body.push_subscription),
        "keywords": body.keywords,
    }
    if existing:
        supabase.table("subscriptions").update(row).eq("id", existing[0]["id"]).execute()
    else:
        supabase.table("subscriptions").insert(row).execute()
    return {"ok": True}
