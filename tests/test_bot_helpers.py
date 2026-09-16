"""Tests for the pure helpers in osint_aggregator.py."""

from __future__ import annotations

import osint_aggregator as oa


# ── make_id ─────────────────────────────────────────────────────────

def test_make_id_is_stable_and_hex():
    assert oa.make_id("hello") == oa.make_id("hello")
    assert len(oa.make_id("hello")) == 32
    int(oa.make_id("hello"), 16)  # parses as hex


def test_make_id_distinguishes_inputs():
    assert oa.make_id("a") != oa.make_id("b")


# ── is_relevant ─────────────────────────────────────────────────────

def test_is_relevant_matches_keyword_case_insensitive():
    assert oa.is_relevant("BREAKING: strike confirmed") is True
    assert oa.is_relevant("lowercase russia update") is True


def test_is_relevant_rejects_non_matching():
    assert oa.is_relevant("just a normal post about cooking") is False


def test_is_relevant_rejects_empty_and_none():
    assert oa.is_relevant("") is False
    assert oa.is_relevant(None) is False  # type: ignore[arg-type]


# ── format_post ─────────────────────────────────────────────────────

def test_format_post_wraps_with_source_and_timestamp():
    out = oa.format_post("hello", "@osintdefender")
    assert out.startswith("⚡️ hello")
    assert "📡 @osintdefender" in out
    assert "UTC" in out


def test_format_post_strips_leading_whitespace():
    out = oa.format_post("  hello  ", "@x")
    assert out.startswith("⚡️ hello")


def test_format_post_truncates_oversized_body():
    huge = "x" * (oa.POST_BODY_MAX + 500)
    out = oa.format_post(huge, "@x")
    assert len(out) <= oa.TELEGRAM_MAX_LEN
    # Truncation marker present somewhere in the body.
    assert "…" in out
    # Timestamp suffix is intact.
    assert "UTC" in out


# ── format_digest ───────────────────────────────────────────────────

def test_format_digest_includes_day_label():
    out = oa.format_digest("summary body", "2026-09-16")
    assert "📰 Daily Digest · 2026-09-16" in out
    assert "summary body" in out
    assert "Synthesised by OSINT Aggregator" in out


def test_format_digest_truncates_oversized_body():
    huge = "y" * (oa.POST_BODY_MAX + 500)
    out = oa.format_digest(huge, "2026-09-16")
    assert len(out) <= oa.TELEGRAM_MAX_LEN


# ── extract_post_id ─────────────────────────────────────────────────

def test_extract_post_id_finds_marker():
    sample = (
        "📥 REVIEW QUEUE\n"
        "POST_ID:abcdef0123456789abcdef0123456789\n"
        "Source: @x\n──────────────\nbody"
    )
    assert oa.extract_post_id(sample) == "abcdef0123456789abcdef0123456789"


def test_extract_post_id_returns_none_when_absent():
    assert oa.extract_post_id("no marker here") is None
    assert oa.extract_post_id("") is None
    assert oa.extract_post_id(None) is None  # type: ignore[arg-type]


def test_extract_post_id_ignores_invalid_hex():
    assert oa.extract_post_id("POST_ID:zzzz") is None
    assert oa.extract_post_id("POST_ID:abc") is None  # too short
