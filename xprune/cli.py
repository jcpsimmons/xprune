"""xprune: archive your old X posts, then delete them."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .api import Client
from .core import archive, count, delete_pending, iso
from .store import Store, parse_ts

# Pay-per-use prices from https://docs.x.com/x-api/getting-started/pricing, checked October 2026.
PRICE_READ = 0.005
PRICE_DELETE = 0.010
PRICE_COUNT_REQUEST = 0.010


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


def window(args, store: Store, user: dict) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc) - timedelta(days=args.older_than)
    if args.since:
        start = parse_ts(args.since)
    else:
        newest = store.newest(user["id"])
        start = newest + timedelta(seconds=1) if newest else parse_ts(user["created_at"])
    return start, end


def require_write(level: str) -> None:
    if "write" not in level:
        raise SystemExit(
            f"your access token is '{level or 'unknown'}' only, so X will refuse deletes. In the developer console, "
            "set the app's permissions to Read and write, then regenerate the access token and secret."
        )


def print_summary(store: Store, user_id: str) -> None:
    rows = store.summary(user_id)
    if not rows:
        print("archive is empty")
        return
    print(f"{'type':<10}{'status':<12}{'posts':>8}")
    for kind, status, n in rows:
        print(f"{kind:<10}{status:<12}{n:>8}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="xprune", description=__doc__)
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--db", default=os.environ.get("XPRUNE_DB", "xprune.db"),
                    help="SQLite file path or postgres:// URL (default: $XPRUNE_DB or ./xprune.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def windowed(p):
        p.add_argument("--older-than", type=int, default=90, metavar="DAYS", help="age cutoff in days (default 90)")
        p.add_argument("--since", help="start of the window (ISO time). Default: newest archived post, or account creation")

    windowed(sub.add_parser("count", help="count posts in the window and estimate the cost"))
    p = sub.add_parser("archive", help="save posts in the window to the database (no deletes)")
    windowed(p)
    p.add_argument("--raw-dir", type=Path, help="also save raw API pages here")
    p = sub.add_parser("delete", help="delete posts that are already archived")
    p.add_argument("--limit", type=int, help="delete at most N posts (try 1 first)")
    p.add_argument("--retry-failed", action="store_true", help="put failed rows back in the queue first")
    p.add_argument("--yes", action="store_true", help="required: confirms you want posts deleted from X")
    p = sub.add_parser("run", help="archive the window, then delete it (dry run unless --yes)")
    windowed(p)
    p.add_argument("--raw-dir", type=Path)
    p.add_argument("--yes", action="store_true", help="actually delete; without it, run only archives")
    sub.add_parser("status", help="show archived posts by type and delete status")
    args = ap.parse_args(argv)

    client = Client.from_env()
    store = Store(args.db)
    user, level = client.me()
    print(f"@{user['username']} ({level or 'access level unknown'})", file=sys.stderr)

    if args.cmd == "status":
        print_summary(store, user["id"])
        return

    if args.cmd == "count":
        start, end = window(args, store, user)
        if start >= end:
            print("nothing in the window")
            return
        n = count(client, user["username"], start, end)
        print(f"{n} posts from {iso(start)} to {iso(end)}")
        print(f"estimated cost: about ${n * PRICE_READ:.2f} to archive and ${n * PRICE_DELETE:.2f} to delete "
              f"(prices as of October 2026; check the X developer console). This count cost about "
              f"${PRICE_COUNT_REQUEST * max(1, (end - start).days // 30 + 1):.2f}.")
        return

    lock = lock_or_exit(Path(os.environ.get("XPRUNE_LOCK", Path.home() / ".xprune.lock")))

    if args.cmd in ("archive", "run"):
        start, end = window(args, store, user)
        if start >= end:
            print("no posts have crossed the cutoff since the last run")
        else:
            n = archive(client, store, user["username"], start, end, args.raw_dir)
            print(f"archived {n} posts from {iso(start)} to {iso(end)}")
        if args.cmd == "archive" or not args.yes:
            if args.cmd == "run":
                print("dry run: nothing deleted. Re-run with --yes to delete what is archived.")
            print_summary(store, user["id"])
            return

    if args.cmd == "delete" and not args.yes:
        raise SystemExit("delete removes posts from X permanently; add --yes to confirm")
    require_write(level)
    if getattr(args, "retry_failed", False):
        store.retry_failed(user["id"])
    counts = delete_pending(client, store, user["id"], limit=getattr(args, "limit", None))
    print(f"deleted {counts['deleted']}, not found {counts['not_found']}, failed {counts['failed']}")
    if counts.get("stopped_on_auth_error"):
        sys.exit(2)
    if counts["failed"]:
        sys.exit(1)
    del lock


if __name__ == "__main__":
    main()
