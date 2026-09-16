"""Tests for config.py — env loading, required-field errors, defaults."""

from __future__ import annotations

import importlib

import pytest


def _reload_config(monkeypatch, env: dict):
    """Reload config.py with a controlled environment."""
    for k in (
        "API_ID", "API_HASH", "OUTPUT_CHANNEL", "MANUAL_REVIEW",
        "REVIEW_CHANNEL", "SESSION_NAME", "OPENAI_API_KEY", "OPENAI_MODEL",
        "TELEGRAM_POLL_INTERVAL", "RSS_POLL_INTERVAL", "MESSAGES_PER_CHANNEL",
        "DIGEST_HOUR_UTC", "DIGEST_MINUTE_UTC", "DB_PATH",
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import config
    importlib.reload(config)
    return config


def test_telegram_config_defaults(monkeypatch):
    cfg = _reload_config(monkeypatch, {}).telegram()
    assert cfg.api_id == 0
    assert cfg.api_hash == ""
    assert cfg.session_name == "aggregator"
    assert cfg.output_channel == ""
    assert cfg.manual_review is True
    assert cfg.review_channel == ""
    assert cfg.poll_interval == 60
    assert cfg.messages_per_channel == 5


def test_telegram_config_parses_env(monkeypatch):
    cfg = _reload_config(monkeypatch, {
        "API_ID": "12345",
        "API_HASH": "hash",
        "OUTPUT_CHANNEL": "@out",
        "MANUAL_REVIEW": "false",
        "REVIEW_CHANNEL": "@review",
        "TELEGRAM_POLL_INTERVAL": "30",
        "MESSAGES_PER_CHANNEL": "10",
    }).telegram()
    assert cfg.api_id == 12345
    assert cfg.api_hash == "hash"
    assert cfg.output_channel == "@out"
    assert cfg.manual_review is False
    assert cfg.review_channel == "@review"
    assert cfg.poll_interval == 30
    assert cfg.messages_per_channel == 10


def test_manual_review_accepts_truthy_strings(monkeypatch):
    cfg = _reload_config(monkeypatch, {"MANUAL_REVIEW": "YeS"}).telegram()
    assert cfg.manual_review is True


def test_rss_config_default(monkeypatch):
    cfg = _reload_config(monkeypatch, {}).rss()
    assert cfg.poll_interval == 300


def test_rss_config_parses_env(monkeypatch):
    cfg = _reload_config(monkeypatch, {"RSS_POLL_INTERVAL": "120"}).rss()
    assert cfg.poll_interval == 120


def test_openai_config_defaults(monkeypatch):
    cfg = _reload_config(monkeypatch, {}).openai_cfg()
    assert cfg.api_key == ""
    assert cfg.model == "gpt-4o-mini"


def test_openai_config_parses_env(monkeypatch):
    cfg = _reload_config(monkeypatch, {
        "OPENAI_API_KEY": "sk-test",
        "OPENAI_MODEL": "gpt-4o",
    }).openai_cfg()
    assert cfg.api_key == "sk-test"
    assert cfg.model == "gpt-4o"


def test_digest_config_defaults(monkeypatch):
    cfg = _reload_config(monkeypatch, {}).digest()
    assert cfg.hour_utc == 9
    assert cfg.minute_utc == 0


def test_digest_config_parses_env(monkeypatch):
    cfg = _reload_config(monkeypatch, {
        "DIGEST_HOUR_UTC": "7",
        "DIGEST_MINUTE_UTC": "30",
    }).digest()
    assert cfg.hour_utc == 7
    assert cfg.minute_utc == 30


def test_require_secrets_passes_when_set(monkeypatch):
    cfg = _reload_config(monkeypatch, {
        "API_ID": "1",
        "API_HASH": "h",
        "OUTPUT_CHANNEL": "@out",
        "MANUAL_REVIEW": "false",
    })
    cfg.require_secrets()  # no exception


def test_require_secrets_raises_when_api_id_missing(monkeypatch):
    cfg = _reload_config(monkeypatch, {
        "API_HASH": "h",
        "OUTPUT_CHANNEL": "@out",
        "MANUAL_REVIEW": "false",
    })
    with pytest.raises(RuntimeError, match="API_ID"):
        cfg.require_secrets()


def test_require_secrets_raises_when_api_hash_missing(monkeypatch):
    cfg = _reload_config(monkeypatch, {
        "API_ID": "1",
        "OUTPUT_CHANNEL": "@out",
        "MANUAL_REVIEW": "false",
    })
    with pytest.raises(RuntimeError, match="API_HASH"):
        cfg.require_secrets()


def test_require_secrets_raises_when_output_channel_missing(monkeypatch):
    cfg = _reload_config(monkeypatch, {
        "API_ID": "1",
        "API_HASH": "h",
        "MANUAL_REVIEW": "false",
    })
    with pytest.raises(RuntimeError, match="OUTPUT_CHANNEL"):
        cfg.require_secrets()


def test_require_secrets_raises_when_manual_review_needs_channel(monkeypatch):
    cfg = _reload_config(monkeypatch, {
        "API_ID": "1",
        "API_HASH": "h",
        "OUTPUT_CHANNEL": "@out",
        "MANUAL_REVIEW": "true",
    })
    with pytest.raises(RuntimeError, match="REVIEW_CHANNEL"):
        cfg.require_secrets()


def test_require_secrets_skips_review_check_when_disabled(monkeypatch):
    cfg = _reload_config(monkeypatch, {
        "API_ID": "1",
        "API_HASH": "h",
        "OUTPUT_CHANNEL": "@out",
        "MANUAL_REVIEW": "false",
    })
    cfg.require_secrets()  # no exception even with no REVIEW_CHANNEL


def test_require_openai_raises_when_missing(monkeypatch):
    cfg = _reload_config(monkeypatch, {})
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        cfg.require_openai()


def test_require_openai_passes_when_set(monkeypatch):
    cfg = _reload_config(monkeypatch, {"OPENAI_API_KEY": "sk-x"})
    cfg.require_openai()


def test_db_path_default(monkeypatch):
    cfg = _reload_config(monkeypatch, {})
    assert cfg.DB_PATH.endswith("aggregator.db")


def test_db_path_overridden_by_env(monkeypatch):
    cfg = _reload_config(monkeypatch, {"DB_PATH": "/tmp/custom.db"})
    assert cfg.DB_PATH == "/tmp/custom.db"
