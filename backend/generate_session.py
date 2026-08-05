"""
Run this ONCE on your own laptop (not on the server) to log into your
Telegram account and get a session string. Telegram will text/send you a
login code — enter it when prompted.

    pip install telethon
    python generate_session.py

Copy the printed session string into the TG_SESSION_STRING environment
variable on your hosting platform. Keep it secret — it's equivalent to
being logged into your Telegram account.
"""

from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(input("Your api_id (from https://my.telegram.org): "))
API_HASH = input("Your api_hash: ")

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    print("\nYour session string (copy everything below into TG_SESSION_STRING):\n")
    print(client.session.save())
