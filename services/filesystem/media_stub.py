"""Media file stub generator for realistic filesystem artifacts.

Creates minimal but valid media files (images, audio, video) in the user's
Pictures, Videos, and Music folders. Files have valid headers to pass
basic file-type detection while being small placeholders.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Tuple

from services.base_service import BaseService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimal 1x1 pixel JPEG (red)
_JPEG_1X1 = bytes([
    0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
    0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
    0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08, 0x07, 0x07, 0x07, 0x09,
    0x09, 0x08, 0x0A, 0x0C, 0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
    0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D, 0x1A, 0x1C, 0x1C, 0x20,
    0x24, 0x2E, 0x27, 0x20, 0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
    0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27, 0x39, 0x3D, 0x38, 0x32,
    0x3C, 0x2E, 0x33, 0x34, 0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
    0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4, 0x00, 0x1F, 0x00, 0x00,
    0x01, 0x05, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
    0x09, 0x0A, 0x0B, 0xFF, 0xC4, 0x00, 0xB5, 0x10, 0x00, 0x02, 0x01, 0x03,
    0x03, 0x02, 0x04, 0x03, 0x05, 0x05, 0x04, 0x04, 0x00, 0x00, 0x01, 0x7D,
    0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
    0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xA1, 0x08,
    0x23, 0x42, 0xB1, 0xC1, 0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72,
    0x82, 0x09, 0x0A, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x25, 0x26, 0x27, 0x28,
    0x29, 0x2A, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45,
    0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
    0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75,
    0x76, 0x77, 0x78, 0x79, 0x7A, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
    0x8A, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3,
    0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6,
    0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9,
    0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8, 0xD9, 0xDA, 0xE1, 0xE2,
    0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF1, 0xF2, 0xF3, 0xF4,
    0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA, 0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01,
    0x00, 0x00, 0x3F, 0x00, 0xFB, 0xD5, 0xDB, 0x20, 0xA8, 0xF1, 0x45, 0x00,
    0x14, 0x51, 0x40, 0x1F, 0xFF, 0xD9,
])

# Minimal PNG header (1x1 pixel)
_PNG_1X1 = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  # PNG signature
    0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,  # IHDR chunk
    0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,  # 1x1 pixels
    0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
    0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41,  # IDAT chunk
    0x54, 0x08, 0xD7, 0x63, 0xF8, 0xFF, 0xFF, 0x3F,
    0x00, 0x05, 0xFE, 0x02, 0xFE, 0xDC, 0xCC, 0x59,
    0xE7, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E,  # IEND chunk
    0x44, 0xAE, 0x42, 0x60, 0x82,
])

# Minimal MP3 header (ID3v2 + silent frame)
_MP3_HEADER = bytes([
    0x49, 0x44, 0x33,  # ID3
    0x04, 0x00,        # Version 2.4.0
    0x00,              # Flags
    0x00, 0x00, 0x00, 0x00,  # Size
    0xFF, 0xFB, 0x90, 0x00,  # MP3 frame header
])

# Minimal MP4/M4A header
_MP4_HEADER = bytes([
    0x00, 0x00, 0x00, 0x18,  # ftyp box size
    0x66, 0x74, 0x79, 0x70,  # 'ftyp'
    0x69, 0x73, 0x6F, 0x6D,  # 'isom'
    0x00, 0x00, 0x02, 0x00,
    0x69, 0x73, 0x6F, 0x6D,  # 'isom'
    0x69, 0x73, 0x6F, 0x32,  # 'iso2'
])

# Profile-specific media files
_PROFILE_MEDIA: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
    "home_user": {
        "Pictures": [
            {"name": "IMG_20240115_vacation.jpg", "type": "jpg", "size": (50000, 200000)},
            {"name": "IMG_20240220_family.jpg", "type": "jpg", "size": (40000, 150000)},
            {"name": "screenshot_2024.png", "type": "png", "size": (100000, 500000)},
            {"name": "photo_editing.png", "type": "png", "size": (80000, 300000)},
            {"name": "wallpaper.jpg", "type": "jpg", "size": (500000, 2000000)},
        ],
        "Videos": [
            {"name": "birthday_2024.mp4", "type": "mp4", "size": (5000000, 20000000)},
            {"name": "vacation_highlights.mp4", "type": "mp4", "size": (10000000, 50000000)},
        ],
        "Music": [
            {"name": "favorite_song.mp3", "type": "mp3", "size": (3000000, 8000000)},
            {"name": "playlist_track.mp3", "type": "mp3", "size": (4000000, 10000000)},
        ],
    },
    "office_user": {
        "Pictures": [
            {"name": "company_logo.png", "type": "png", "size": (20000, 80000)},
            {"name": "team_photo.jpg", "type": "jpg", "size": (100000, 400000)},
            {"name": "presentation_chart.png", "type": "png", "size": (50000, 150000)},
        ],
        "Videos": [
            {"name": "training_video.mp4", "type": "mp4", "size": (20000000, 100000000)},
        ],
        "Music": [],
    },
    "developer": {
        "Pictures": [
            {"name": "architecture_diagram.png", "type": "png", "size": (200000, 800000)},
            {"name": "screenshot_debug.png", "type": "png", "size": (100000, 400000)},
            {"name": "ui_mockup.png", "type": "png", "size": (150000, 600000)},
        ],
        "Videos": [
            {"name": "demo_recording.mp4", "type": "mp4", "size": (10000000, 50000000)},
        ],
        "Music": [],
    },
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class MediaStubError(Exception):
    """Raised when media stub generation fails."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class MediaStubService(BaseService):
    """Creates minimal but valid media file stubs.

    Generates placeholder image, audio, and video files with valid headers
    to pass file-type detection while keeping storage requirements minimal.

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
        return "MediaStubService"

    def apply(self, context: dict) -> None:
        """Generate media stubs for the user profile.

        Args:
            context: Runtime context dict. Recognised keys:

                * ``username`` (str) — Windows username.
                * ``profile_type`` (str) — ``home_user`` / ``office_user`` / ``developer``.
                * ``computer_name`` (str) — used as RNG seed.

        Raises:
            MediaStubError: If stub creation fails.
        """
        username = context.get("username", "default_user")
        profile_type = context.get("profile_type", "home_user")
        seed = context.get("computer_name", username)

        rng = Random(hash(seed + profile_type))
        user_root = Path("Users") / username
        created_files = 0

        try:
            media_config = _PROFILE_MEDIA.get(profile_type, {})

            for folder, files in media_config.items():
                for file_spec in files:
                    # Randomly skip some files for variety
                    if rng.random() < 0.15:
                        continue

                    file_path = user_root / folder / file_spec["name"]
                    file_type = file_spec["type"]
                    size_range = file_spec.get("size", (10000, 50000))

                    content = self._generate_media_stub(file_type, rng, size_range)
                    self._write_file(file_path, content)
                    created_files += 1

            self._audit.log({
                "service": self.service_name,
                "operation": "generate_media_stubs",
                "username": username,
                "profile_type": profile_type,
                "files_created": created_files,
            })

            logger.info(
                "Generated %d media stubs for user '%s' (%s profile)",
                created_files, username, profile_type,
            )

        except Exception as exc:
            logger.error("Failed to generate media stubs: %s", exc)
            raise MediaStubError(f"Media stub generation failed: {exc}") from exc

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

    def _generate_media_stub(
        self,
        file_type: str,
        rng: Random,
        size_range: Tuple[int, int],
    ) -> bytes:
        """Generate a media file stub with valid header.

        Args:
            file_type: File type (jpg, png, mp3, mp4).
            rng: Random number generator.
            size_range: (min_size, max_size) tuple.

        Returns:
            Binary content for the file.
        """
        target_size = rng.randint(*size_range)

        if file_type == "jpg":
            # Use minimal JPEG, pad to target size
            header = _JPEG_1X1
        elif file_type == "png":
            header = _PNG_1X1
        elif file_type == "mp3":
            header = _MP3_HEADER
        elif file_type == "mp4":
            header = _MP4_HEADER
        else:
            header = b"\x00" * 16

        # Pad with zeros to reach target size
        if len(header) < target_size:
            padding = b"\x00" * (target_size - len(header))
            return header + padding
        return header[:target_size]
