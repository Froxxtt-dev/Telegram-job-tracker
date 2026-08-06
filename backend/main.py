"""
Job Radar API
-------------
A stateless FastAPI service — search stored jobs, save a push
subscription, hand out the VAPID public key. It does NOT hold a live
Telegram connection (that's poll.py's job, run on a schedule via GitHub
Actions), so it's safe to run on Render's free tier even though the
service sleeps when idle.

Run with:  uvicorn main:app --host 0.0.0.0 --port 8000
"""

import json
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from shared import supabase, VAPID_PUBLIC_KEY, expand_query, retention_cutoff_iso

app = FastAPI(title="Job Radar API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your GitHub Pages URL once deployed
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/vapid-public-key")
def vapid_public_key():
    return {"key": VAPID_PUBLIC_KEY}


@app.get("/jobs")
def search_jobs(q: str = ""):
    query = (
        supabase.table("jobs")
        .select("*")
        .gte("posted_at", retention_cutoff_iso())
        .order("posted_at", desc=True)
        .limit(200)
    )
    data = query.execute().data
    if q:
        terms = expand_query(q)

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
