"""
SQLite layer shared by the bot and the Streamlit dashboard.

Provides:
- schema initialisation (idempotent)
- WAL mode + busy_timeout so bot and dashboard can hit the same DB safely
- helpers for seen-posts, sources, RSS feeds, and the posted_log audit table
- seeding of default sources on first run
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    post_id   TEXT PRIMARY KEY,
    source    TEXT,
    timestamp TEXT
);

CREATE TABLE IF NOT EXISTS review_queue (
    post_id   TEXT PRIMARY KEY,
    source    TEXT,
    text      TEXT,
    timestamp TEXT,
    approved  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS telegram_sources (
    channel   TEXT PRIMARY KEY,
    added_at  TEXT NOT NULL,
    enabled   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS rss_feeds (
    url       TEXT PRIMARY KEY,
    added_at  TEXT NOT NULL,
    enabled   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS posted_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id   TEXT NOT NULL,
    source    TEXT NOT NULL,
    text      TEXT NOT NULL,
    sent_at   TEXT NOT NULL,
    status    TEXT NOT NULL,
    digest_of TEXT
);

CREATE INDEX IF NOT EXISTS idx_posted_log_sent_at ON posted_log(sent_at);
CREATE INDEX IF NOT EXISTS idx_posted_log_status   ON posted_log(status);
CREATE INDEX IF NOT EXISTS idx_posted_log_source   ON posted_log(source);

CREATE TABLE IF NOT EXISTS digest_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    requested_at  TEXT NOT NULL,
    window_start  TEXT NOT NULL,
    window_end    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    started_at    TEXT,
    finished_at   TEXT,
    digest_post_id TEXT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_digest_jobs_status ON digest_jobs(status);
"""

# Default source lists — seeded into the DB on first run only.
# Kept here so the bot and dashboard agree on what "default" means.
DEFAULT_TELEGRAM_SOURCES = [
    "osintdefender",
    "war_monitor",
    "intelslava",
    "aurora_intel",
]

DEFAULT_RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/topNews",
    "https://rss.ap.org/rss/apf-topnews",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection with safe defaults for concurrent access.

    Sets `check_same_thread=False` so the same connection can be reused
    across APScheduler jobs and Streamlit reruns without immediate errors.
    WAL + busy_timeout handle cross-process contention.
    """
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    """Create tables and seed defaults if empty."""
    Path(db_path or config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _seed_defaults(conn)


def _seed_defaults(conn: sqlite3.Connection) -> None:
    now = _now_iso()
    cur = conn.execute("SELECT COUNT(*) AS n FROM telegram_sources")
    if cur.fetchone()["n"] == 0:
        conn.executemany(
            "INSERT OR IGNORE INTO telegram_sources (channel, added_at, enabled) "
            "VALUES (?, ?, 1)",
            [(ch, now) for ch in DEFAULT_TELEGRAM_SOURCES],
        )
    cur = conn.execute("SELECT COUNT(*) AS n FROM rss_feeds")
    if cur.fetchone()["n"] == 0:
        conn.executemany(
            "INSERT OR IGNORE INTO rss_feeds (url, added_at, enabled) VALUES (?, ?, 1)",
            [(url, now) for url in DEFAULT_RSS_FEEDS],
        )


# ── seen-posts helpers ──────────────────────────────────────────────

def is_new(post_id: str, source: str = "") -> bool:
    """Return True if `post_id` has not been seen; record it either way."""
    with connect() as conn:
        cur = conn.execute(
            "SELECT 1 FROM seen WHERE post_id = ?", (post_id,)
        )
        if cur.fetchone() is not None:
            return False
        conn.execute(
            "INSERT INTO seen (post_id, source, timestamp) VALUES (?, ?, ?)",
            (post_id, source, _now_iso()),
        )
        return True


# ── source helpers ──────────────────────────────────────────────────

def list_telegram_sources(enabled_only: bool = False) -> list[str]:
    with connect() as conn:
        if enabled_only:
            rows = conn.execute(
                "SELECT channel FROM telegram_sources "
                "WHERE enabled = 1 ORDER BY added_at"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT channel FROM telegram_sources ORDER BY added_at"
            ).fetchall()
        return [r["channel"] for r in rows]


def add_telegram_source(channel: str) -> bool:
    channel = channel.strip().lstrip("@").lower()
    if not channel:
        return False
    with connect() as conn:
        cur = conn.execute(
            "SELECT 1 FROM telegram_sources WHERE channel = ?", (channel,)
        )
        if cur.fetchone() is not None:
            return False
        conn.execute(
            "INSERT INTO telegram_sources (channel, added_at, enabled) "
            "VALUES (?, ?, 1)",
            (channel, _now_iso()),
        )
        return True


def set_telegram_source_enabled(channel: str, enabled: bool) -> None:
    channel = channel.strip().lstrip("@").lower()
    with connect() as conn:
        conn.execute(
            "UPDATE telegram_sources SET enabled = ? WHERE channel = ?",
            (1 if enabled else 0, channel),
        )


def delete_telegram_source(channel: str) -> None:
    channel = channel.strip().lstrip("@").lower()
    with connect() as conn:
        conn.execute("DELETE FROM telegram_sources WHERE channel = ?", (channel,))


def list_rss_feeds(enabled_only: bool = False) -> list[str]:
    with connect() as conn:
        if enabled_only:
            rows = conn.execute(
                "SELECT url FROM rss_feeds WHERE enabled = 1 ORDER BY added_at"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT url FROM rss_feeds ORDER BY added_at"
            ).fetchall()
        return [r["url"] for r in rows]


def add_rss_feed(url: str) -> bool:
    url = url.strip()
    if not url:
        return False
    with connect() as conn:
        cur = conn.execute("SELECT 1 FROM rss_feeds WHERE url = ?", (url,))
        if cur.fetchone() is not None:
            return False
        conn.execute(
            "INSERT INTO rss_feeds (url, added_at, enabled) VALUES (?, ?, 1)",
            (url, _now_iso()),
        )
        return True


def set_rss_feed_enabled(url: str, enabled: bool) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE rss_feeds SET enabled = ? WHERE url = ?",
            (1 if enabled else 0, url),
        )


def delete_rss_feed(url: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM rss_feeds WHERE url = ?", (url,))


# ── posted_log helpers ──────────────────────────────────────────────

def log_post(
    post_id: str,
    source: str,
    text: str,
    status: str,
    digest_of: str | None = None,
) -> None:
    """Record a post that was sent (or queued for review, or discarded)."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO posted_log "
            "(post_id, source, text, sent_at, status, digest_of) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (post_id, source, text, _now_iso(), status, digest_of),
        )


def list_posts(
    *,
    since: str | None = None,
    until: str | None = None,
    source: str | None = None,
    status: str | None = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    """Query posted_log with optional filters. Newest first."""
    clauses: list[str] = []
    params: list = []
    if since:
        clauses.append("sent_at >= ?")
        params.append(since)
    if until:
        clauses.append("sent_at <= ?")
        params.append(until)
    if source:
        clauses.append("source = ?")
        params.append(source)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM posted_log {where} "
            f"ORDER BY sent_at DESC LIMIT ?",
            params,
        ).fetchall()
        return rows


def posts_for_window(start_iso: str, end_iso: str) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM posted_log "
            "WHERE sent_at >= ? AND sent_at < ? "
            "AND status IN ('sent','review') "
            "ORDER BY sent_at ASC",
            (start_iso, end_iso),
        ).fetchall()


def distinct_sources() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT source FROM posted_log ORDER BY source"
        ).fetchall()
        return [r["source"] for r in rows]


def posts_per_day(days: int = 30) -> list[sqlite3.Row]:
    """Daily post counts for the last N days, including zero days."""
    from datetime import timedelta
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days - 1)
    with connect() as conn:
        rows = conn.execute(
            "SELECT substr(sent_at, 1, 10) AS day, COUNT(*) AS n "
            "FROM posted_log "
            "WHERE sent_at >= ? "
            "GROUP BY day ORDER BY day",
            (start.isoformat(),),
        ).fetchall()
    counts = {r["day"]: r["n"] for r in rows}
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        out.append({"day": d, "n": counts.get(d, 0)})
    return out


def posts_per_source() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT source, COUNT(*) AS n FROM posted_log "
            "GROUP BY source ORDER BY n DESC"
        ).fetchall()


# ── digest_jobs helpers ─────────────────────────────────────────────

def enqueue_digest_job(window_start: str, window_end: str) -> int:
    """Insert a pending digest job and return its id."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO digest_jobs (requested_at, window_start, window_end, status) "
            "VALUES (?, ?, ?, 'pending')",
            (_now_iso(), window_start, window_end),
        )
        return cur.lastrowid


def list_digest_jobs(limit: int = 25) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM digest_jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
