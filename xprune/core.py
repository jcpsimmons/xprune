"""Find posts in a date window, then delete them."""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from .api import Client


class Post(NamedTuple):
    id: str
    created_at: str
    type: str  # original, reply, quote, repost
    ref_id: str | None  # for a repost, the post that was reposted


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def classify(post: dict) -> tuple[str, str | None]:
    """Return (post_type, referenced_post_id)."""
    refs = {r["type"]: r["id"] for r in post.get("referenced_tweets", [])}
    if "retweeted" in refs:
        return "repost", refs["retweeted"]
    if "replied_to" in refs:
        return "reply", refs["replied_to"]
    if "quoted" in refs:
        return "quote", refs["quoted"]
    return "original", None


def count(client: Client, username: str, start: datetime, end: datetime) -> int:
    """Count posts in [start, end) with the full-archive counts endpoint. Billed per request, not per post."""
    total, token = 0, None
    while True:
        params = {"query": f"from:{username}", "start_time": iso(start), "end_time": iso(end), "granularity": "day"}
        if token:
            params["next_token"] = token
        data = client.get("/tweets/counts/all", params, app=True)
        total += data.get("meta", {}).get("total_tweet_count", 0)
        token = data.get("meta", {}).get("next_token")
        if not token:
            return total
        time.sleep(1.1)


def _search(client: Client, username: str, start: datetime, end: datetime) -> list[Post]:
    """Every post in [start, end), oldest first."""
    found, token = [], None
    while True:
        params = {"query": f"from:{username}", "start_time": iso(start), "end_time": iso(end),
                  "max_results": 500, "tweet.fields": "created_at,referenced_tweets"}
        if token:
            params["next_token"] = token
        data = client.get("/tweets/search/all", params, app=True)
        for p in data.get("data", []):
            kind, ref = classify(p)
            found.append(Post(p["id"], p["created_at"], kind, ref))
        token = data.get("meta", {}).get("next_token")
        time.sleep(1.1)
        if not token:
            return sorted(found, key=lambda p: p.created_at)


def find_posts(client: Client, username: str, start: datetime, end: datetime, types: set[str] | None = None,
               limit: int | None = None, chunk_days: int = 30) -> list[Post]:
    """List posts in [start, end), oldest first, keeping only `types`. Asks only for the fields needed to
    delete, with no expansions, so X bills only the posts themselves.

    Search returns newest first, so with a limit the window is searched in chunks from the oldest end and
    the search stops once enough posts are found. That keeps a `--limit 1` test from paying to read the
    whole history."""
    windows = [(start, end)]
    if limit:
        windows, s = [], start
        while s < end:
            windows.append((s, min(s + timedelta(days=chunk_days), end)))
            s += timedelta(days=chunk_days)
    found: list[Post] = []
    for s, e in windows:
        found += [p for p in _search(client, username, s, e) if types is None or p.type in types]
        if limit and len(found) >= limit:
            return found[:limit]
    return found


def delete_posts(client: Client, me_id: str, posts: list[Post], progress_every: int = 100) -> Counter:
    """Delete posts oldest first. Posts and reposts use separate endpoints with separate rate limits,
    so they run in two threads."""
    groups = [[p for p in posts if p.type != "repost"], [p for p in posts if p.type == "repost"]]
    counts: Counter = Counter()
    lock, stop = threading.Lock(), threading.Event()
    client.log(f"deleting {len(posts)} ({len(groups[0])} posts, {len(groups[1])} reposts)")

    def worker(items: list[Post]) -> None:
        for p in items:
            if stop.is_set():
                return
            if p.type == "repost":
                if not p.ref_id:
                    with lock:
                        counts["failed"] += 1
                    continue
                path = f"/users/{me_id}/retweets/{p.ref_id}"
            else:
                path = f"/tweets/{p.id}"
            status, _, body = client.request("DELETE", path)
            data = body.get("data", {}) if isinstance(body, dict) else {}
            if status in (401, 403):
                stop.set()
                client.log(f"stopping: X returned {status} {json.dumps(body)[:300]}. "
                           "Check that the app has Read and write permission and the access token was regenerated after.")
                return
            if status == 200 and (data.get("deleted") is True or data.get("retweeted") is False):
                result = "deleted"
            elif status == 404:
                result = "not_found"
            else:
                result = "failed"
                client.log(f"failed {p.id}: {status} {json.dumps(body)[:300]}")
            with lock:
                counts[result] += 1
                done = sum(counts.values())
                if done % progress_every == 0:
                    client.log(f"progress: {done}/{len(posts)} ({dict(counts)})")

    threads = [threading.Thread(target=worker, args=(g,), daemon=True) for g in groups if g]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if stop.is_set():
        counts["stopped_on_auth_error"] = 1
    return counts
