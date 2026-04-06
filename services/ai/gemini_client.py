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
import re
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

    @staticmethod
    def _to_string_list(value: Any) -> List[str]:
        """Convert mixed input into a clean list of non-empty strings."""
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str) and value.strip():
            if "," in value:
                return [part.strip() for part in value.split(",") if part.strip()]
            return [value.strip()]
        return []

    @staticmethod
    def _dedupe_strings(values: List[str]) -> List[str]:
        """Return case-insensitive de-duplicated values preserving order."""
        deduped: List[str] = []
        seen: set[str] = set()
        for value in values:
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(value)
        return deduped

    @staticmethod
    def _build_username(full_name: str) -> str:
        """Derive a schema-compatible username from full name."""
        parts = [
            re.sub(r"[^a-z0-9]", "", part.lower())
            for part in full_name.split()
            if part.strip()
        ]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            username = f"{parts[0]}.{parts[-1]}"
        elif parts:
            username = parts[0]
        else:
            username = "alex.user"

        if not username or not username[0].isalpha():
            username = f"u{username}" if username else "user.account"

        return username[:20]

    @staticmethod
    def _build_domain(organization: str) -> str:
        """Build a plausible email domain from organization name."""
        if organization.strip().lower() == "personal":
            return "gmail.com"
        cleaned = re.sub(r"[^a-z0-9]", "", organization.lower())
        if not cleaned:
            cleaned = "company"
        return f"{cleaned[:16]}.com"

    @staticmethod
    def _normalize_tech_proficiency(value: Any) -> str:
        """Map free-form proficiency values to schema enum values."""
        text = str(value or "intermediate").strip().lower()
        if any(k in text for k in ("expert", "advanced", "high", "pro", "senior")):
            return "high"
        if any(k in text for k in ("beginner", "basic", "low", "novice")):
            return "low"
        return "intermediate"

    def _normalize_persona_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize common alternate persona JSON shapes to PersonaContext."""
        data: Dict[str, Any] = dict(payload)

        # Handle wrapped outputs like {"persona": {...}}
        nested = data.get("persona")
        if isinstance(nested, dict):
            merged = dict(nested)
            for key, value in data.items():
                if key != "persona" and key not in merged:
                    merged[key] = value
            data = merged

        def pick(*keys: str, default: Any = None) -> Any:
            for key in keys:
                if key in data and data[key] not in (None, "", []):
                    return data[key]
            return default

        full_name = str(pick("full_name", "name", default="Alex Johnson"))
        occupation = str(pick("occupation", "role", "job_title", default="Office Worker"))
        organization = str(pick("organization", "company", "employer", default="Personal"))
        if not organization.strip():
            organization = "Personal"

        username = str(pick("username", default="")).strip() or self._build_username(full_name)
        email = str(pick("email", default="")).strip()
        if not email:
            email = f"{username}@{self._build_domain(organization)}"

        age_range_value = pick("age_range")
        if age_range_value:
            nums = [int(n) for n in re.findall(r"\d+", str(age_range_value))]
            if len(nums) >= 2:
                age_range = f"{nums[0]:02d}-{nums[1]:02d}"
            elif len(nums) == 1:
                low = max(18, nums[0] - 4)
                high = min(75, nums[0] + 3)
                age_range = f"{low:02d}-{high:02d}"
            else:
                age_range = "28-35"
        else:
            age_raw = pick("age")
            if isinstance(age_raw, (int, float)):
                low = max(18, int(age_raw) - 4)
                high = min(75, int(age_raw) + 3)
                age_range = f"{low:02d}-{high:02d}"
            else:
                age_range = "28-35"

        interests_raw = pick("interests", default={})
        if isinstance(interests_raw, dict):
            hobbies = self._to_string_list(interests_raw.get("hobbies"))
            professional_topics = self._to_string_list(interests_raw.get("professional_topics"))
            entertainment = self._to_string_list(interests_raw.get("entertainment"))
        elif isinstance(interests_raw, list):
            hobbies = self._to_string_list(interests_raw)
            professional_topics = []
            entertainment = []
        else:
            hobbies = []
            professional_topics = []
            entertainment = []

        hobbies = self._dedupe_strings(hobbies)
        professional_topics = self._dedupe_strings(professional_topics)
        entertainment = self._dedupe_strings(entertainment)

        hobby_fallback = ["reading", "fitness", "technology"]
        topic_fallback = ["productivity", "industry trends"]

        for fallback in hobby_fallback:
            if len(hobbies) >= 3:
                break
            if fallback.lower() not in {h.lower() for h in hobbies}:
                hobbies.append(fallback)

        for fallback in topic_fallback:
            if len(professional_topics) >= 2:
                break
            if fallback.lower() not in {p.lower() for p in professional_topics}:
                professional_topics.append(fallback)

        work_style_raw = pick("work_style", default={})
        usage_patterns = pick("usage_patterns", default={})
        tech_stack = pick("tech_stack", default={})

        if isinstance(work_style_raw, dict):
            work_description = str(
                work_style_raw.get("description")
                or pick("persona_summary", default=f"{occupation} with a practical workflow")
            )
            typical_tools = self._to_string_list(work_style_raw.get("typical_tools"))
            collaboration_style = str(work_style_raw.get("collaboration_style", "hybrid")).lower()
            meeting_frequency = str(work_style_raw.get("meeting_frequency", "moderate")).lower()
        else:
            work_description = str(pick("persona_summary", default=f"{occupation} with a practical workflow"))
            typical_tools = []
            collaboration_style = "hybrid"
            meeting_frequency = "moderate"

        if isinstance(tech_stack, dict):
            for key in ("tools", "software", "apps"):
                typical_tools.extend(self._to_string_list(tech_stack.get(key)))

        if isinstance(usage_patterns, dict):
            typical_tools.extend(self._to_string_list(usage_patterns.get("tools")))

        if not typical_tools:
            occupation_lower = occupation.lower()
            if any(k in occupation_lower for k in ("developer", "engineer", "programmer", "devops")):
                typical_tools = ["VS Code", "Git", "Docker", "Terminal"]
            elif any(k in occupation_lower for k in ("manager", "marketing", "sales", "analyst")):
                typical_tools = ["Outlook", "Excel", "Teams", "PowerPoint"]
            else:
                typical_tools = ["Chrome", "Notepad", "File Explorer"]

        typical_tools = self._dedupe_strings(typical_tools)[:8]

        projects_raw = pick("project_names", "projects", default=[])
        if isinstance(projects_raw, dict):
            project_names = self._to_string_list(list(projects_raw.keys()))
        else:
            project_names = self._to_string_list(projects_raw)
        project_defaults = ["Platform Upgrade", "Q4 Initiative", "Process Automation"]
        for default_project in project_defaults:
            if len(project_names) >= 3:
                break
            if default_project.lower() not in {p.lower() for p in project_names}:
                project_names.append(default_project)

        colleagues_raw = pick("colleague_names", "coworkers", "team_members", default=[])
        if isinstance(colleagues_raw, dict):
            colleague_names = self._to_string_list(list(colleagues_raw.keys()))
        else:
            colleague_names = self._to_string_list(colleagues_raw)
        colleague_defaults = [
            "Taylor Reed",
            "Morgan Patel",
            "Casey Nguyen",
            "Jordan Lee",
            "Sam Wilson",
        ]
        for fallback_name in colleague_defaults:
            if len(colleague_names) >= 5:
                break
            if fallback_name.lower() not in {c.lower() for c in colleague_names}:
                colleague_names.append(fallback_name)

        tech_proficiency = self._normalize_tech_proficiency(
            pick("tech_proficiency", "technical_proficiency", default="intermediate")
        )

        work_hours_start = pick("work_hours_start", default=9)
        work_hours_end = pick("work_hours_end", default=17)
        try:
            work_hours_start = max(0, min(23, int(work_hours_start)))
        except (TypeError, ValueError):
            work_hours_start = 9
        try:
            work_hours_end = max(0, min(23, int(work_hours_end)))
        except (TypeError, ValueError):
            work_hours_end = 17
        if work_hours_end <= work_hours_start:
            work_hours_end = min(23, work_hours_start + 8)

        active_days_raw = pick("active_days")
        active_days: List[int] = []
        if isinstance(active_days_raw, list):
            for day in active_days_raw:
                try:
                    day_int = int(day)
                except (TypeError, ValueError):
                    continue
                if 1 <= day_int <= 7:
                    active_days.append(day_int)
        if not active_days:
            active_days = [1, 2, 3, 4, 5]

        return {
            "full_name": full_name,
            "username": username,
            "email": email,
            "organization": organization,
            "occupation": occupation,
            "department": pick("department", "team"),
            "age_range": age_range,
            "locale": str(pick("locale", default="en_US")),
            "location": pick("location", "city"),
            "tech_proficiency": tech_proficiency,
            "interests": {
                "hobbies": hobbies[:10],
                "professional_topics": professional_topics[:8],
                "entertainment": entertainment[:8],
            },
            "work_style": {
                "description": work_description,
                "typical_tools": typical_tools,
                "collaboration_style": collaboration_style,
                "meeting_frequency": meeting_frequency,
            },
            "project_names": self._dedupe_strings(project_names)[:15],
            "colleague_names": self._dedupe_strings(colleague_names)[:20],
            "work_hours_start": work_hours_start,
            "work_hours_end": work_hours_end,
            "active_days": active_days,
        }

    def _normalize_list_item_payload(self, payload: Any, item_schema: Type[T]) -> Any:
        """Normalize list item payload for common schema mismatches."""
        if not isinstance(payload, dict):
            return payload

        item: Dict[str, Any] = dict(payload)
        fields = item_schema.model_fields

        if "context" in fields and not item.get("context"):
            context_source = (
                item.get("content_theme")
                or item.get("filename_pattern")
                or item.get("pattern")
                or item.get("url_template")
            )
            item["context"] = str(context_source) if context_source else f"{item_schema.__name__} generated by AI"

        if "expansion" in fields:
            expansion_raw = item.get("expansion")
            if not isinstance(expansion_raw, dict):
                expansion_raw = {}

            def _as_int(value: Any, default: int) -> int:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return default

            expansion = {
                "target_count": max(1, min(_as_int(expansion_raw.get("target_count"), 50), 1000)),
                "date_range_days": max(1, min(_as_int(expansion_raw.get("date_range_days"), 90), 365)),
                "include_versions": bool(expansion_raw.get("include_versions", True)),
                "include_drafts": bool(expansion_raw.get("include_drafts", True)),
                "include_dates": bool(expansion_raw.get("include_dates", True)),
            }
            item["expansion"] = expansion

        if item_schema.__name__ == "DocumentSeed" and not item.get("document_type"):
            pattern = str(item.get("filename_pattern", "")).strip()
            extension = pattern.rsplit(".", 1)[-1].lower() if "." in pattern else "docx"
            item["document_type"] = extension

        return item
    
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
        
        # Unwrap nested wrapper if present (e.g., {"persona": {...}})
        if isinstance(data, dict) and len(data) == 1:
            key = list(data.keys())[0]
            if isinstance(data[key], dict) and key.lower() in schema.__name__.lower():
                data = data[key]

        if schema.__name__ == "PersonaContext" and isinstance(data, dict):
            data = self._normalize_persona_payload(data)

        if isinstance(data, dict) and "expansion" in schema.model_fields:
            expansion = data.get("expansion")
            if isinstance(expansion, dict):
                target_count = expansion.get("target_count")
                if isinstance(target_count, (int, float)):
                    expansion["target_count"] = max(1, min(int(target_count), 1000))
                data["expansion"] = expansion
        
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

        normalized_data = [
            self._normalize_list_item_payload(item, item_schema)
            for item in data
        ]
        
        try:
            return [item_schema.model_validate(item) for item in normalized_data]
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
