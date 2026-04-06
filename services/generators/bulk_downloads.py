"""Bulk downloads generator that expands seeds into thousands of download artifacts.

Takes 10-30 download seeds from the AI generator and expands them into
500-2000 unique download files using filename permutation and realistic
metadata generation.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional

from services.ai.schemas import DownloadSeed, PersonaContext
from services.generators.filename_permutator import FilenamePermutator

logger = logging.getLogger(__name__)


@dataclass
class ExpandedDownload:
    """A fully expanded download artifact ready for creation."""
    
    filename: str
    url: str
    referrer: str
    mime_type: str
    size_bytes: int
    download_time: datetime
    completed_time: datetime
    context: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "filename": self.filename,
            "url": self.url,
            "referrer": self.referrer,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "download_time": self.download_time.isoformat(),
            "completed_time": self.completed_time.isoformat(),
            "context": self.context,
        }


class BulkDownloadsGenerator:
    """Expands download seeds into thousands of unique downloads.
    
    Takes AI-generated download seeds and uses permutation to create:
    - Unique filenames with dates, versions, and variations
    - Realistic download timestamps spread across the timeline
    - Proper file sizes within the seed's range
    - Matching URLs and referrers
    
    Args:
        seed: Random seed for reproducibility.
        timeline_days: Days to spread downloads across.
        target_total: Target total number of downloads to generate.
    
    Example:
        >>> generator = BulkDownloadsGenerator(target_total=1500)
        >>> downloads = generator.expand_seeds(seeds, persona)
        >>> print(len(downloads))  # ~1500
    """
    
    def __init__(
        self,
        seed: int = 42,
        timeline_days: int = 90,
        target_total: int = 1500,
    ) -> None:
        self._rng = Random(seed)
        self._timeline_days = timeline_days
        self._target_total = target_total
        self._filename_permutator = FilenamePermutator(seed=seed, timeline_days=timeline_days)
        self._base_date = datetime.now(timezone.utc)
    
    def expand_seeds(
        self,
        seeds: List[DownloadSeed],
        persona: PersonaContext,
    ) -> List[ExpandedDownload]:
        """Expand all download seeds into complete download records.
        
        Args:
            seeds: List of DownloadSeed instances from AI generator.
            persona: PersonaContext for work hours and patterns.
        
        Returns:
            List of ExpandedDownload instances.
        """
        if not seeds:
            logger.warning("No download seeds provided")
            return []
        
        # Calculate how many downloads per seed
        downloads_per_seed = self._target_total // len(seeds)
        remainder = self._target_total % len(seeds)
        
        all_downloads: List[ExpandedDownload] = []
        
        for i, seed in enumerate(seeds):
            # Distribute remainder across first seeds
            target_count = seed.expansion.target_count
            if target_count <= 0:
                target_count = downloads_per_seed + (1 if i < remainder else 0)
            
            expanded = self._expand_single_seed(seed, persona, target_count)
            all_downloads.extend(expanded)
            
            logger.debug(
                "Expanded seed '%s' → %d downloads",
                seed.seed_id, len(expanded)
            )
        
        # Shuffle for realistic order
        self._rng.shuffle(all_downloads)
        
        logger.info(
            "Expanded %d seeds → %d total downloads",
            len(seeds), len(all_downloads)
        )
        
        return all_downloads[:self._target_total]
    
    def _expand_single_seed(
        self,
        seed: DownloadSeed,
        persona: PersonaContext,
        target_count: int,
    ) -> List[ExpandedDownload]:
        """Expand a single download seed into multiple downloads."""
        # Generate filenames using permutator
        filenames = self._filename_permutator.expand_pattern(
            pattern=seed.filename_pattern,
            variables=seed.variables,
            target_count=target_count,
            include_versions=seed.expansion.include_versions,
            include_dates=seed.expansion.include_dates,
            include_suffixes=False,  # Downloads don't usually have _DRAFT
        )
        
        downloads = []
        
        for filename in filenames:
            # Generate URL from template
            url = self._expand_url(seed.url_template, seed.variables, filename)
            referrer = self._expand_url(seed.referrer_template, seed.variables, filename)
            
            # Generate realistic file size
            size_bytes = self._rng.randint(*seed.size_range_bytes)
            
            # Generate download timestamp within persona's active hours
            download_time = self._generate_download_time(persona)
            
            # Completion time is download time + time based on file size
            download_duration = self._estimate_download_duration(size_bytes)
            completed_time = download_time + download_duration
            
            downloads.append(ExpandedDownload(
                filename=filename,
                url=url,
                referrer=referrer,
                mime_type=seed.mime_type,
                size_bytes=size_bytes,
                download_time=download_time,
                completed_time=completed_time,
                context=seed.context,
            ))
        
        return downloads
    
    def _expand_url(
        self,
        url_template: str,
        variables: Dict[str, List[str]],
        filename: str,
    ) -> str:
        """Expand URL template with variables."""
        url = url_template
        
        # Replace variables
        for var_name, values in variables.items():
            if f"{{{var_name}}}" in url:
                url = url.replace(f"{{{var_name}}}", self._rng.choice(values))
        
        # Replace {filename} if present
        url = url.replace("{filename}", filename)
        
        return url
    
    def _generate_download_time(
        self,
        persona: PersonaContext,
    ) -> datetime:
        """Generate a realistic download timestamp."""
        # Random day within timeline
        days_ago = self._rng.randint(0, self._timeline_days)
        date = self._base_date - timedelta(days=days_ago)
        
        # Check if this day is an active day
        day_of_week = date.isoweekday()
        is_active_day = day_of_week in persona.active_days
        
        if is_active_day:
            # Work hours download
            hour = self._rng.randint(persona.work_hours_start, persona.work_hours_end - 1)
        else:
            # Off-hours download (evenings/weekends)
            hour = self._rng.choice([8, 9, 10, 19, 20, 21, 22])
        
        minute = self._rng.randint(0, 59)
        second = self._rng.randint(0, 59)
        
        return date.replace(hour=hour, minute=minute, second=second, microsecond=0)
    
    def _estimate_download_duration(self, size_bytes: int) -> timedelta:
        """Estimate download duration based on file size."""
        # Assume 10-50 Mbps connection
        speed_mbps = self._rng.uniform(10, 50)
        speed_bytes_per_sec = speed_mbps * 125000  # Mbps to bytes/sec
        
        seconds = size_bytes / speed_bytes_per_sec
        # Add some variance
        seconds *= self._rng.uniform(0.8, 1.5)
        
        # Minimum 1 second, maximum 10 minutes
        seconds = max(1, min(seconds, 600))
        
        return timedelta(seconds=seconds)
    
    def create_filesystem_stubs(
        self,
        downloads: List[ExpandedDownload],
        downloads_dir: Path,
    ) -> int:
        """Create placeholder files in the Downloads directory.
        
        Args:
            downloads: List of ExpandedDownload instances.
            downloads_dir: Path to the Downloads directory.
        
        Returns:
            Number of files created.
        """
        downloads_dir.mkdir(parents=True, exist_ok=True)
        created = 0
        
        for dl in downloads:
            file_path = downloads_dir / dl.filename
            
            # Avoid overwriting existing files
            if file_path.exists():
                continue
            
            # Create zero-byte stub (or small content for realism)
            try:
                file_path.touch()
                created += 1
            except OSError as e:
                logger.warning("Failed to create download stub %s: %s", file_path, e)
        
        logger.info("Created %d download stubs in %s", created, downloads_dir)
        return created
