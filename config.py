"""
Centralised configuration loaded from environment variables / .env.

Both osint_aggregator.py and dashboard.py import from here so there is one
source of truth for credentials, channel names, polling cadence, and the
digest schedule.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required env var {name!r}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = os.getenv("DB_PATH", str(PROJECT_ROOT / "aggregator.db"))


@dataclass(frozen=True)
class TelegramConfig:
    api_id: int
    api_hash: str
    session_name: str
    output_channel: str
    manual_review: bool
    review_channel: str
    poll_interval: int
    messages_per_channel: int
    digest_only: bool


@dataclass(frozen=True)
class RSSConfig:
    poll_interval: int


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: str
    model: str
    base_url: str


# Backwards-compatible alias (the config used to be OpenAI-only).
OpenAIConfig = LLMConfig


# Provider presets: name → (default base URL, default model).
# All three expose OpenAI-compatible Chat Completions endpoints, which the
# `openai` Python SDK talks to via `base_url=`. Override any field with
# LLM_BASE_URL / LLM_MODEL / LLM_API_KEY if needed.
_LLM_PROVIDERS: dict[str, tuple[str, str]] = {
    "openai":  ("https://api.openai.com/v1",             "gpt-4o-mini"),
    "minimax": ("https://api.minimax.io/v1",             "MiniMax-M3"),
    "glm":     ("https://open.bigmodel.cn/api/paas/v4/", "glm-4-flash"),
}


@dataclass(frozen=True)
class DigestConfig:
    hour_utc: int
    minute_utc: int
    interval_hours: int  # 0 → daily cron at hour_utc:minute_utc; >0 → every N hours


def telegram() -> TelegramConfig:
    return TelegramConfig(
        api_id=_int("API_ID", 0),
        api_hash=os.getenv("API_HASH", ""),
        session_name=os.getenv("SESSION_NAME", "aggregator"),
        output_channel=os.getenv("OUTPUT_CHANNEL", ""),
        manual_review=_bool("MANUAL_REVIEW", True),
        review_channel=os.getenv("REVIEW_CHANNEL", ""),
        poll_interval=_int("TELEGRAM_POLL_INTERVAL", 60),
        messages_per_channel=_int("MESSAGES_PER_CHANNEL", 5),
        digest_only=_bool("DIGEST_ONLY", False),
    )


def rss() -> RSSConfig:
    return RSSConfig(
        poll_interval=_int("RSS_POLL_INTERVAL", 300),
    )


def llm() -> LLMConfig:
    """
    LLM provider config for the daily digest.

    `LLM_PROVIDER` selects the provider preset (default: "openai"). Each
    preset supplies a default base URL and model. Override per-call with
    LLM_API_KEY, LLM_MODEL, and LLM_BASE_URL. For backwards compatibility,
    OPENAI_API_KEY and OPENAI_MODEL are still read when the new vars
    are not set.
    """
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    if provider not in _LLM_PROVIDERS:
        raise RuntimeError(
            f"Unknown LLM_PROVIDER {provider!r}. "
            f"Valid options: {', '.join(sorted(_LLM_PROVIDERS))}."
        )
    default_url, default_model = _LLM_PROVIDERS[provider]
    return LLMConfig(
        provider=provider,
        api_key=os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", ""),
        model=os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or default_model,
        base_url=os.getenv("LLM_BASE_URL") or default_url,
    )


# Backwards-compatible alias.
def openai_cfg() -> LLMConfig:
    return llm()


def digest() -> DigestConfig:
    return DigestConfig(
        hour_utc=_int("DIGEST_HOUR_UTC", 9),
        minute_utc=_int("DIGEST_MINUTE_UTC", 0),
        interval_hours=_int("DIGEST_INTERVAL_HOURS", 0),
    )


def require_secrets() -> None:
    """Call before starting the bot. Raises if anything mandatory is missing."""
    _required("API_ID")
    _required("API_HASH")
    if _bool("MANUAL_REVIEW", True):
        _required("REVIEW_CHANNEL")
    _required("OUTPUT_CHANNEL")


def require_llm() -> None:
    """Call before generating a digest. Raises if no API key is set."""
    cfg = llm()
    if not cfg.api_key:
        raise RuntimeError(
            f"API key for LLM provider {cfg.provider!r} not set. "
            "Set LLM_API_KEY (or legacy OPENAI_API_KEY) in .env."
        )


# Backwards-compatible alias.
def require_openai() -> None:
    require_llm()
