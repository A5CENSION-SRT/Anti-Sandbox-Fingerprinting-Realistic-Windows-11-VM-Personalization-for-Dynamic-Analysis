"""LLM Client for dynamic artifact content generation.

Provides a reusable HTTP client that communicates with a locally hosted
LLM endpoint.  All services that need realistic, varied text content
(documents, emails, search terms, event logs) consume this client
via dependency injection from the Orchestrator.

Key design decisions:
    * **Graceful fallback** — every public method returns ``Optional[str]``.
      A ``None`` return signals the caller to use its existing static
      template.  The LLM is *never* a hard dependency.
    * **In-memory cache** — identical prompts hit a dict-based cache to
      avoid redundant HTTP round-trips across services and runs.
    * **Retry with back-off** — transient network errors are retried up to
      ``max_retries`` times with exponential back-off.
    * **Structured logging** — every request/response/failure is logged for
      full auditability.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMConfig:
    """Immutable configuration for the LLM client.

    Attributes:
        enabled: Master switch — when ``False``, all methods return ``None``.
        endpoint: Full URL of the LLM chat API.
        timeout_seconds: HTTP request timeout.
        max_retries: Number of retries on transient failures.
        cache_enabled: Whether to cache prompt→response pairs.
        cache_max_size: Maximum number of cached entries.
    """

    enabled: bool = True
    endpoint: str = ""
    timeout_seconds: int = 15
    max_retries: int = 2
    cache_enabled: bool = True
    cache_max_size: int = 256


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LLMClientError(Exception):
    """Raised when LLM communication fails irrecoverably."""


# ---------------------------------------------------------------------------
# LRU Cache (simple, thread-safe-*enough* for single-threaded ARC)
# ---------------------------------------------------------------------------

class _LRUCache:
    """Bounded LRU dict for caching prompt→response pairs."""

    def __init__(self, max_size: int = 256) -> None:
        self._max_size = max(1, max_size)
        self._store: OrderedDict[str, str] = OrderedDict()

    def get(self, key: str) -> Optional[str]:
        if key in self._store:
            self._store.move_to_end(key)
            return self._store[key]
        return None

    def put(self, key: str, value: str) -> None:
        if key in self._store:
            self._store.move_to_end(key)
            self._store[key] = value
        else:
            if len(self._store) >= self._max_size:
                self._store.popitem(last=False)
            self._store[key] = value

    @property
    def size(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# Prompt Builder
# ---------------------------------------------------------------------------

class PromptBuilder:
    """Constructs domain-specific prompts for the LLM.
    
    Loads configuration from system_prompts.json.
    """

    _PROFILE_PERSONAS: Dict[str, str] = {
        "office_user": (
            "You are simulating a typical office worker at a mid-size corporation. "
            "They use Microsoft Office daily, attend meetings, write reports, and "
            "communicate via email and Teams."
        ),
        "developer": (
            "You are simulating a software developer. They code daily, use Git, "
            "Docker, VS Code, read Stack Overflow and GitHub, and write technical "
            "documentation."
        ),
        "home_user": (
            "You are simulating a casual home computer user. They browse social "
            "media, shop online, watch streaming content, and manage personal "
            "files like recipes and shopping lists."
        ),
    }

    _PROMPTS: Optional[Dict[str, str]] = None

    @classmethod
    def _load_prompts(cls) -> Dict[str, str]:
        if cls._PROMPTS is None:
            import json
            from pathlib import Path
            prompts_file = Path(__file__).resolve().parent.parent / "system_prompts.json"
            try:
                with open(prompts_file, "r", encoding="utf-8") as f:
                    cls._PROMPTS = json.load(f)
            except Exception as e:
                logger.error("Failed to load system_prompts.json: %s", e)
                cls._PROMPTS = {}
        return cls._PROMPTS or {}

    @classmethod
    def _persona(cls, profile_type: str) -> str:
        return cls._PROFILE_PERSONAS.get(
            profile_type, cls._PROFILE_PERSONAS["home_user"]
        )

    @classmethod
    def _build_base(cls, profile_type: str, prompt_key: str, **kwargs) -> str:
        prompts = cls._load_prompts()
        general = prompts.get("general", "You are an expert Windows artifact generation AI.")
        template = prompts.get(prompt_key, "")
        try:
            custom_text = template.format(**kwargs)
        except KeyError:
            custom_text = template # fallback if missing keys
            
        persona = cls._persona(profile_type)
        return (
            f"<start_of_turn>user\n"
            f"{general}\n"
            f"{persona}\n\n"
            f"{custom_text}\n"
            f"<end_of_turn>\n<start_of_turn>model\n"
        )

    @classmethod
    def build_document_content(
        cls,
        profile_type: str,
        doc_name: str,
        doc_type: str,
        *,
        extra_context: str = "",
    ) -> str:
        return cls._build_base(
            profile_type, "document_content",
            doc_name=doc_name, doc_type=doc_type, extra_context=extra_context
        )

    @classmethod
    def build_email_subject(
        cls,
        profile_type: str,
        sender_name: str,
        organization: str,
    ) -> str:
        return cls._build_base(
            profile_type, "email_subject",
            sender_name=sender_name, organization=organization
        )

    @classmethod
    def build_search_terms(
        cls,
        profile_type: str,
        count: int = 10,
    ) -> str:
        return cls._build_base(profile_type, "search_terms", count=count)

    @classmethod
    def build_bookmark_titles(
        cls,
        profile_type: str,
        count: int = 8,
    ) -> str:
        return cls._build_base(profile_type, "bookmark_titles", count=count)

    @classmethod
    def build_event_log_message(
        cls,
        event_type: str,
        source: str,
        profile_type: str = "office_user",
    ) -> str:
        return cls._build_base(
            profile_type, "event_log_message",
            source=source, event_type=event_type
        )

    @classmethod
    def build_file_names(
        cls,
        profile_type: str,
        file_category: str,
        count: int = 5,
    ) -> str:
        return cls._build_base(
            profile_type, "file_names",
            file_category=file_category, count=count
        )

    @staticmethod
    def build_pre_validation_prompt(
        profile_type: str,
        timeline_days: int,
        services: list[str],
    ) -> str:
        prompts = PromptBuilder._load_prompts()
        template = prompts.get("pre_validation_prompt", "")
        services_str = ", ".join(services)
        custom_text = template.format(profile_type=profile_type, timeline_days=timeline_days, services_str=services_str)
        return (
            f"<start_of_turn>user\n"
            f"{custom_text}\n"
            f"<end_of_turn>\n<start_of_turn>model\n"
        )

    @classmethod
    def build_web_agent_prompt(
        cls,
        profile_type: str,
        file_name: str,
    ) -> str:
        return cls._build_base(profile_type, "web_agent_prompt", file_name=file_name)


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------

class LLMClient:
    """HTTP client for the locally hosted LLM endpoint.

    Args:
        config: Validated :class:`LLMConfig` instance.

    Example::

        cfg = LLMConfig(endpoint="http://localhost:8080/chat")
        client = LLMClient(cfg)
        result = client.generate("Write meeting notes for Q4 review")
        if result is None:
            # fallback to static template
            ...
    """

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._cache = _LRUCache(max_size=config.cache_max_size)
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        
        hf_token = os.getenv("HF_TOKEN") or os.getenv("hf_token")
        if not hf_token:
            from pathlib import Path
            env_path = Path(__file__).resolve().parent.parent / ".env"
            if env_path.exists():
                try:
                    with open(env_path, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.lower().startswith("hf_token="):
                                hf_token = line.split("=", 1)[1].strip().strip('"').strip("'")
                                break
                except Exception as e:
                    logger.debug("Could not parse .env: %s", e)
        
        if hf_token:
            self._session.headers.update({"Authorization": f"Bearer {hf_token}"})
            logger.info("Loaded Hugging Face API Token into LLMClient headers")

        self._request_count: int = 0
        self._cache_hit_count: int = 0

    # -- public API ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Whether the LLM client is active."""
        return self._config.enabled

    @property
    def stats(self) -> Dict[str, int]:
        """Return usage statistics."""
        return {
            "requests": self._request_count,
            "cache_hits": self._cache_hit_count,
            "cache_size": self._cache.size,
        }

    def generate(self, prompt: str) -> Optional[str]:
        """Send a prompt to the LLM and return the response text.

        Args:
            prompt: The prompt string to send.

        Returns:
            Generated text, or ``None`` if the LLM is disabled, unreachable,
            or returns an error.  Callers should fall back to their static
            template when ``None`` is returned.
        """
        if not self._config.enabled:
            logger.debug("LLM disabled — returning None")
            return None

        if not self._config.endpoint:
            logger.warning("LLM endpoint not configured — returning None")
            return None

        # Check cache
        if self._config.cache_enabled:
            cache_key = self._cache_key(prompt)
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache_hit_count += 1
                logger.debug("LLM cache hit (hash=%s)", cache_key[:12])
                return cached

        # Make HTTP request with retry
        response_text = self._request_with_retry(prompt)

        if response_text and self._config.cache_enabled:
            self._cache.put(self._cache_key(prompt), response_text)

        return response_text

    def generate_lines(self, prompt: str) -> Optional[List[str]]:
        """Generate content and split into non-empty lines.

        Useful for search terms, file names, bookmark titles, etc.

        Args:
            prompt: The prompt string.

        Returns:
            List of non-empty stripped lines, or ``None`` on failure.
        """
        raw = self.generate(prompt)
        if raw is None:
            return None
        lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
        return lines if lines else None

    # -- internal -----------------------------------------------------------

    def _request_with_retry(self, prompt: str) -> Optional[str]:
        """Execute an HTTP POST with retry logic.

        Returns:
            Response text or ``None`` on failure.
        """
        last_error: Optional[Exception] = None

        for attempt in range(1, self._config.max_retries + 2):
            try:
                self._request_count += 1
                logger.debug(
                    "LLM request attempt %d/%d (prompt length=%d)",
                    attempt,
                    self._config.max_retries + 1,
                    len(prompt),
                )

                # Use {"inputs": prompt} to support HF API natively, alongside {"prompt": prompt} for fallback generic endpoints
                resp = self._session.post(
                    self._config.endpoint,
                    json={
                        "prompt": prompt, 
                        "inputs": prompt,
                        "parameters": {"max_new_tokens": 512, "return_full_text": False}
                    },
                    timeout=self._config.timeout_seconds,
                )
                resp.raise_for_status()

                data = resp.json()
                # Handle common response formats
                text = self._extract_response_text(data)

                if text:
                    logger.debug(
                        "LLM response received (length=%d)", len(text),
                    )
                    return text

                logger.warning("LLM returned empty response on attempt %d", attempt)

            except requests.exceptions.Timeout:
                last_error = TimeoutError(
                    f"LLM request timed out after {self._config.timeout_seconds}s"
                )
                logger.warning(
                    "LLM timeout on attempt %d/%d",
                    attempt,
                    self._config.max_retries + 1,
                )

            except requests.exceptions.ConnectionError as exc:
                last_error = exc
                logger.warning(
                    "LLM connection error on attempt %d/%d: %s",
                    attempt,
                    self._config.max_retries + 1,
                    exc,
                )

            except requests.exceptions.HTTPError as exc:
                last_error = exc
                logger.warning(
                    "LLM HTTP error on attempt %d/%d: %s",
                    attempt,
                    self._config.max_retries + 1,
                    exc,
                )

            except Exception as exc:
                last_error = exc
                logger.error(
                    "Unexpected LLM error on attempt %d/%d: %s",
                    attempt,
                    self._config.max_retries + 1,
                    exc,
                )

            # Back off before retry (skip on last attempt)
            if attempt <= self._config.max_retries:
                backoff = min(2 ** (attempt - 1), 8)
                logger.debug("Backing off %ds before retry", backoff)
                time.sleep(backoff)

        logger.warning(
            "LLM exhausted all %d retries — falling back to static content. "
            "Last error: %s",
            self._config.max_retries + 1,
            last_error,
        )
        return None

    @staticmethod
    def _extract_response_text(data: Any) -> Optional[str]:
        """Extract the generated text from various LLM response formats.

        Supports:
            * ``{"response": "..."}``
            * ``{"text": "..."}``
            * ``{"choices": [{"text": "..."}]}``  (OpenAI-like)
            * ``{"output": "..."}``
            * Plain string
        """
        if isinstance(data, str):
            return data.strip() or None

        # HF Inference API style list return
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict) and "generated_text" in first:
                return first["generated_text"].strip() or None

        if isinstance(data, dict):
            for key in ("response", "text", "output", "content", "result"):
                if key in data and isinstance(data[key], str):
                    return data[key].strip() or None

            # OpenAI-style choices array
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict):
                    msg = first.get("message", {})
                    if isinstance(msg, dict) and "content" in msg:
                        return msg["content"].strip() or None
                    text = first.get("text", "")
                    if text:
                        return text.strip() or None

        return None

    @staticmethod
    def _cache_key(prompt: str) -> str:
        """Produce a deterministic cache key from a prompt string."""
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()
        logger.debug("LLM client session closed. Stats: %s", self.stats)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_llm_client(config: Dict[str, Any]) -> LLMClient:
    """Create an :class:`LLMClient` from a raw config dictionary.

    Args:
        config: The ``llm`` section of config.yaml, or an empty dict.

    Returns:
        A configured :class:`LLMClient` instance (may be disabled).
    """
    llm_section = config.get("llm", {})
    if not isinstance(llm_section, dict):
        llm_section = {}

    llm_config = LLMConfig(
        enabled=llm_section.get("enabled", False),
        endpoint=llm_section.get("endpoint", ""),
        timeout_seconds=llm_section.get("timeout_seconds", 15),
        max_retries=llm_section.get("max_retries", 2),
        cache_enabled=llm_section.get("cache_enabled", True),
        cache_max_size=llm_section.get("cache_max_size", 256),
    )

    client = LLMClient(llm_config)
    logger.info(
        "LLM client created (enabled=%s, endpoint=%s)",
        llm_config.enabled,
        llm_config.endpoint or "<not set>",
    )
    return client
