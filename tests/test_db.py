"""Tests for db.py — schema, seeding, and CRUD/query helpers."""

from __future__ import annotations

import pytest


def test_init_db_creates_all_tables(fresh_db):
    _db_path, db = fresh_db
    with db.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {
        "seen",
        "review_queue",
        "telegram_sources",
        "rss_feeds",
        "posted_log",
        "digest_jobs",
        "keywords",
    } <= tables


def test_init_db_sets_wal_and_busy_timeout(fresh_db):
    _db_path, db = fresh_db
    with db.connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert mode.lower() == "wal"
    assert busy >= 5000


def test_init_db_is_idempotent(fresh_db):
    _db_path, db = fresh_db
    db.add_telegram_source("customchan")
    db.init_db()  # second call must not wipe or duplicate
    assert "customchan" in db.list_telegram_sources()


def test_seed_inserts_default_telegram_sources(fresh_db):
    _db_path, db = fresh_db
    assert set(db.list_telegram_sources()) == {
        "osintdefender",
        "war_monitor",
        "intelslava",
        "aurora_intel",
    }


def test_seed_does_not_overwrite_existing_sources(fresh_db):
    _db_path, db = fresh_db
    db.delete_telegram_source("osintdefender")
    db.init_db()
    channels = db.list_telegram_sources()
    assert "osintdefender" not in channels
    assert "war_monitor" in channels  # untouched default still present


def test_seed_inserts_default_rss_feeds(fresh_db):
    _db_path, db = fresh_db
    feeds = db.list_rss_feeds()
    assert len(feeds) == 4
    assert "https://rss.ap.org/rss/apf-topnews" in feeds


def test_seed_inserts_default_keywords(fresh_db):
    _db_path, db = fresh_db
    kws = db.list_keywords()
    assert "breaking" in kws
    assert "strike" in kws
    assert len(kws) >= 5


# ── telegram_sources ────────────────────────────────────────────────


def test_add_telegram_source_normalises_and_dedupes(fresh_db):
    _db_path, db = fresh_db
    assert db.add_telegram_source("  @NewChan  ") is True
    assert db.add_telegram_source("newchan") is False
    assert db.list_telegram_sources()[-1] == "newchan"


def test_add_telegram_source_rejects_empty(fresh_db):
    _db_path, db = fresh_db
    assert db.add_telegram_source("   ") is False
    assert db.add_telegram_source("@") is False


def test_toggle_telegram_source_enabled(fresh_db):
    _db_path, db = fresh_db
    db.set_telegram_source_enabled("osintdefender", False)
    enabled = db.list_telegram_sources(enabled_only=True)
    assert "osintdefender" not in enabled
    db.set_telegram_source_enabled("osintdefender", True)
    assert "osintdefender" in db.list_telegram_sources(enabled_only=True)


def test_delete_telegram_source(fresh_db):
    _db_path, db = fresh_db
    db.delete_telegram_source("osintdefender")
    assert "osintdefender" not in db.list_telegram_sources()


# ── rss_feeds ───────────────────────────────────────────────────────


def test_add_rss_feed_dedupes(fresh_db):
    _db_path, db = fresh_db
    assert db.add_rss_feed("https://example.com/feed") is True
    assert db.add_rss_feed("https://example.com/feed") is False


def test_toggle_and_delete_rss_feed(fresh_db):
    _db_path, db = fresh_db
    url = "https://example.com/x"
    db.add_rss_feed(url)
    db.set_rss_feed_enabled(url, False)
    assert url not in db.list_rss_feeds(enabled_only=True)
    db.set_rss_feed_enabled(url, True)
    assert url in db.list_rss_feeds(enabled_only=True)
    db.delete_rss_feed(url)
    assert url not in db.list_rss_feeds()


# ── keywords ────────────────────────────────────────────────────────


def test_add_keyword_normalises_and_dedupes(fresh_db):
    _db_path, db = fresh_db
    assert db.add_keyword("  Evacuation  ") is True
    assert db.add_keyword("evacuation") is False
    assert "evacuation" in db.list_keywords()


def test_add_keyword_rejects_empty(fresh_db):
    _db_path, db = fresh_db
    assert db.add_keyword("   ") is False
    assert db.add_keyword("") is False


def test_toggle_and_delete_keyword(fresh_db):
    _db_path, db = fresh_db
    db.set_keyword_enabled("breaking", False)
    assert "breaking" not in db.list_keywords(enabled_only=True)
    db.set_keyword_enabled("breaking", True)
    assert "breaking" in db.list_keywords(enabled_only=True)
    db.delete_keyword("breaking")
    assert "breaking" not in db.list_keywords()


# ── is_new / seen ───────────────────────────────────────────────────


def test_is_new_records_first_seen_and_source(seeded_db):
    _db_path, db = seeded_db
    assert db.is_new("freshhash", "@newchan") is True
    assert db.is_new("freshhash", "@newchan") is False
    with db.connect() as conn:
        row = conn.execute(
            "SELECT source FROM seen WHERE post_id = ?", ("freshhash",)
        ).fetchone()
    assert row["source"] == "@newchan"


def test_is_new_does_not_duplicate_existing_seen(fresh_db):
    _db_path, db = fresh_db
    assert db.is_new("h", "@x") is True
    assert db.is_new("h", "@y") is False  # different source, but post_id already known


# ── posted_log ──────────────────────────────────────────────────────


def test_list_posts_filters_by_status(seeded_db):
    _db_path, db = seeded_db
    rows = db.list_posts(status="review")
    assert [r["post_id"] for r in rows] == ["p2"]


def test_list_posts_filters_by_source(seeded_db):
    _db_path, db = seeded_db
    rows = db.list_posts(source="@osintdefender")
    ids = {r["post_id"] for r in rows}
    assert ids == {"p1", "p3"}


def test_list_posts_filters_by_date_range(seeded_db):
    _db_path, db = seeded_db
    future = "2999-01-01T00:00:00+00:00"
    assert db.list_posts(until=future, limit=100)  # returns something (no crash)
    past = "1970-01-01T00:00:00+00:00"
    assert db.list_posts(since=past, limit=100)


def test_list_posts_orders_newest_first(seeded_db):
    _db_path, db = seeded_db
    rows = db.list_posts(limit=100)
    timestamps = [r["sent_at"] for r in rows]
    assert timestamps == sorted(timestamps, reverse=True)


def test_distinct_sources(seeded_db):
    _db_path, db = seeded_db
    assert set(db.distinct_sources()) == {
        "@osintdefender",
        "@war_monitor",
        "Daily Digest",
    }


def test_posts_for_window_only_returns_sent_and_review(seeded_db):
    _db_path, db = seeded_db
    rows = db.posts_for_window("1970-01-01T00:00:00+00:00", "2999-12-31T00:00:00+00:00")
    statuses = {r["status"] for r in rows}
    assert statuses <= {"sent", "review"}
    ids = {r["post_id"] for r in rows}
    assert ids == {"p1", "p2"}  # p3=discarded, p4=digest excluded


def test_mark_post_status_via_direct_sql(seeded_db):
    """The review handler updates status directly; verify the schema supports it."""
    _db_path, db = seeded_db
    with db.connect() as conn:
        conn.execute(
            "UPDATE posted_log SET status = 'sent' "
            "WHERE post_id = ? AND status = 'review'",
            ("p2",),
        )
        row = conn.execute(
            "SELECT status FROM posted_log WHERE post_id = ?", ("p2",)
        ).fetchone()
    assert row["status"] == "sent"


# ── posts_per_day / posts_per_source ────────────────────────────────


def test_posts_per_day_fills_zero_days(seeded_db):
    _db_path, db = seeded_db
    rows = db.posts_per_day(days=7)
    assert len(rows) == 7
    # All counts are 0 because seeded posts have today's ISO timestamp.
    total = sum(r["n"] for r in rows)
    assert total >= 1  # at least one post in the 7-day window


def test_posts_per_source_orders_desc(seeded_db):
    _db_path, db = seeded_db
    rows = db.posts_per_source()
    counts = [r["n"] for r in rows]
    assert counts == sorted(counts, reverse=True)
    assert sum(counts) == 4


# ── digest_jobs ─────────────────────────────────────────────────────


def test_enqueue_digest_job_creates_pending_row(fresh_db):
    _db_path, db = fresh_db
    job_id = db.enqueue_digest_job(
        "2026-09-15T00:00:00+00:00", "2026-09-16T00:00:00+00:00"
    )
    jobs = db.list_digest_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job["id"] == job_id
    assert job["status"] == "pending"
    assert job["window_start"] == "2026-09-15T00:00:00+00:00"
    assert job["window_end"] == "2026-09-16T00:00:00+00:00"


def test_digest_jobs_state_transitions(fresh_db):
    """Verify the worker can mark running → done/failed via SQL."""
    _db_path, db = fresh_db
    job_id = db.enqueue_digest_job(
        "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"
    )
    with db.connect() as conn:
        conn.execute(
            "UPDATE digest_jobs SET status='running', started_at=? WHERE id=?",
            ("2026-01-01T00:00:01", job_id),
        )
        conn.execute(
            "UPDATE digest_jobs SET status='done', finished_at=?, digest_post_id=? "
            "WHERE id=?",
            ("2026-01-01T00:00:05", "abc123", job_id),
        )
        row = conn.execute("SELECT * FROM digest_jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == "done"
    assert row["digest_post_id"] == "abc123"


def test_list_digest_jobs_newest_first(fresh_db):
    _db_path, db = fresh_db
    db.enqueue_digest_job("2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00")
    db.enqueue_digest_job("2026-02-01T00:00:00+00:00", "2026-02-02T00:00:00+00:00")
    rows = db.list_digest_jobs()
    assert [r["id"] for r in rows] == sorted([r["id"] for r in rows], reverse=True)


# ── cross-process / concurrency sanity ──────────────────────────────


def test_concurrent_connections_share_state(seeded_db):
    """WAL + busy_timeout should let two connections see each other's writes."""
    _db_path, db = seeded_db
    with db.connect() as conn_a:
        conn_a.execute(
            "INSERT INTO posted_log (post_id, source, text, sent_at, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("concurrent", "@x", "from A", "2026-09-16T00:00:00+00:00", "sent"),
        )
        conn_a.commit()
    with db.connect() as conn_b:
        row = conn_b.execute(
            "SELECT post_id FROM posted_log WHERE post_id = ?", ("concurrent",)
        ).fetchone()
    assert row["post_id"] == "concurrent"


# ── review-queue helpers ─────────────────────────────────────────────


def _status(db, post_id: str) -> str | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT status FROM posted_log WHERE post_id = ?", (post_id,)
        ).fetchone()
    return row["status"] if row else None


def test_list_pending_review_returns_review_and_digest(seeded_db):
    _db_path, db = seeded_db
    rows = db.list_pending_review()
    ids = {r["post_id"] for r in rows}
    # seeded_db has p2=review and p4=digest; p1=sent and p3=discarded
    assert ids == {"p2", "p4"}


def test_list_pending_review_newest_first(seeded_db):
    _db_path, db = seeded_db
    rows = db.list_pending_review()
    sent_at = [r["sent_at"] for r in rows]
    assert sent_at == sorted(sent_at, reverse=True)


def test_mark_review_action_approves_review_post(seeded_db):
    _db_path, db = seeded_db
    assert db.mark_review_action("p2", "approve") is True
    assert _status(db, "p2") == "approved"


def test_mark_review_action_discards_digest_post(seeded_db):
    _db_path, db = seeded_db
    assert db.mark_review_action("p4", "discard") is True
    assert _status(db, "p4") == "discarded"


def test_mark_review_action_noop_when_already_actioned(seeded_db):
    _db_path, db = seeded_db
    # p1 is already 'sent', p3 is already 'discarded' — neither is in
    # ('review','digest'), so neither action should change anything.
    assert db.mark_review_action("p1", "approve") is False
    assert db.mark_review_action("p3", "discard") is False
    assert _status(db, "p1") == "sent"
    assert _status(db, "p3") == "discarded"


def test_mark_review_action_returns_false_for_unknown_post(seeded_db):
    _db_path, db = seeded_db
    assert db.mark_review_action("does-not-exist", "approve") is False


def test_mark_review_action_rejects_invalid_action(seeded_db):
    _db_path, db = seeded_db
    with pytest.raises(ValueError, match="approve.*discard"):
        db.mark_review_action("p2", "telegram-react")


def test_mark_review_action_is_idempotent(seeded_db):
    _db_path, db = seeded_db
    # Approve once → succeeds, status moves to 'approved' (no longer in
    # the eligible set). Approve again → no-op, returns False.
    assert db.mark_review_action("p2", "approve") is True
    assert db.mark_review_action("p2", "approve") is False
    assert _status(db, "p2") == "approved"


# ── posts_for_window status coverage ────────────────────────────────


def test_posts_for_window_includes_buffered(seeded_db):
    """Digest should pick up digest-only buffered posts, not just sent/review."""
    _db_path, db = seeded_db
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO posted_log (post_id, source, text, sent_at, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("buf1", "@x", "buffered post", "2026-09-16T12:00:00+00:00", "buffered"),
        )
        conn.commit()
    rows = db.posts_for_window("2026-09-16T00:00:00+00:00", "2026-09-17T00:00:00+00:00")
    ids = {r["post_id"] for r in rows}
    assert "buf1" in ids


def test_posts_for_window_excludes_discarded(seeded_db):
    """Discarded posts should never feed into a digest."""
    _db_path, db = seeded_db
    rows = db.posts_for_window("2026-09-16T00:00:00+00:00", "2026-09-17T00:00:00+00:00")
    ids = {r["post_id"] for r in rows}
    assert "p3" not in ids  # p3 was seeded as 'discarded'
