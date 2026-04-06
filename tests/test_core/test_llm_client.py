"""Unit tests for core.llm_client module.

Tests cover:
    * LLMConfig dataclass defaults
    * LLMClient — disabled & enabled states
    * Cache behaviour (hit / miss / eviction)
    * Retry logic with back-off
    * Graceful fallback on network errors, timeouts, and bad responses
    * PromptBuilder — template generation for each domain
    * _extract_response_text — multiple response formats
    * create_llm_client factory
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from core.llm_client import (
    LLMClient,
    LLMConfig,
    PromptBuilder,
    _LRUCache,
    create_llm_client,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def disabled_config() -> LLMConfig:
    """Config with LLM disabled."""
    return LLMConfig(enabled=False, endpoint="http://localhost/chat")


@pytest.fixture
def enabled_config() -> LLMConfig:
    """Config with LLM enabled and a dummy endpoint."""
    return LLMConfig(
        enabled=True,
        endpoint="http://localhost:8080/chat",
        timeout_seconds=5,
        max_retries=1,
        cache_enabled=True,
        cache_max_size=4,
    )


@pytest.fixture
def client(enabled_config: LLMConfig) -> LLMClient:
    """An enabled LLM client for testing."""
    return LLMClient(enabled_config)


@pytest.fixture
def disabled_client(disabled_config: LLMConfig) -> LLMClient:
    """A disabled LLM client for testing."""
    return LLMClient(disabled_config)


# ---------------------------------------------------------------------------
# LLMConfig
# ---------------------------------------------------------------------------

class TestLLMConfig:
    """Tests for LLMConfig defaults."""

    def test_defaults(self) -> None:
        cfg = LLMConfig()
        assert cfg.enabled is True
        assert cfg.endpoint == ""
        assert cfg.timeout_seconds == 15
        assert cfg.max_retries == 2
        assert cfg.cache_enabled is True
        assert cfg.cache_max_size == 256

    def test_custom_values(self) -> None:
        cfg = LLMConfig(
            enabled=False,
            endpoint="http://x.com/api",
            timeout_seconds=30,
            max_retries=5,
            cache_enabled=False,
            cache_max_size=10,
        )
        assert cfg.enabled is False
        assert cfg.endpoint == "http://x.com/api"
        assert cfg.cache_max_size == 10

    def test_frozen(self) -> None:
        cfg = LLMConfig()
        with pytest.raises(AttributeError):
            cfg.enabled = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _LRUCache
# ---------------------------------------------------------------------------

class TestLRUCache:
    """Tests for the bounded LRU prompt cache."""

    def test_put_and_get(self) -> None:
        cache = _LRUCache(max_size=3)
        cache.put("a", "alpha")
        assert cache.get("a") == "alpha"

    def test_get_miss(self) -> None:
        cache = _LRUCache(max_size=3)
        assert cache.get("missing") is None

    def test_eviction(self) -> None:
        cache = _LRUCache(max_size=2)
        cache.put("a", "1")
        cache.put("b", "2")
        cache.put("c", "3")  # should evict "a"
        assert cache.get("a") is None
        assert cache.get("b") == "2"
        assert cache.get("c") == "3"

    def test_access_refreshes_order(self) -> None:
        cache = _LRUCache(max_size=2)
        cache.put("a", "1")
        cache.put("b", "2")
        cache.get("a")  # refresh "a"
        cache.put("c", "3")  # should evict "b", not "a"
        assert cache.get("a") == "1"
        assert cache.get("b") is None

    def test_size(self) -> None:
        cache = _LRUCache(max_size=5)
        assert cache.size == 0
        cache.put("x", "y")
        assert cache.size == 1


# ---------------------------------------------------------------------------
# LLMClient — disabled
# ---------------------------------------------------------------------------

class TestLLMClientDisabled:
    """Tests for LLMClient when LLM is disabled."""

    def test_generate_returns_none(self, disabled_client: LLMClient) -> None:
        result = disabled_client.generate("hello")
        assert result is None

    def test_generate_lines_returns_none(self, disabled_client: LLMClient) -> None:
        result = disabled_client.generate_lines("hello")
        assert result is None

    def test_enabled_property(self, disabled_client: LLMClient) -> None:
        assert disabled_client.enabled is False


# ---------------------------------------------------------------------------
# LLMClient — enabled with mocked HTTP
# ---------------------------------------------------------------------------

class TestLLMClientEnabled:
    """Tests for LLMClient with mocked HTTP responses."""

    def test_generate_success(self, client: LLMClient) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "Meeting notes for Q4"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.generate("Write meeting notes")
        assert result == "Meeting notes for Q4"

    def test_generate_caches_response(self, client: LLMClient) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "cached content"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp) as mock_post:
            r1 = client.generate("same prompt")
            r2 = client.generate("same prompt")

        assert r1 == r2 == "cached content"
        assert mock_post.call_count == 1  # only one HTTP call

    def test_generate_empty_endpoint(self) -> None:
        cfg = LLMConfig(enabled=True, endpoint="")
        c = LLMClient(cfg)
        assert c.generate("hello") is None

    def test_generate_lines(self, client: LLMClient) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "term1\nterm2\nterm3\n"}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.generate_lines("search terms")
        assert result == ["term1", "term2", "term3"]

    def test_generate_lines_empty_returns_none(self, client: LLMClient) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "  \n  \n  "}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp):
            result = client.generate_lines("empty lines")
        assert result is None

    def test_stats(self, client: LLMClient) -> None:
        stats = client.stats
        assert stats["requests"] == 0
        assert stats["cache_hits"] == 0
        assert stats["cache_size"] == 0


# ---------------------------------------------------------------------------
# LLMClient — error handling & retry
# ---------------------------------------------------------------------------

class TestLLMClientRetry:
    """Tests for retry and fallback behaviour."""

    def test_timeout_retries_and_returns_none(self, client: LLMClient) -> None:
        import requests
        with patch.object(
            client._session, "post",
            side_effect=requests.exceptions.Timeout("timeout"),
        ):
            with patch("core.llm_client.time.sleep"):
                result = client.generate("timeout prompt")
        assert result is None

    def test_connection_error_returns_none(self, client: LLMClient) -> None:
        import requests
        with patch.object(
            client._session, "post",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            with patch("core.llm_client.time.sleep"):
                result = client.generate("connection error")
        assert result is None

    def test_http_error_returns_none(self, client: LLMClient) -> None:
        mock_resp = MagicMock()
        import requests
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500")

        with patch.object(client._session, "post", return_value=mock_resp):
            with patch("core.llm_client.time.sleep"):
                result = client.generate("500 error")
        assert result is None


# ---------------------------------------------------------------------------
# _extract_response_text
# ---------------------------------------------------------------------------

class TestExtractResponseText:
    """Tests for parsing various LLM response formats."""

    def test_plain_string(self) -> None:
        assert LLMClient._extract_response_text("hello world") == "hello world"

    def test_response_key(self) -> None:
        assert LLMClient._extract_response_text({"response": "abc"}) == "abc"

    def test_text_key(self) -> None:
        assert LLMClient._extract_response_text({"text": "abc"}) == "abc"

    def test_output_key(self) -> None:
        assert LLMClient._extract_response_text({"output": "abc"}) == "abc"

    def test_content_key(self) -> None:
        assert LLMClient._extract_response_text({"content": "abc"}) == "abc"

    def test_openai_choices(self) -> None:
        data = {"choices": [{"message": {"content": "hello"}}]}
        assert LLMClient._extract_response_text(data) == "hello"

    def test_openai_text_format(self) -> None:
        data = {"choices": [{"text": "hello"}]}
        assert LLMClient._extract_response_text(data) == "hello"

    def test_empty_string(self) -> None:
        assert LLMClient._extract_response_text("  ") is None

    def test_unknown_dict(self) -> None:
        assert LLMClient._extract_response_text({"unknown": "val"}) is None

    def test_none_input(self) -> None:
        assert LLMClient._extract_response_text(None) is None


# ---------------------------------------------------------------------------
# PromptBuilder
# ---------------------------------------------------------------------------

class TestPromptBuilder:
    """Tests for prompt template generation."""

    def test_document_content_prompt(self) -> None:
        prompt = PromptBuilder.build_document_content(
            "developer", "README.md", "md",
        )
        assert "developer" in prompt.lower() or "software" in prompt.lower()
        assert "README.md" in prompt
        assert "ONLY" in prompt

    def test_email_subject_prompt(self) -> None:
        prompt = PromptBuilder.build_email_subject(
            "office_user", "John Doe", "Acme Corp",
        )
        assert "John Doe" in prompt
        assert "Acme Corp" in prompt

    def test_search_terms_prompt(self) -> None:
        prompt = PromptBuilder.build_search_terms("home_user", count=5)
        assert "5" in prompt

    def test_bookmark_titles_prompt(self) -> None:
        prompt = PromptBuilder.build_bookmark_titles("developer", count=10)
        assert "10" in prompt

    def test_event_log_prompt(self) -> None:
        prompt = PromptBuilder.build_event_log_message(
            "Error", "Application Error",
        )
        assert "Windows Event Log" in prompt

    def test_file_names_prompt(self) -> None:
        prompt = PromptBuilder.build_file_names("office_user", "reports", count=3)
        assert "3" in prompt
        assert "reports" in prompt

    def test_unknown_profile_defaults_to_home(self) -> None:
        prompt = PromptBuilder.build_search_terms("unknown_profile")
        # Should use home_user persona which mentions "casual"
        assert "casual" in prompt.lower()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestCreateLLMClient:
    """Tests for the create_llm_client factory function."""

    def test_from_empty_config(self) -> None:
        client = create_llm_client({})
        assert client.enabled is False

    def test_from_full_config(self) -> None:
        config = {
            "llm": {
                "enabled": True,
                "endpoint": "http://example.com/chat",
                "timeout_seconds": 10,
                "max_retries": 3,
            }
        }
        client = create_llm_client(config)
        assert client.enabled is True

    def test_from_no_llm_key(self) -> None:
        client = create_llm_client({"other": "stuff"})
        assert client.enabled is False

    def test_invalid_llm_value(self) -> None:
        client = create_llm_client({"llm": "not_a_dict"})
        assert client.enabled is False
