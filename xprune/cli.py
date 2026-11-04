"""xprune: delete your X posts older than N days through the official X API."""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .api import Client
from .core import count, delete_posts, find_posts, iso

# Pay-per-use prices from https://docs.x.com/x-api/getting-started/pricing, checked October 2026.
PRICE_READ = 0.005
PRICE_DELETE = 0.010
PRICE_COUNT_REQUEST = 0.010
TYPES = ("original", "reply", "quote", "repost")


def lock_or_exit(path: Path):
    """Keep two runs (say, a cron job and a manual run) from deleting at the same time."""
    try:
        import fcntl
    except ImportError:  # Windows: no lock
        return None
    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(f"another xprune run holds {path}; exiting")
    return handle


def parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def require_write(level: str) -> None:
    if "write" not in level:
        raise SystemExit(
            f"your access token is '{level or 'unknown'}' only, so X will refuse deletes. In the developer console, "
            "set the app's permissions to Read and write, then regenerate the access token and secret."
        )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="xprune", description=__doc__)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def windowed(p):
        p.add_argument("--older-than", type=int, default=90, metavar="DAYS", help="age cutoff in days (default 90)")
        p.add_argument("--since", help="only posts after this ISO time (default: account creation)")

    windowed(sub.add_parser("count", help="count posts older than the cutoff and estimate the cost"))
    p = sub.add_parser("delete", help="delete posts older than the cutoff (dry run unless --yes)")
    windowed(p)
    p.add_argument("--types", default=",".join(TYPES),
                   help=f"comma-separated post types to delete (default: {','.join(TYPES)})")
    p.add_argument("--limit", type=int, help="delete at most N posts, oldest first (try 1 first)")
    p.add_argument("--yes", action="store_true", help="actually delete; without it, only lists what would go")
    args = ap.parse_args(argv)

    client = Client.from_env()
    user, level = client.me()
    print(f"@{user['username']} ({level or 'access level unknown'})", file=sys.stderr)
    end = datetime.now(timezone.utc) - timedelta(days=args.older_than)
    start = parse_ts(args.since) if args.since else parse_ts(user["created_at"])
    if start >= end:
        print("nothing is older than the cutoff")
        return

    if args.cmd == "count":
        n = count(client, user["username"], start, end)
        print(f"{n} posts from {iso(start)} to {iso(end)}")
        print(f"estimated cost: about ${n * (PRICE_READ + PRICE_DELETE):.2f} to delete them "
              f"(prices as of October 2026; check the X developer console). "
              f"This count cost about ${PRICE_COUNT_REQUEST * ((end - start).days // 30 + 1):.2f}.")
        return

    types = {t.strip() for t in args.types.split(",") if t.strip()}
    unknown = types - set(TYPES)
    if unknown:
        raise SystemExit(f"unknown post types: {', '.join(sorted(unknown))}")
    lock = lock_or_exit(Path(os.environ.get("XPRUNE_LOCK", Path.home() / ".xprune.lock")))
    posts = [p for p in find_posts(client, user["username"], start, end) if p.type in types]
    if args.limit:
        posts = posts[:args.limit]
    by_type = Counter(p.type for p in posts)
    print(f"{len(posts)} posts from {iso(start)} to {iso(end)}: "
          + (", ".join(f"{by_type[t]} {t}" for t in TYPES if by_type[t]) or "none"))
    if not posts:
        return
    if not args.yes:
        print(f"dry run: nothing deleted. Deleting these costs about ${len(posts) * PRICE_DELETE:.2f}; "
              "re-run with --yes.")
        return

    require_write(level)
    counts = delete_posts(client, user["id"], posts)
    print(f"deleted {counts['deleted']}, not found {counts['not_found']}, failed {counts['failed']}")
    del lock
    if counts.get("stopped_on_auth_error"):
        sys.exit(2)
    if counts["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
