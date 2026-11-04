"""Count, archive, and delete posts."""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .api import Client
from .store import Store, now_iso

TWEET_FIELDS = ",".join([
    "article", "attachments", "author_id", "card_uri", "community_id", "context_annotations",
    "conversation_id", "created_at", "display_text_range", "edit_controls", "edit_history_tweet_ids",
    "entities", "geo", "id", "in_reply_to_user_id", "lang", "note_tweet", "possibly_sensitive",
    "public_metrics", "referenced_tweets", "reply_settings", "scopes", "source", "text", "withheld",
])
MEDIA_FIELDS = "alt_text,duration_ms,height,media_key,preview_image_url,public_metrics,type,url,variants,width"
POLL_FIELDS = "duration_minutes,end_datetime,id,options,voting_status"
EXPANSIONS = "attachments.media_keys,attachments.poll_ids,referenced_tweets.id"


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


def to_row(post: dict, media: dict, refs: dict, polls: dict) -> tuple:
    kind, ref_id = classify(post)
    p_media = [media[k] for k in post.get("attachments", {}).get("media_keys", []) if k in media]
    poll_ids = post.get("attachments", {}).get("poll_ids", [])
    if poll_ids:
        post = {**post, "_polls": [polls[i] for i in poll_ids if i in polls]}
    pm = post.get("public_metrics", {})
    referenced = [refs[r["id"]] for r in post.get("referenced_tweets", []) if r["id"] in refs]
    return (
        post["id"], post["author_id"], post["created_at"], kind,
        post.get("note_tweet", {}).get("text") or post.get("text", ""),
        post.get("conversation_id"), post.get("in_reply_to_user_id"), ref_id,
        pm.get("impression_count"), pm.get("like_count"), pm.get("retweet_count"), pm.get("reply_count"),
        pm.get("quote_count"), pm.get("bookmark_count"),
        json.dumps(p_media), json.dumps(referenced), json.dumps(post), now_iso(),
    )


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


def archive(client: Client, store: Store, username: str, start: datetime, end: datetime,
            raw_dir: Path | None = None) -> int:
    """Save every post in [start, end) to the store. Each page is written as it arrives."""
    if raw_dir:
        raw_dir.mkdir(parents=True, exist_ok=True)
    saved, page, token = 0, 0, None
    while True:
        params = {
            "query": f"from:{username}", "start_time": iso(start), "end_time": iso(end), "max_results": 500,
            "tweet.fields": TWEET_FIELDS, "media.fields": MEDIA_FIELDS, "poll.fields": POLL_FIELDS,
            "expansions": EXPANSIONS,
        }
        if token:
            params["next_token"] = token
        data = client.get("/tweets/search/all", params, app=True)
        page += 1
        if raw_dir:
            (raw_dir / f"{iso(start)}_{page:04d}.json".replace(":", "")).write_text(json.dumps(data))
        inc = data.get("includes", {})
        media = {m["media_key"]: m for m in inc.get("media", [])}
        refs = {t["id"]: t for t in inc.get("tweets", [])}
        polls = {p["id"]: p for p in inc.get("polls", [])}
        rows = [to_row(p, media, refs, polls) for p in data.get("data", [])]
        store.upsert(rows)
        saved += len(rows)
        client.log(f"archived page {page}: {saved} posts")
        token = data.get("meta", {}).get("next_token")
        if not token:
            return saved
        time.sleep(1.1)


def delete_pending(client: Client, store: Store, me_id: str, limit: int | None = None,
                   progress_every: int = 100) -> Counter:
    """Delete archived posts, oldest first. Posts and reposts use separate endpoints with separate rate
    limits, so they run in two threads. Only rows already in the store are touched."""
    todo = store.pending(me_id)
    if limit:
        todo = todo[:limit]
    groups = [[r for r in todo if r[1] != "repost"], [r for r in todo if r[1] == "repost"]]
    counts: Counter = Counter()
    lock, stop = threading.Lock(), threading.Event()
    client.log(f"{len(todo)} pending ({len(groups[0])} posts, {len(groups[1])} reposts)")

    def worker(items):
        for post_id, kind, ref_id in items:
            if stop.is_set():
                return
            if kind == "repost":
                if not ref_id:
                    store.mark(post_id, "failed", "repost has no source post id")
                    continue
                path = f"/users/{me_id}/retweets/{ref_id}"
            else:
                path = f"/tweets/{post_id}"
            status, _, body = client.request("DELETE", path)
            data = body.get("data", {}) if isinstance(body, dict) else {}
            if status in (401, 403):
                stop.set()
                client.log(f"stopping: X returned {status} {json.dumps(body)[:300]}. "
                           "Check that the app has Read and write permission and the access token was regenerated after.")
                return
            if status == 200 and (data.get("deleted") is True or data.get("retweeted") is False):
                result, error = "deleted", None
            elif status == 404:
                result, error = "not_found", json.dumps(body)[:500]
            else:
                result, error = "failed", f"{status} {json.dumps(body)[:480]}"
            store.mark(post_id, result, error)
            with lock:
                counts[result] += 1
                done = sum(counts.values())
                if done % progress_every == 0:
                    client.log(f"progress: {done}/{len(todo)} ({dict(counts)})")

    threads = [threading.Thread(target=worker, args=(g,), daemon=True) for g in groups if g]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if stop.is_set():
        counts["stopped_on_auth_error"] = 1
    return counts
