"""Bulk media file generator for pictures, videos, and music.

Expands media seeds into hundreds of unique media file stubs with
realistic filenames, timestamps, and proper file headers.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional

from services.ai.schemas import MediaEventCluster, MediaSeed, MediaType, PersonaContext

logger = logging.getLogger(__name__)


# Minimal valid file headers
_JPEG_HEADER = bytes([
    0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
    0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
])

_PNG_HEADER = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
    0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
])

_MP3_HEADER = bytes([
    0x49, 0x44, 0x33, 0x04, 0x00, 0x00,  # ID3v2.4
    0x00, 0x00, 0x00, 0x00,
    0xFF, 0xFB, 0x90, 0x00,  # MP3 frame
])

_MP4_HEADER = bytes([
    0x00, 0x00, 0x00, 0x18,
    0x66, 0x74, 0x79, 0x70,  # 'ftyp'
    0x69, 0x73, 0x6F, 0x6D,  # 'isom'
    0x00, 0x00, 0x02, 0x00,
    0x69, 0x73, 0x6F, 0x6D,
    0x69, 0x73, 0x6F, 0x32,
])

_HEADERS_BY_EXT = {
    "jpg": _JPEG_HEADER,
    "jpeg": _JPEG_HEADER,
    "png": _PNG_HEADER,
    "mp3": _MP3_HEADER,
    "m4a": _MP4_HEADER,
    "mp4": _MP4_HEADER,
    "mov": _MP4_HEADER,
    "avi": b"RIFF\x00\x00\x00\x00AVI ",
    "mkv": b"\x1A\x45\xDF\xA3",
    "wav": b"RIFF\x00\x00\x00\x00WAVE",
    "flac": b"fLaC",
}


@dataclass
class ExpandedMediaFile:
    """A fully expanded media file artifact."""
    
    filename: str
    relative_path: str
    media_type: MediaType
    extension: str
    content: bytes
    size_bytes: int
    created_time: datetime
    modified_time: datetime
    context: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "filename": self.filename,
            "relative_path": self.relative_path,
            "media_type": self.media_type.value,
            "extension": self.extension,
            "size_bytes": self.size_bytes,
            "created_time": self.created_time.isoformat(),
            "modified_time": self.modified_time.isoformat(),
            "context": self.context,
        }


class BulkMediaGenerator:
    """Expands media seeds into hundreds of unique media files.
    
    Generates:
    - Photos: IMG_YYYYMMDD_NNNN.jpg with event clustering
    - Videos: event_date.mp4 with realistic patterns
    - Music: Artist - Track.mp3 from persona preferences
    
    Args:
        seed: Random seed for reproducibility.
        timeline_days: Days to spread media across.
    
    Example:
        >>> generator = BulkMediaGenerator()
        >>> media = generator.expand_seeds(seeds, persona)
        >>> photos = [m for m in media if m.media_type == MediaType.PHOTO]
    """
    
    def __init__(
        self,
        seed: int = 42,
        timeline_days: int = 90,
    ) -> None:
        self._rng = Random(seed)
        self._timeline_days = timeline_days
        self._base_date = datetime.now(timezone.utc)
    
    def expand_seeds(
        self,
        seeds: List[MediaSeed],
        persona: PersonaContext,
    ) -> List[ExpandedMediaFile]:
        """Expand all media seeds into files."""
        all_media: List[ExpandedMediaFile] = []
        
        for seed in seeds:
            if seed.media_type == MediaType.PHOTO:
                expanded = self._expand_photo_seed(seed, persona)
            elif seed.media_type == MediaType.VIDEO:
                expanded = self._expand_video_seed(seed, persona)
            elif seed.media_type == MediaType.MUSIC:
                expanded = self._expand_music_seed(seed, persona)
            elif seed.media_type == MediaType.SCREENSHOT:
                expanded = self._expand_screenshot_seed(seed, persona)
            else:
                expanded = []
            
            all_media.extend(expanded)
            logger.debug("Expanded %s seed → %d files", seed.media_type.value, len(expanded))
        
        self._rng.shuffle(all_media)
        logger.info("Expanded %d media seeds → %d total files", len(seeds), len(all_media))
        
        return all_media
    
    def _expand_photo_seed(
        self,
        seed: MediaSeed,
        persona: PersonaContext,
    ) -> List[ExpandedMediaFile]:
        """Expand photo seed with event clustering."""
        photos = []
        
        # Process event clusters (vacations, gatherings, etc.)
        for cluster in seed.event_clusters:
            cluster_photos = self._generate_event_photos(
                cluster=cluster,
                extension=seed.extension,
                size_range=seed.size_range_bytes,
            )
            photos.extend(cluster_photos)
        
        # Add random photos spread across timeline
        random_photos = self._generate_random_photos(
            count=seed.random_file_count,
            extension=seed.extension,
            size_range=seed.size_range_bytes,
        )
        photos.extend(random_photos)
        
        return photos
    
    def _generate_event_photos(
        self,
        cluster: MediaEventCluster,
        extension: str,
        size_range: tuple[int, int],
    ) -> List[ExpandedMediaFile]:
        """Generate photos for a specific event."""
        photos = []
        
        start_date = datetime.combine(cluster.date_start, datetime.min.time()).replace(tzinfo=timezone.utc)
        end_date = datetime.combine(
            cluster.date_end or cluster.date_start,
            datetime.max.time()
        ).replace(tzinfo=timezone.utc)
        
        total_days = max(1, (end_date - start_date).days + 1)
        photos_per_day = max(1, cluster.file_count // total_days)
        
        counter = 1
        current_date = start_date
        
        for day in range(total_days):
            day_date = start_date + timedelta(days=day)
            
            # Cluster photos at different times of day
            for _ in range(photos_per_day):
                if counter > cluster.file_count:
                    break
                
                hour = self._rng.choice([9, 10, 11, 12, 14, 15, 16, 17, 18, 19])
                minute = self._rng.randint(0, 59)
                photo_time = day_date.replace(hour=hour, minute=minute)
                
                filename = cluster.naming_pattern.format(
                    date=day_date.strftime("%Y%m%d"),
                    counter=f"{counter:04d}",
                )
                if not filename.endswith(f".{extension}"):
                    filename = f"{filename}.{extension}"
                
                content = self._generate_media_content(extension, size_range)
                
                photos.append(ExpandedMediaFile(
                    filename=filename,
                    relative_path=f"Pictures/{filename}",
                    media_type=MediaType.PHOTO,
                    extension=extension,
                    content=content,
                    size_bytes=len(content),
                    created_time=photo_time,
                    modified_time=photo_time,
                    context=cluster.event_name,
                ))
                
                counter += 1
        
        return photos
    
    def _generate_random_photos(
        self,
        count: int,
        extension: str,
        size_range: tuple[int, int],
    ) -> List[ExpandedMediaFile]:
        """Generate random photos spread across timeline."""
        photos = []
        
        for i in range(count):
            days_ago = self._rng.randint(0, self._timeline_days)
            date = self._base_date - timedelta(days=days_ago)
            hour = self._rng.randint(8, 22)
            photo_time = date.replace(hour=hour, minute=self._rng.randint(0, 59))
            
            filename = f"IMG_{date.strftime('%Y%m%d')}_{i+1:04d}.{extension}"
            content = self._generate_media_content(extension, size_range)
            
            photos.append(ExpandedMediaFile(
                filename=filename,
                relative_path=f"Pictures/{filename}",
                media_type=MediaType.PHOTO,
                extension=extension,
                content=content,
                size_bytes=len(content),
                created_time=photo_time,
                modified_time=photo_time,
                context="random_photo",
            ))
        
        return photos
    
    def _expand_video_seed(
        self,
        seed: MediaSeed,
        persona: PersonaContext,
    ) -> List[ExpandedMediaFile]:
        """Expand video seed."""
        videos = []
        
        for cluster in seed.event_clusters:
            videos.extend(self._generate_event_videos(
                cluster=cluster,
                extension=seed.extension,
                size_range=seed.size_range_bytes,
            ))
        
        # Add random videos
        for i in range(seed.random_file_count):
            days_ago = self._rng.randint(0, self._timeline_days)
            date = self._base_date - timedelta(days=days_ago)
            
            filename = f"VID_{date.strftime('%Y%m%d')}_{i+1:03d}.{seed.extension}"
            content = self._generate_media_content(seed.extension, seed.size_range_bytes)
            
            videos.append(ExpandedMediaFile(
                filename=filename,
                relative_path=f"Videos/{filename}",
                media_type=MediaType.VIDEO,
                extension=seed.extension,
                content=content,
                size_bytes=len(content),
                created_time=date,
                modified_time=date,
                context="random_video",
            ))
        
        return videos
    
    def _generate_event_videos(
        self,
        cluster: MediaEventCluster,
        extension: str,
        size_range: tuple[int, int],
    ) -> List[ExpandedMediaFile]:
        """Generate videos for a specific event."""
        videos = []
        
        start_date = datetime.combine(cluster.date_start, datetime.min.time()).replace(tzinfo=timezone.utc)
        
        for i in range(cluster.file_count):
            day_offset = i % max(1, (cluster.date_end - cluster.date_start).days + 1) if cluster.date_end else 0
            video_date = start_date + timedelta(days=day_offset)
            
            filename = f"{cluster.event_name}_{video_date.strftime('%Y%m%d')}_{i+1:02d}.{extension}"
            content = self._generate_media_content(extension, size_range)
            
            videos.append(ExpandedMediaFile(
                filename=filename,
                relative_path=f"Videos/{filename}",
                media_type=MediaType.VIDEO,
                extension=extension,
                content=content,
                size_bytes=len(content),
                created_time=video_date,
                modified_time=video_date,
                context=cluster.event_name,
            ))
        
        return videos
    
    def _expand_music_seed(
        self,
        seed: MediaSeed,
        persona: PersonaContext,
    ) -> List[ExpandedMediaFile]:
        """Expand music seed with artist/album structure."""
        music = []
        target = seed.expansion.target_count
        
        # Generate tracks from artists and albums
        for artist in seed.artists:
            for album in seed.albums:
                # 8-14 tracks per album
                track_count = self._rng.randint(8, 14)
                for track_num in range(1, track_count + 1):
                    if len(music) >= target:
                        break
                    
                    track_name = f"Track {track_num:02d}"
                    filename = f"{artist} - {track_name}.{seed.extension}"
                    
                    content = self._generate_media_content(seed.extension, seed.size_range_bytes)
                    
                    # Music files have older dates typically
                    days_ago = self._rng.randint(30, 365)
                    file_date = self._base_date - timedelta(days=days_ago)
                    
                    music.append(ExpandedMediaFile(
                        filename=filename,
                        relative_path=f"Music/{artist}/{album}/{filename}",
                        media_type=MediaType.MUSIC,
                        extension=seed.extension,
                        content=content,
                        size_bytes=len(content),
                        created_time=file_date,
                        modified_time=file_date,
                        context=f"{artist} - {album}",
                    ))
        
        return music[:target]
    
    def _expand_screenshot_seed(
        self,
        seed: MediaSeed,
        persona: PersonaContext,
    ) -> List[ExpandedMediaFile]:
        """Expand screenshot seed."""
        screenshots = []
        
        for i in range(seed.random_file_count):
            days_ago = self._rng.randint(0, self._timeline_days)
            date = self._base_date - timedelta(days=days_ago)
            hour = self._rng.randint(persona.work_hours_start, persona.work_hours_end)
            
            screenshot_time = date.replace(hour=hour, minute=self._rng.randint(0, 59))
            
            filename = f"Screenshot_{date.strftime('%Y-%m-%d')}_{screenshot_time.strftime('%H%M%S')}.png"
            content = self._generate_media_content("png", seed.size_range_bytes)
            
            screenshots.append(ExpandedMediaFile(
                filename=filename,
                relative_path=f"Pictures/Screenshots/{filename}",
                media_type=MediaType.SCREENSHOT,
                extension="png",
                content=content,
                size_bytes=len(content),
                created_time=screenshot_time,
                modified_time=screenshot_time,
                context="screenshot",
            ))
        
        return screenshots
    
    def _generate_media_content(
        self,
        extension: str,
        size_range: tuple[int, int],
    ) -> bytes:
        """Generate file content with proper header and size."""
        header = _HEADERS_BY_EXT.get(extension.lower(), b"")
        target_size = self._rng.randint(*size_range)
        
        # Pad with zeros to reach target size
        padding_size = max(0, target_size - len(header))
        return header + (b"\x00" * padding_size)
    
    def create_filesystem_media(
        self,
        media: List[ExpandedMediaFile],
        user_dir: Path,
    ) -> int:
        """Create media files in the filesystem."""
        created = 0
        
        for m in media:
            file_path = user_dir / m.relative_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            if file_path.exists():
                continue
            
            try:
                file_path.write_bytes(m.content)
                created += 1
            except OSError as e:
                logger.warning("Failed to create media file %s: %s", file_path, e)
        
        logger.info("Created %d media files under %s", created, user_dir)
        return created
