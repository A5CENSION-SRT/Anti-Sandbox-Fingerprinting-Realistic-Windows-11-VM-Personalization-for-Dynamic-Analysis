"""Gemini API client with retry logic, rate limiting, and response caching.

Provides a robust wrapper around Google's Generative AI API with:
- Exponential backoff retry for transient failures
- Token-bucket rate limiting to avoid quota exhaustion
- Disk-based response caching for reproducibility and cost savings
- Structured JSON output parsing with Pydantic validation
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# Type variable for generic Pydantic model parsing
T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class GeminiClientError(Exception):
    """Base exception for Gemini client errors."""


class GeminiAPIError(GeminiClientError):
    """Raised when Gemini API returns an error."""


class GeminiRateLimitError(GeminiClientError):
    """Raised when rate limit is exceeded."""


class GeminiParseError(GeminiClientError):
    """Raised when response cannot be parsed."""


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------

class TokenBucketRateLimiter:
    """Token bucket rate limiter for API calls.
    
    Args:
        tokens_per_minute: Maximum tokens (calls) per minute.
        burst_size: Maximum burst size (defaults to tokens_per_minute).
    """
    
    def __init__(
        self,
        tokens_per_minute: int = 60,
        burst_size: Optional[int] = None,
    ) -> None:
        self._rate = tokens_per_minute / 60.0  # tokens per second
        self._burst = burst_size or tokens_per_minute
        self._tokens = float(self._burst)
        self._last_update = time.monotonic()
    
    def acquire(self, timeout: float = 60.0) -> bool:
        """Acquire a token, blocking up to timeout seconds.
        
        Returns:
            True if token acquired, False if timeout.
        """
        start = time.monotonic()
        while True:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            if time.monotonic() - start >= timeout:
                return False
            # Wait for approximately one token worth of time
            time.sleep(min(1.0 / self._rate, 0.1))
    
    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_update
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last_update = now


# ---------------------------------------------------------------------------
# Response Cache
# ---------------------------------------------------------------------------

class ResponseCache:
    """Disk-based response cache for Gemini API calls.
    
    Caches responses by hashing the prompt + model + temperature, allowing
    reproducible results and reducing API costs during development.
    
    Args:
        cache_dir: Directory to store cached responses.
        ttl_hours: Time-to-live for cache entries (0 = never expire).
    """
    
    def __init__(
        self,
        cache_dir: Path,
        ttl_hours: int = 24,
    ) -> None:
        self._cache_dir = cache_dir
        self._ttl_seconds = ttl_hours * 3600 if ttl_hours > 0 else 0
        self._cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _cache_key(
        self,
        prompt: str,
        model: str,
        temperature: float,
    ) -> str:
        """Generate cache key from request parameters."""
        content = f"{model}:{temperature}:{prompt}"
        return hashlib.sha256(content.encode()).hexdigest()[:32]
    
    def get(
        self,
        prompt: str,
        model: str,
        temperature: float,
    ) -> Optional[str]:
        """Retrieve cached response if valid.
        
        Returns:
            Cached response text, or None if not found/expired.
        """
        key = self._cache_key(prompt, model, temperature)
        cache_file = self._cache_dir / f"{key}.json"
        
        if not cache_file.exists():
            return None
        
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        
        # Check expiration
        if self._ttl_seconds > 0:
            cached_at = data.get("cached_at", 0)
            if time.time() - cached_at > self._ttl_seconds:
                cache_file.unlink(missing_ok=True)
                return None
        
        return data.get("response")
    
    def set(
        self,
        prompt: str,
        model: str,
        temperature: float,
        response: str,
    ) -> None:
        """Store response in cache."""
        key = self._cache_key(prompt, model, temperature)
        cache_file = self._cache_dir / f"{key}.json"
        
        data = {
            "cached_at": time.time(),
            "model": model,
            "temperature": temperature,
            "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest()[:16],
            "response": response,
        }
        
        try:
            cache_file.write_text(
                json.dumps(data, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Failed to write cache: %s", e)


# ---------------------------------------------------------------------------
# Gemini Client
# ---------------------------------------------------------------------------

class GeminiClient:
    """Robust Gemini API client with caching, rate limiting, and retries.
    
    Args:
        api_key: Gemini API key (or set GEMINI_API_KEY env var).
        model: Model name (default: gemini-2.0-flash).
        temperature: Generation temperature (0.0-1.0).
        max_retries: Maximum retry attempts for transient errors.
        cache_dir: Directory for response caching (None = no caching).
        cache_ttl_hours: Cache TTL in hours (0 = never expire).
        rate_limit_rpm: Rate limit in requests per minute.
    
    Example:
        >>> client = GeminiClient(api_key="...")
        >>> response = client.generate("Describe a marketing manager persona")
        >>> persona = client.generate_structured(prompt, PersonaContext)
    """
    
    _RETRY_DELAYS = [1, 2, 4, 8, 16]  # Exponential backoff seconds
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-2.0-flash",
        temperature: float = 0.7,
        max_retries: int = 3,
        cache_dir: Optional[Path] = None,
        cache_ttl_hours: int = 24,
        rate_limit_rpm: int = 60,
    ) -> None:
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self._api_key:
            logger.warning(
                "No Gemini API key provided. Set GEMINI_API_KEY env var or "
                "pass api_key to constructor. Client will fail on API calls."
            )
        
        self._model = model
        self._temperature = temperature
        self._max_retries = max_retries
        
        # Initialize cache
        self._cache: Optional[ResponseCache] = None
        if cache_dir:
            self._cache = ResponseCache(cache_dir, cache_ttl_hours)
        
        # Initialize rate limiter
        self._rate_limiter = TokenBucketRateLimiter(rate_limit_rpm)
        
        # Lazy-load the generative AI library
        self._genai = None
        self._gen_model = None
    
    def _ensure_initialized(self) -> None:
        """Lazy-initialize the Gemini SDK."""
        if self._genai is not None:
            return
        
        try:
            import google.generativeai as genai
        except ImportError:
            raise GeminiClientError(
                "google-generativeai package not installed. "
                "Run: pip install google-generativeai"
            )
        
        if not self._api_key:
            raise GeminiClientError("Gemini API key not configured")
        
        genai.configure(api_key=self._api_key)
        self._genai = genai
        self._gen_model = genai.GenerativeModel(self._model)
        logger.info("Initialized Gemini client with model: %s", self._model)
    
    def generate(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        use_cache: bool = True,
    ) -> str:
        """Generate text response from Gemini.
        
        Args:
            prompt: The prompt to send.
            temperature: Override default temperature.
            use_cache: Whether to use response cache.
        
        Returns:
            Generated text response.
        
        Raises:
            GeminiAPIError: On API errors after retries exhausted.
            GeminiRateLimitError: If rate limit cannot be acquired.
        """
        temp = temperature if temperature is not None else self._temperature
        
        # Check cache first
        if use_cache and self._cache:
            cached = self._cache.get(prompt, self._model, temp)
            if cached:
                logger.debug("Cache hit for prompt (hash: %s...)",
                           hashlib.sha256(prompt.encode()).hexdigest()[:8])
                return cached
        
        # Acquire rate limit token
        if not self._rate_limiter.acquire(timeout=30.0):
            raise GeminiRateLimitError("Rate limit timeout")
        
        # Initialize SDK if needed
        self._ensure_initialized()
        
        # Retry loop with exponential backoff
        last_error: Optional[Exception] = None
        for attempt in range(self._max_retries):
            try:
                generation_config = self._genai.types.GenerationConfig(
                    temperature=temp,
                )
                response = self._gen_model.generate_content(
                    prompt,
                    generation_config=generation_config,
                )
                
                result = response.text
                
                # Cache successful response
                if use_cache and self._cache:
                    self._cache.set(prompt, self._model, temp, result)
                
                return result
                
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                
                # Check if retryable
                if any(x in error_str for x in ["rate", "quota", "429", "503"]):
                    delay = self._RETRY_DELAYS[min(attempt, len(self._RETRY_DELAYS) - 1)]
                    logger.warning(
                        "Gemini API rate limit/quota error (attempt %d/%d), "
                        "retrying in %ds: %s",
                        attempt + 1, self._max_retries, delay, e
                    )
                    time.sleep(delay)
                    continue
                
                # Non-retryable error
                logger.error("Gemini API error: %s", e)
                raise GeminiAPIError(f"Gemini API error: {e}") from e
        
        raise GeminiAPIError(
            f"Gemini API failed after {self._max_retries} retries: {last_error}"
        )
    
    def generate_json(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """Generate and parse JSON response from Gemini.
        
        The prompt should instruct Gemini to respond with valid JSON.
        
        Args:
            prompt: Prompt requesting JSON output.
            temperature: Override default temperature.
            use_cache: Whether to use response cache.
        
        Returns:
            Parsed JSON as dict.
        
        Raises:
            GeminiParseError: If response is not valid JSON.
        """
        response = self.generate(prompt, temperature, use_cache)
        
        # Try to extract JSON from response (handle markdown code blocks)
        text = response.strip()
        if text.startswith("```"):
            # Extract content between code fences
            lines = text.split("\n")
            start_idx = 1 if lines[0].startswith("```") else 0
            end_idx = len(lines) - 1 if lines[-1] == "```" else len(lines)
            text = "\n".join(lines[start_idx:end_idx])
            # Remove language specifier if present
            if text.startswith("json"):
                text = text[4:].strip()
        
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise GeminiParseError(
                f"Failed to parse Gemini response as JSON: {e}\n"
                f"Response: {response[:500]}..."
            ) from e
    
    def generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        temperature: Optional[float] = None,
        use_cache: bool = True,
    ) -> T:
        """Generate and validate structured output against a Pydantic schema.
        
        Args:
            prompt: Prompt requesting JSON matching the schema.
            schema: Pydantic model class to validate against.
            temperature: Override default temperature.
            use_cache: Whether to use response cache.
        
        Returns:
            Validated Pydantic model instance.
        
        Raises:
            GeminiParseError: If response doesn't match schema.
        """
        data = self.generate_json(prompt, temperature, use_cache)
        
        try:
            return schema.model_validate(data)
        except ValidationError as e:
            raise GeminiParseError(
                f"Gemini response doesn't match schema {schema.__name__}: {e}"
            ) from e
    
    def generate_list(
        self,
        prompt: str,
        item_schema: Type[T],
        temperature: Optional[float] = None,
        use_cache: bool = True,
    ) -> List[T]:
        """Generate and validate a list of structured items.
        
        Args:
            prompt: Prompt requesting JSON array.
            item_schema: Pydantic model for each array item.
            temperature: Override default temperature.
            use_cache: Whether to use response cache.
        
        Returns:
            List of validated Pydantic model instances.
        """
        data = self.generate_json(prompt, temperature, use_cache)
        
        if not isinstance(data, list):
            # Try to extract list from common response shapes
            if isinstance(data, dict):
                # Look for common list keys
                for key in ["items", "results", "data", "list"]:
                    if key in data and isinstance(data[key], list):
                        data = data[key]
                        break
        
        if not isinstance(data, list):
            raise GeminiParseError(
                f"Expected JSON array, got {type(data).__name__}"
            )
        
        try:
            return [item_schema.model_validate(item) for item in data]
        except ValidationError as e:
            raise GeminiParseError(
                f"Item doesn't match schema {item_schema.__name__}: {e}"
            ) from e
    
    @property
    def model(self) -> str:
        """Return the model name."""
        return self._model
    
    @property
    def is_configured(self) -> bool:
        """Return True if API key is configured."""
        return bool(self._api_key)
