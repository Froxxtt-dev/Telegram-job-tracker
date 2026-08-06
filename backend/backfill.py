"""
Run this ONCE (locally, or as a one-off job) to pull each channel's past
messages into Supabase — everything the live listener missed because it
only sees posts from the moment it starts.

Setup: create a `.env` file in this same folder (not committed to git)
with the same variables as `.env.example`, including GROQ_API_KEY.

    pip install -r requirements.txt python-dotenv
    python backfill.py

It paces itself (~1 request/sec) to stay well within Groq's free-tier
rate limits, so a few hundred old posts will take a few minutes — that's
expected, just let it run.

Safe to re-run: already-stored posts are skipped via a unique tg_key,
so running it twice (or running it after the live listener has already
caught some of the same messages) won't create duplicates.
"""

import os
import time
import asyncio
import logging
from datetime import timezone

from dotenv import load_dotenv
load_dotenv()

from shared import tg, resolve_unlisted_channel, PUBLIC_CHANNELS, store_job

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("backfill")

LIMIT_PER_CHANNEL = int(os.environ.get("BACKFILL_LIMIT", "500"))


async def backfill_channel(entity, name: str):
    count = 0
    username = getattr(entity, "username", None)
    async for msg in tg.iter_messages(entity, limit=LIMIT_PER_CHANNEL):
        text = (msg.raw_text or "").strip()
        if not text:
            continue
        tg_key = f"{entity.id}_{msg.id}"
        link = f"https://t.me/{username}/{msg.id}" if username else None
        posted_at = msg.date.astimezone(timezone.utc)

        store_job(name, text, posted_at, link, tg_key=tg_key)
        count += 1
        time.sleep(1.1)  # pace Groq tagging calls
    log.info("Backfilled %d posts from %s", count, name)


async def main_backfill():
    await tg.start()
    log.info("Connected. Starting backfill (limit=%d per channel)...", LIMIT_PER_CHANNEL)

    entities = []
    for username in PUBLIC_CHANNELS:
        e = await tg.get_entity(username)
        entities.append((e, username))
    unlisted = await resolve_unlisted_channel()
    entities.append((unlisted, getattr(unlisted, "title", "unlisted-channel")))

    for entity, name in entities:
        await backfill_channel(entity, name)

    log.info("Backfill complete.")


if __name__ == "__main__":
    asyncio.run(main_backfill())
