# Job Radar — setup guide

Architecture:

```
Telegram (your account) --Telethon--> backend (Render, always-on)
                                          |-- Supabase (jobs + subscriptions)
                                          |-- Web Push --> your PWA
Frontend (GitHub Pages) <--fetch/search-- backend
```

## 1. Get Telegram API credentials
Go to https://my.telegram.org → API Development Tools → create an app.
You'll get `api_id` and `api_hash`. Keep these private.

## 2. Generate your session string (run locally, once)
```
cd backend
pip install telethon
python generate_session.py
```
Enter your `api_id`/`api_hash`, then your phone number and the login code
Telegram sends you. Copy the printed session string — you'll paste it into
`TG_SESSION_STRING` on the server. This is equivalent to being logged into
your account, so never commit it or share it.

Make sure your account has already joined all three channels/groups before
running the backend (join the two public ones normally; join the third via
its invite link in your Telegram app first — the backend will also try to
join it automatically the first time it runs, but joining it yourself first
avoids surprises).

## 3. Create a free Supabase project
https://supabase.com → New project → SQL editor → run:

```sql
create table jobs (
  id bigint generated always as identity primary key,
  channel text,
  text text,
  posted_at timestamptz,
  link text,
  inserted_at timestamptz default now()
);

create table subscriptions (
  id bigint generated always as identity primary key,
  endpoint text unique,
  push_subscription text,
  keywords text[]
);
```

Grab `SUPABASE_URL` and the `service_role` key from Project Settings → API.

## 4. Generate VAPID keys (for Web Push)
```
npx web-push generate-vapid-keys
```
Save the public and private key.

## 5. Deploy the backend (Render free tier)
- New → Web Service → point at this `backend/` folder
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Add all the environment variables from `.env.example` with your real values
- Note the resulting URL, e.g. `https://job-radar-api.onrender.com`

Free-tier services on Render sleep after inactivity on the *web* type but a
background worker/always-on instance is worth it here since Telethon needs
a persistent connection — if the free web service sleeps and this matters
to you, Render's paid "background worker" instance type ($7/mo) keeps it
running 24/7; otherwise the free tier will reconnect and catch up shortly
after each visit/ping.

## 6. Deploy the frontend (GitHub Pages — same as ExcelTutor)
- Edit `frontend/app.js`, set `BACKEND_URL` to your Render URL from step 5
- Add two icon PNGs at `frontend/icons/icon-192.png` and `icon-512.png`
  (any square logo works — even a placeholder for now)
- Push the `frontend/` folder to a GitHub repo, enable Pages on it
- Open the deployed URL, tap "Enable alerts" — you'll get a browser
  permission prompt, then you're subscribed

## Notes / things worth knowing
- Search box: typing a role filters both the on-page feed AND (if you hit
  "Enable alerts" while text is in the box) narrows what triggers a push —
  leave it empty before enabling alerts if you want notified on everything.
- The channel `t.me/uWZDpI7x1KZkOGY0` doesn't look like a public username —
  the backend tries to resolve it as one first, then falls back to treating
  it as an invite link. If it still fails on first run, check the Render
  logs; you may need a fresh invite link if the old one expired.
- All storage is in Supabase, so the ephemeral disk on Render free tier
  isn't a problem — nothing important lives only on the server.
