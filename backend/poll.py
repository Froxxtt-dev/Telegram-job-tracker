"""
Job Radar poller — the piece that actually checks Telegram.

Runs ONE short check: connects, asks each channel "anything new since the
last message I saw?", stores + tags any new posts with Groq, sends push
notifications, then disconnects. Meant to run on a schedule (every ~10
minutes via GitHub Actions — see .github/workflows/poll.yml) rather than
staying connected 24/7, which sidesteps free-tier hosts putting a
long-lived connection to sleep.

Local run:
    pip install -r requirements.txt python-dotenv
    python poll.py
(needs a local .env — see .env.example)
"""

import asyncio
import logging
from datetime import timezone

from shared import (
    tg, resolve_unlisted_channel, PUBLIC_CHANNELS,
    store_job, notify_matching_subscribers,
    get_last_message_id, set_last_message_id, purge_old_jobs, log,
)

CHECK_LIMIT = 100  # safety cap per channel per run, in case of a big backlog


async def poll_channel(entity, name: str):
    last_id = get_last_message_id(entity.id)
    username = getattr(entity, "username", None)
    new_max = last_id
    found = 0

    async for msg in tg.iter_messages(entity, min_id=last_id, limit=CHECK_LIMIT):
        text = (msg.raw_text or "").strip()
        new_max = max(new_max, msg.id)
        if not text:
            continue
        tg_key = f"{entity.id}_{msg.id}"
        link = f"https://t.me/{username}/{msg.id}" if username else None
        posted_at = msg.date.astimezone(timezone.utc)

        store_job(name, text, posted_at, link, tg_key=tg_key)
        notify_matching_subscribers(text)
        found += 1

    if new_max != last_id:
        set_last_message_id(entity.id, new_max)
    log.info("%s: %d new post(s)", name, found)


async def main():
    await tg.start()
    log.info("Connected. Checking channels...")

    entities = []
    for username in PUBLIC_CHANNELS:
        e = await tg.get_entity(username)
        entities.append((e, username))
    unlisted = await resolve_unlisted_channel()
    entities.append((unlisted, getattr(unlisted, "title", "unlisted-channel")))

    for entity, name in entities:
        await poll_channel(entity, name)

    purge_old_jobs()

    await tg.disconnect()
    log.info("Check complete.")


if __name__ == "__main__":
    asyncio.run(main())
