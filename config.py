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


@dataclass(frozen=True)
class RSSConfig:
    poll_interval: int


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str
    model: str


@dataclass(frozen=True)
class DigestConfig:
    hour_utc: int
    minute_utc: int


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
    )


def rss() -> RSSConfig:
    return RSSConfig(
        poll_interval=_int("RSS_POLL_INTERVAL", 300),
    )


def openai_cfg() -> OpenAIConfig:
    return OpenAIConfig(
        api_key=os.getenv("OPENAI_API_KEY", ""),
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    )


def digest() -> DigestConfig:
    return DigestConfig(
        hour_utc=_int("DIGEST_HOUR_UTC", 9),
        minute_utc=_int("DIGEST_MINUTE_UTC", 0),
    )


def require_secrets() -> None:
    """Call before starting the bot. Raises if anything mandatory is missing."""
    _required("API_ID")
    _required("API_HASH")
    if _bool("MANUAL_REVIEW", True):
        _required("REVIEW_CHANNEL")
    _required("OUTPUT_CHANNEL")


def require_openai() -> None:
    _required("OPENAI_API_KEY")
