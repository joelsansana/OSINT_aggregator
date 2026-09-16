"""
Shared pytest fixtures.

`db.py` reads `config.DB_PATH` at module-import time and the `connect()`
helper falls back to that value when no explicit path is given. We patch
`config.DB_PATH` to a per-test tmp file so every test gets a clean DB
without monkey-patching every helper.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def fresh_db(db_path, monkeypatch):
    """
    Yield (db_path, db_module) after initialising an empty database.

    Each test gets a fresh DB. The fixture points config.DB_PATH at the
    tmp file so any helper that uses the default connection target lands
    in the right place.
    """
    import config
    import db

    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db(db_path=db_path)
    return db_path, db


@pytest.fixture
def seeded_db(fresh_db):
    """A fresh DB plus a couple of sources and posts for richer tests."""
    db_path, db = fresh_db
    db.add_telegram_source("osintdefender")
    db.add_telegram_source("war_monitor")
    db.add_rss_feed("https://example.com/feed.xml")

    db.log_post("p1", "@osintdefender", "Breaking: strike on X", "sent")
    db.log_post("p2", "@war_monitor", "Missile launch reported", "review")
    db.log_post("p3", "@osintdefender", "Discarded post", "discarded")
    db.log_post("p4", "Daily Digest", "Summary body", "digest", digest_of="2026-09-16")

    db.is_new("seenhash1", "@osintdefender")
    db.is_new("seenhash2", "@war_monitor")

    return db_path, db
