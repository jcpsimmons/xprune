"""Archive storage: SQLite by default, Postgres when given a postgres:// URL."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

TABLE = "xprune_posts"

SQLITE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    post_id TEXT PRIMARY KEY,
    author_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    post_type TEXT NOT NULL CHECK (post_type IN ('original','reply','quote','repost')),
    text TEXT NOT NULL,
    conversation_id TEXT,
    in_reply_to_user_id TEXT,
    referenced_post_id TEXT,
    impression_count INTEGER,
    like_count INTEGER,
    repost_count INTEGER,
    reply_count INTEGER,
    quote_count INTEGER,
    bookmark_count INTEGER,
    media TEXT NOT NULL DEFAULT '[]',
    referenced_posts TEXT NOT NULL DEFAULT '[]',
    payload TEXT NOT NULL,
    archived_at TEXT NOT NULL,
    delete_status TEXT NOT NULL DEFAULT 'pending' CHECK (delete_status IN ('pending','deleted','not_found','failed')),
    delete_error TEXT,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS {TABLE}_author_created_idx ON {TABLE} (author_id, created_at);
CREATE INDEX IF NOT EXISTS {TABLE}_status_idx ON {TABLE} (delete_status);
"""

POSTGRES_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    post_id text PRIMARY KEY,
    author_id text NOT NULL,
    created_at timestamptz NOT NULL,
    post_type text NOT NULL CHECK (post_type IN ('original','reply','quote','repost')),
    text text NOT NULL,
    conversation_id text,
    in_reply_to_user_id text,
    referenced_post_id text,
    impression_count bigint,
    like_count bigint,
    repost_count bigint,
    reply_count bigint,
    quote_count bigint,
    bookmark_count bigint,
    media jsonb NOT NULL DEFAULT '[]'::jsonb,
    referenced_posts jsonb NOT NULL DEFAULT '[]'::jsonb,
    payload jsonb NOT NULL,
    archived_at timestamptz NOT NULL,
    delete_status text NOT NULL DEFAULT 'pending' CHECK (delete_status IN ('pending','deleted','not_found','failed')),
    delete_error text,
    deleted_at timestamptz
);
CREATE INDEX IF NOT EXISTS {TABLE}_author_created_idx ON {TABLE} (author_id, created_at);
CREATE INDEX IF NOT EXISTS {TABLE}_status_idx ON {TABLE} (delete_status);
"""

COLUMNS = ("post_id", "author_id", "created_at", "post_type", "text", "conversation_id", "in_reply_to_user_id",
           "referenced_post_id", "impression_count", "like_count", "repost_count", "reply_count", "quote_count",
           "bookmark_count", "media", "referenced_posts", "payload", "archived_at")
# ?t = timestamp, ?j = JSON. Plain ? on SQLite; cast placeholders on Postgres.
PLACEHOLDERS = ("?", "?", "?t", "?", "?", "?", "?", "?", "?", "?", "?", "?", "?", "?", "?j", "?j", "?j", "?t")
REFRESHED = ("impression_count", "like_count", "repost_count", "reply_count", "quote_count", "bookmark_count",
             "media", "referenced_posts", "payload", "archived_at")

UPSERT = (
    f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({', '.join(PLACEHOLDERS)}) "
    f"ON CONFLICT (post_id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in REFRESHED)} "
    f"WHERE {TABLE}.delete_status = 'pending'"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class Store:
    def __init__(self, url: str):
        self.url = url
        self.pg = url.startswith(("postgres://", "postgresql://"))
        if self.pg:
            try:
                import psycopg
            except ImportError as exc:
                raise SystemExit("Postgres storage needs psycopg: pip install 'xprune[postgres]'") from exc
            self._psycopg = psycopg
            self._errors: tuple = (psycopg.OperationalError,)
        else:
            self.path = url[len("sqlite:///"):] if url.startswith("sqlite:///") else url
            self._errors = (sqlite3.OperationalError,)
        with self._conn() as conn:
            if self.pg:
                conn.execute(POSTGRES_DDL)
            else:
                conn.executescript(SQLITE_DDL)

    @contextmanager
    def _conn(self):
        # A fresh connection for each unit of work. Delete runs sleep through 15-minute rate-limit windows,
        # and hosted Postgres (Neon, Supabase, RDS proxies) drops idle connections in that time.
        conn = self._psycopg.connect(self.url, connect_timeout=30) if self.pg else sqlite3.connect(self.path, timeout=30)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _sql(self, sql: str) -> str:
        if self.pg:
            return sql.replace("?j", "%s::jsonb").replace("?t", "%s::timestamptz").replace("?", "%s")
        return sql.replace("?j", "?").replace("?t", "?")

    def _run(self, sql: str, params=(), *, many: bool = False, fetch: bool = False):
        for attempt in range(5):
            try:
                with self._conn() as conn:
                    cur = conn.cursor()
                    if many:
                        cur.executemany(self._sql(sql), params)
                    else:
                        cur.execute(self._sql(sql), params)
                    return cur.fetchall() if fetch else None
            except self._errors:
                if attempt == 4:
                    raise
                time.sleep(3 * (attempt + 1))
        return None

    def upsert(self, rows: list[tuple]) -> None:
        """Insert archived posts. Rows already deleted or failed are never overwritten."""
        if rows:
            self._run(UPSERT, rows, many=True)

    def pending(self, author_id: str) -> list[tuple[str, str, str | None]]:
        return self._run(
            f"SELECT post_id, post_type, referenced_post_id FROM {TABLE} "
            "WHERE delete_status = 'pending' AND author_id = ? ORDER BY created_at",
            (author_id,), fetch=True,
        )

    def mark(self, post_id: str, status: str, error: str | None = None) -> None:
        deleted_at = now_iso() if status in ("deleted", "not_found") else None
        self._run(f"UPDATE {TABLE} SET delete_status = ?, delete_error = ?, deleted_at = ?t WHERE post_id = ?",
                  (status, error, deleted_at, post_id))

    def retry_failed(self, author_id: str) -> None:
        self._run(f"UPDATE {TABLE} SET delete_status = 'pending', delete_error = NULL "
                  "WHERE delete_status = 'failed' AND author_id = ?", (author_id,))

    def newest(self, author_id: str) -> datetime | None:
        rows = self._run(f"SELECT max(created_at) FROM {TABLE} WHERE author_id = ?", (author_id,), fetch=True)
        return parse_ts(rows[0][0]) if rows else None

    def summary(self, author_id: str) -> list[tuple[str, str, int]]:
        return self._run(
            f"SELECT post_type, delete_status, count(*) FROM {TABLE} WHERE author_id = ? "
            "GROUP BY post_type, delete_status ORDER BY post_type, delete_status",
            (author_id,), fetch=True,
        )
