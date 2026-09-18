"""Tests for config.py — env loading, required-field errors, defaults."""

from __future__ import annotations

import importlib

import pytest


def _reload_config(monkeypatch, env: dict):
    """Reload config.py with a controlled environment, ignoring any .env file."""
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: None)
    for k in (
        "API_ID",
        "API_HASH",
        "OUTPUT_CHANNEL",
        "MANUAL_REVIEW",
        "REVIEW_CHANNEL",
        "SESSION_NAME",
        "DIGEST_ONLY",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_BASE_URL",
        "TELEGRAM_POLL_INTERVAL",
        "RSS_POLL_INTERVAL",
        "MESSAGES_PER_CHANNEL",
        "DIGEST_HOUR_UTC",
        "DIGEST_MINUTE_UTC",
        "DIGEST_INTERVAL_HOURS",
        "DB_PATH",
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
    assert cfg.digest_only is False


def test_telegram_config_parses_env(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "API_ID": "12345",
            "API_HASH": "hash",
            "OUTPUT_CHANNEL": "@out",
            "MANUAL_REVIEW": "false",
            "REVIEW_CHANNEL": "@review",
            "TELEGRAM_POLL_INTERVAL": "30",
            "MESSAGES_PER_CHANNEL": "10",
            "DIGEST_ONLY": "true",
        },
    ).telegram()
    assert cfg.api_id == 12345
    assert cfg.api_hash == "hash"
    assert cfg.output_channel == "@out"
    assert cfg.manual_review is False
    assert cfg.review_channel == "@review"
    assert cfg.poll_interval == 30
    assert cfg.messages_per_channel == 10
    assert cfg.digest_only is True


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
    assert cfg.provider == "openai"
    assert cfg.api_key == ""
    assert cfg.model == "gpt-4o-mini"
    assert cfg.base_url == "https://api.openai.com/v1"


def test_openai_config_parses_env(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "OPENAI_API_KEY": "sk-test",
            "OPENAI_MODEL": "gpt-4o",
        },
    ).openai_cfg()
    assert cfg.api_key == "sk-test"
    assert cfg.model == "gpt-4o"


def test_llm_defaults_to_openai(monkeypatch):
    cfg = _reload_config(monkeypatch, {}).llm()
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-4o-mini"
    assert cfg.base_url == "https://api.openai.com/v1"


def test_llm_minimax_preset(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "LLM_PROVIDER": "minimax",
            "LLM_API_KEY": "minimax-key",
        },
    ).llm()
    assert cfg.provider == "minimax"
    assert cfg.api_key == "minimax-key"
    assert cfg.model == "MiniMax-M3"
    assert cfg.base_url == "https://api.minimax.io/v1"


def test_llm_glm_preset(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "LLM_PROVIDER": "glm",
            "LLM_API_KEY": "glm-key",
        },
    ).llm()
    assert cfg.provider == "glm"
    assert cfg.api_key == "glm-key"
    assert cfg.model == "glm-4-flash"
    assert cfg.base_url == "https://open.bigmodel.cn/api/paas/v4/"


def test_llm_unknown_provider_raises(monkeypatch):
    cfg = _reload_config(monkeypatch, {"LLM_PROVIDER": "anthropic"})
    with pytest.raises(RuntimeError, match="Unknown LLM_PROVIDER"):
        cfg.llm()


def test_llm_explicit_overrides(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "LLM_PROVIDER": "openai",
            "LLM_BASE_URL": "https://my-proxy.example.com/v1",
            "LLM_MODEL": "gpt-4o",
            "LLM_API_KEY": "sk-x",
        },
    ).llm()
    assert cfg.base_url == "https://my-proxy.example.com/v1"
    assert cfg.model == "gpt-4o"


def test_llm_falls_back_to_legacy_env(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "OPENAI_API_KEY": "sk-legacy",
            "OPENAI_MODEL": "gpt-4",
        },
    ).llm()
    assert cfg.api_key == "sk-legacy"
    assert cfg.model == "gpt-4"


def test_llm_new_env_wins_over_legacy(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "LLM_API_KEY": "sk-new",
            "OPENAI_API_KEY": "sk-legacy",
        },
    ).llm()
    assert cfg.api_key == "sk-new"


def test_require_llm_raises_when_missing(monkeypatch):
    cfg = _reload_config(monkeypatch, {})
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        cfg.require_llm()


def test_require_llm_passes_when_set_via_legacy(monkeypatch):
    cfg = _reload_config(monkeypatch, {"OPENAI_API_KEY": "sk-x"})
    cfg.require_llm()


def test_require_llm_passes_when_set_via_new(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "LLM_PROVIDER": "minimax",
            "LLM_API_KEY": "minimax-key",
        },
    )
    cfg.require_llm()


def test_digest_config_defaults(monkeypatch):
    cfg = _reload_config(monkeypatch, {}).digest()
    assert cfg.hour_utc == 9
    assert cfg.minute_utc == 0
    assert cfg.interval_hours == 0


def test_digest_config_parses_env(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "DIGEST_HOUR_UTC": "7",
            "DIGEST_MINUTE_UTC": "30",
        },
    ).digest()
    assert cfg.hour_utc == 7
    assert cfg.minute_utc == 30


def test_digest_config_interval_hours(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "DIGEST_INTERVAL_HOURS": "1",
        },
    ).digest()
    assert cfg.interval_hours == 1
    # hour_utc / minute_utc still default — they're only used in cron mode.
    assert cfg.hour_utc == 9
    assert cfg.minute_utc == 0


def test_require_secrets_passes_when_set(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "API_ID": "1",
            "API_HASH": "h",
            "OUTPUT_CHANNEL": "@out",
            "MANUAL_REVIEW": "false",
        },
    )
    cfg.require_secrets()  # no exception


def test_require_secrets_raises_when_api_id_missing(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "API_HASH": "h",
            "OUTPUT_CHANNEL": "@out",
            "MANUAL_REVIEW": "false",
        },
    )
    with pytest.raises(RuntimeError, match="API_ID"):
        cfg.require_secrets()


def test_require_secrets_raises_when_api_hash_missing(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "API_ID": "1",
            "OUTPUT_CHANNEL": "@out",
            "MANUAL_REVIEW": "false",
        },
    )
    with pytest.raises(RuntimeError, match="API_HASH"):
        cfg.require_secrets()


def test_require_secrets_raises_when_output_channel_missing(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "API_ID": "1",
            "API_HASH": "h",
            "MANUAL_REVIEW": "false",
        },
    )
    with pytest.raises(RuntimeError, match="OUTPUT_CHANNEL"):
        cfg.require_secrets()


def test_require_secrets_raises_when_manual_review_needs_channel(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "API_ID": "1",
            "API_HASH": "h",
            "OUTPUT_CHANNEL": "@out",
            "MANUAL_REVIEW": "true",
        },
    )
    with pytest.raises(RuntimeError, match="REVIEW_CHANNEL"):
        cfg.require_secrets()


def test_require_secrets_skips_review_check_when_disabled(monkeypatch):
    cfg = _reload_config(
        monkeypatch,
        {
            "API_ID": "1",
            "API_HASH": "h",
            "OUTPUT_CHANNEL": "@out",
            "MANUAL_REVIEW": "false",
        },
    )
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
