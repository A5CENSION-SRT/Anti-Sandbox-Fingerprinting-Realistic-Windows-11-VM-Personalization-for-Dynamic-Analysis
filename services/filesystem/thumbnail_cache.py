"""Windows thumbnail cache (thumbcache) generator.

Creates thumbnail cache database files in the user's
AppData/Local/Microsoft/Windows/Explorer/ directory.

Thumbnail caches store preview images for files viewed in Explorer.
The presence of realistic thumbcache files indicates genuine user activity.
"""

from __future__ import annotations

import hashlib
import logging
import struct
from datetime import datetime, timezone
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Tuple

from services.base_service import BaseService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Thumbcache file magic signature
_THUMBCACHE_MAGIC = b"CMMM"  # Common Media Maestro
_THUMBCACHE_VERSION = 32  # Windows 10/11 version

# Thumbcache file types and their typical sizes
_THUMBCACHE_FILES: Dict[str, Tuple[int, int]] = {
    "thumbcache_16.db": (16384, 65536),
    "thumbcache_32.db": (32768, 131072),
    "thumbcache_48.db": (65536, 262144),
    "thumbcache_96.db": (131072, 524288),
    "thumbcache_256.db": (262144, 1048576),
    "thumbcache_768.db": (524288, 2097152),
    "thumbcache_1280.db": (1048576, 4194304),
    "thumbcache_1920.db": (2097152, 8388608),
    "thumbcache_2560.db": (4194304, 16777216),
    "thumbcache_sr.db": (65536, 262144),  # Super resolution
    "thumbcache_wide.db": (131072, 524288),
    "thumbcache_exif.db": (32768, 131072),
    "thumbcache_wide_alternate.db": (131072, 524288),
    "thumbcache_custom_stream.db": (32768, 131072),
    "thumbcache_idx.db": (16384, 65536),  # Index file
}

# Files expected per profile type
_PROFILE_CACHE_SIZES: Dict[str, float] = {
    "home_user": 1.0,      # Full size - lots of media
    "office_user": 0.5,    # Medium - some documents
    "developer": 0.3,      # Smaller - fewer visual files
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ThumbnailCacheError(Exception):
    """Raised when thumbnail cache generation fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ThumbnailCacheService(BaseService):
    """Creates Windows thumbnail cache database files.

    Generates thumbcache_*.db files in the Explorer folder with
    realistic binary structure and profile-appropriate sizes.

    Args:
        mount_manager: Resolves paths against the mounted image root.
        timestamp_service: Provides timestamps for file operations.
        audit_logger: Structured audit logging.
    """

    def __init__(
        self,
        mount_manager,
        timestamp_service,
        audit_logger,
    ) -> None:
        self._mount = mount_manager
        self._ts = timestamp_service
        self._audit = audit_logger

    @property
    def service_name(self) -> str:
        return "ThumbnailCacheService"

    def apply(self, context: dict) -> None:
        """Generate thumbnail cache files for the user profile.

        Args:
            context: Runtime context dict. Recognised keys:

                * ``username`` (str) — Windows username.
                * ``profile_type`` (str) — ``home_user`` / ``office_user`` / ``developer``.
                * ``computer_name`` (str) — used as RNG seed.

        Raises:
            ThumbnailCacheError: If cache generation fails.
        """
        username = context.get("username", "default_user")
        profile_type = context.get("profile_type", "home_user")
        seed = context.get("computer_name", username)

        rng = Random(hash(seed + profile_type))
        cache_dir = (
            Path("Users") / username / "AppData" / "Local"
            / "Microsoft" / "Windows" / "Explorer"
        )
        created_files = 0

        try:
            # Ensure cache directory exists
            full_cache_dir = self._mount.resolve(str(cache_dir))
            full_cache_dir.mkdir(parents=True, exist_ok=True)

            # Size multiplier for this profile
            size_mult = _PROFILE_CACHE_SIZES.get(profile_type, 0.5)

            for filename, size_range in _THUMBCACHE_FILES.items():
                # Skip some files randomly
                if rng.random() < 0.1:
                    continue

                # Adjust size based on profile
                min_size = int(size_range[0] * size_mult)
                max_size = int(size_range[1] * size_mult)
                min_size = max(min_size, 4096)  # Minimum 4KB

                # Generate cache content
                content = self._create_thumbcache_file(
                    filename, rng, (min_size, max_size)
                )

                file_path = cache_dir / filename
                self._write_file(file_path, content)
                created_files += 1

            # Create the index file
            idx_content = self._create_index_file(rng)
            self._write_file(cache_dir / "iconcache_idx.db", idx_content)
            created_files += 1

            self._audit.log({
                "service": self.service_name,
                "operation": "generate_thumbnail_cache",
                "username": username,
                "profile_type": profile_type,
                "files_created": created_files,
            })

            logger.info(
                "Generated %d thumbnail cache files for user '%s'",
                created_files, username,
            )

        except Exception as exc:
            logger.error("Failed to generate thumbnail cache: %s", exc)
            raise ThumbnailCacheError(
                f"Thumbnail cache generation failed: {exc}"
            ) from exc

    def _write_file(self, rel_path: Path, content: bytes) -> None:
        """Write file content to the mounted filesystem."""
        full_path = self._mount.resolve(str(rel_path))
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

        self._audit.log({
            "service": self.service_name,
            "operation": "create_file",
            "path": str(full_path),
            "size": len(content),
        })

    def _create_thumbcache_file(
        self,
        filename: str,
        rng: Random,
        size_range: Tuple[int, int],
    ) -> bytes:
        """Create a thumbcache database file.

        Args:
            filename: Cache filename (determines format).
            rng: Random number generator.
            size_range: (min_size, max_size) tuple.

        Returns:
            Binary content for the cache file.
        """
        target_size = rng.randint(*size_range)

        # Build header (24 bytes)
        header = bytearray(24)
        header[0:4] = _THUMBCACHE_MAGIC
        struct.pack_into("<I", header, 4, _THUMBCACHE_VERSION)
        struct.pack_into("<I", header, 8, 0)  # Cache type
        struct.pack_into("<I", header, 12, target_size)  # File size

        # First entry offset
        first_entry_offset = 24
        struct.pack_into("<I", header, 16, first_entry_offset)

        # Available entry offset
        struct.pack_into("<I", header, 20, target_size - 32)

        # Generate entries (simplified)
        entries = bytearray(target_size - 24)
        num_entries = min(target_size // 256, 1000)

        for i in range(num_entries):
            offset = i * 256
            if offset + 256 > len(entries):
                break

            # Entry header
            entry_header = bytearray(32)
            entry_header[0:4] = b"CMMM"  # Entry magic
            struct.pack_into("<Q", entry_header, 4, rng.randint(0, 2**64 - 1))  # Hash
            struct.pack_into("<I", entry_header, 12, 224)  # Data size
            struct.pack_into("<I", entry_header, 16, 0)  # Image width
            struct.pack_into("<I", entry_header, 20, 0)  # Image height
            struct.pack_into("<I", entry_header, 24, 0)  # Data checksum
            struct.pack_into("<I", entry_header, 28, 0)  # Header checksum

            entries[offset:offset + 32] = entry_header

            # Fill data area with pseudo-random bytes
            for j in range(32, 256):
                entries[offset + j] = rng.randint(0, 255)

        return bytes(header) + bytes(entries)

    def _create_index_file(self, rng: Random) -> bytes:
        """Create a thumbcache index file.

        Args:
            rng: Random number generator.

        Returns:
            Binary content for the index file.
        """
        # Header
        header = bytearray(28)
        header[0:4] = b"IMMM"  # Index magic
        struct.pack_into("<I", header, 4, _THUMBCACHE_VERSION)
        struct.pack_into("<I", header, 8, 0)  # Flags

        # Entry count
        entry_count = rng.randint(50, 500)
        struct.pack_into("<I", header, 12, entry_count)

        # Generate index entries
        entries = bytearray(entry_count * 16)
        for i in range(entry_count):
            offset = i * 16
            struct.pack_into("<Q", entries, offset, rng.randint(0, 2**64 - 1))  # Hash
            struct.pack_into("<I", entries, offset + 8, rng.randint(0, 10))  # Cache type
            struct.pack_into("<I", entries, offset + 12, rng.randint(0, 2**32 - 1))  # Offset

        return bytes(header) + bytes(entries)
