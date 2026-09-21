"""
tests/test_digest_thinking.py

Verifies that MiniMax-M3 "thinking" content never reaches the posted
digest:
  1. `thinking: disabled` is actually passed to the API call.
  2. Even if a <think>...</think> block leaks through anyway, the
     regex safety net in `_strip_thinking` removes it.
"""

from unittest.mock import MagicMock, patch

import pytest

import osint_aggregator as oa


# ─────────────────────────────────────────────
# 1. _strip_thinking — pure unit tests, no mocking needed
# ─────────────────────────────────────────────

class TestStripThinking:
    def test_removes_single_think_block(self):
        raw = "<think>some internal reasoning here</think>**Headline**\nBody text."
        assert oa._strip_thinking(raw) == "**Headline**\nBody text."

    def test_removes_multiline_think_block(self):
        raw = (
            "<think>\n"
            "Let me analyze this...\n"
            "Multiple lines of reasoning.\n"
            "</think>\n"
            "**Real digest starts here**"
        )
        assert oa._strip_thinking(raw) == "**Real digest starts here**"

    def test_removes_multiple_think_blocks(self):
        raw = "<think>first</think>Kept text A<think>second</think>Kept text B"
        assert oa._strip_thinking(raw) == "Kept text AKept text B"

    def test_no_think_tags_returns_unchanged(self):
        raw = "**Headline**\nJust a normal digest, no reasoning tags at all."
        assert oa._strip_thinking(raw) == raw

    def test_empty_string(self):
        assert oa._strip_thinking("") == ""

    def test_only_think_block_returns_empty(self):
        raw = "<think>only reasoning, no real answer produced</think>"
        assert oa._strip_thinking(raw) == ""

    def test_strips_surrounding_whitespace_after_removal(self):
        raw = "   <think>reasoning</think>   \n\n  Final digest text.  "
        assert oa._strip_thinking(raw) == "Final digest text."


# ─────────────────────────────────────────────
# 2. _summarise_sync — confirms the API call itself is configured
#    correctly (thinking disabled) and that the returned text is clean
# ─────────────────────────────────────────────

class TestSummariseSyncThinkingConfig:
    def _fake_posts(self):
        return [
            {"source": "@testchannel", "text": "Some news snippet about an event."},
            {"source": "RSS Feed", "text": "Another snippet, different source."},
        ]

    def _make_mock_response(self, content: str):
        mock_message = MagicMock()
        mock_message.content = content
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        return mock_response

    @patch("osint_aggregator._build_llm_client")
    @patch("osint_aggregator.config")
    def test_thinking_disabled_is_passed_to_api(self, mock_config, mock_build_client):
        mock_config.llm.return_value.model = "MiniMax-M3"
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._make_mock_response(
            "**Clean headline**\nClean body, no reasoning tags."
        )
        mock_build_client.return_value = mock_client

        oa._summarise_sync(self._fake_posts(), "2026-09-21")

        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs.get("extra_body") == {"thinking": {"type": "disabled"}}

    @patch("osint_aggregator._build_llm_client")
    @patch("osint_aggregator.config")
    def test_leaked_think_block_is_stripped_from_result(
        self, mock_config, mock_build_client
    ):
        """Even if the provider ignores `thinking: disabled` and leaks a
        <think> block anyway, _summarise_sync must not return it."""
        mock_config.llm.return_value.model = "MiniMax-M3"
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._make_mock_response(
            "<think>Let me figure out the top stories...</think>"
            "**Top story headline**\nSubstantive body text about the story."
        )
        mock_build_client.return_value = mock_client

        result = oa._summarise_sync(self._fake_posts(), "2026-09-21")

        assert "<think>" not in result
        assert "</think>" not in result
        assert "Let me figure out" not in result
        assert result.startswith("**Top story headline**")

    @patch("osint_aggregator._build_llm_client")
    @patch("osint_aggregator.config")
    def test_clean_response_passes_through_unchanged(
        self, mock_config, mock_build_client
    ):
        mock_config.llm.return_value.model = "MiniMax-M3"
        mock_client = MagicMock()
        expected = "**Headline one**\nBody one.\n\n**Headline two**\nBody two."
        mock_client.chat.completions.create.return_value = self._make_mock_response(
            expected
        )
        mock_build_client.return_value = mock_client

        result = oa._summarise_sync(self._fake_posts(), "2026-09-21")

        assert result == expected


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
