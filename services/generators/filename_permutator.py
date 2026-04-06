"""Combinatorial filename generator for massive-scale artifact creation.

Takes filename pattern seeds and expands them into thousands of unique
filenames through systematic permutation of variables, dates, versions,
and suffixes.
"""

from __future__ import annotations

import itertools
import logging
import re
from datetime import datetime, timedelta
from random import Random
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)


class FilenamePermutator:
    """Expands filename patterns into thousands of unique filenames.
    
    Given a pattern like "{project}_Report_{date}{suffix}.docx", generates:
    - ProjectAlpha_Report_20240115.docx
    - ProjectAlpha_Report_20240115_v2.docx  
    - ProjectAlpha_Report_20240115_DRAFT.docx
    - WebRedesign_Report_20240116.docx
    - ... (1000s of combinations)
    
    Args:
        seed: Seed value for reproducible randomness.
        timeline_days: Number of days to spread dates across.
    
    Example:
        >>> permutator = FilenamePermutator(seed=42)
        >>> filenames = permutator.expand_pattern(
        ...     pattern="{project}_Report_{date}.docx",
        ...     variables={"project": ["Alpha", "Beta"]},
        ...     target_count=100
        ... )
        >>> print(len(filenames))  # 100
    """
    
    # Regex to find {variable} placeholders
    _VAR_PATTERN = re.compile(r"\{(\w+)\}")
    
    def __init__(
        self,
        seed: int = 42,
        timeline_days: int = 90,
    ) -> None:
        self._rng = Random(seed)
        self._timeline_days = timeline_days
        self._base_date = datetime.now()
    
    def expand_pattern(
        self,
        pattern: str,
        variables: Dict[str, List[str]],
        target_count: int,
        date_formats: Optional[List[str]] = None,
        version_styles: Optional[List[str]] = None,
        suffix_options: Optional[List[str]] = None,
        include_versions: bool = True,
        include_dates: bool = True,
        include_suffixes: bool = True,
    ) -> List[str]:
        """Expand a pattern into multiple unique filenames.
        
        Args:
            pattern: Filename pattern with {variable} placeholders.
            variables: Dict of variable name → possible values.
            target_count: Target number of filenames to generate.
            date_formats: strftime format strings for {date} variable.
            version_styles: Version number formats (e.g., "v{n}").
            suffix_options: Filename suffixes (e.g., "_DRAFT", "_Final").
            include_versions: Whether to add version variations.
            include_dates: Whether to add date variations.
            include_suffixes: Whether to add suffix variations.
        
        Returns:
            List of unique filenames.
        """
        date_formats = date_formats or ["%Y%m%d", "%Y-%m-%d"]
        version_styles = version_styles or ["v{n}", "_v{n}"]
        suffix_options = suffix_options or ["", "_DRAFT", "_Final"]
        
        # Parse pattern to find all variables
        var_names = self._VAR_PATTERN.findall(pattern)
        
        # Build expansion sets for each variable
        expansion_sets: Dict[str, List[str]] = {}
        
        for var_name in var_names:
            if var_name == "date" and include_dates:
                # Generate dates across timeline
                expansion_sets[var_name] = self._generate_dates(
                    count=min(target_count // 5, 90),
                    formats=date_formats,
                )
            elif var_name == "counter":
                # Sequential counter (handled separately)
                expansion_sets[var_name] = ["COUNTER"]
            elif var_name == "time":
                expansion_sets[var_name] = self._generate_times(20)
            elif var_name in variables:
                expansion_sets[var_name] = variables[var_name]
            else:
                # Unknown variable - use placeholder
                expansion_sets[var_name] = [var_name.upper()]
        
        # Add suffix as a pseudo-variable if pattern doesn't have {suffix}
        if "{suffix}" not in pattern and include_suffixes:
            # Insert suffix before extension
            parts = pattern.rsplit(".", 1)
            if len(parts) == 2:
                pattern = f"{parts[0]}{{suffix}}.{parts[1]}"
                expansion_sets["suffix"] = suffix_options
        elif "{suffix}" in pattern:
            expansion_sets["suffix"] = suffix_options
        
        # Generate combinations
        filenames = []
        
        if "COUNTER" in expansion_sets.get("counter", []):
            # Counter-based pattern (e.g., IMG_20240115_0001.jpg)
            filenames = self._expand_with_counter(
                pattern=pattern,
                expansion_sets=expansion_sets,
                target_count=target_count,
            )
        else:
            # Combinatorial expansion
            filenames = self._expand_combinatorial(
                pattern=pattern,
                expansion_sets=expansion_sets,
                target_count=target_count,
            )
        
        # Add version variations if enabled
        if include_versions and version_styles:
            filenames = self._add_version_variations(
                filenames=filenames,
                version_styles=version_styles,
                target_count=target_count,
            )
        
        # Shuffle and trim to target
        self._rng.shuffle(filenames)
        return list(dict.fromkeys(filenames))[:target_count]  # Remove dupes, keep order
    
    def _expand_combinatorial(
        self,
        pattern: str,
        expansion_sets: Dict[str, List[str]],
        target_count: int,
    ) -> List[str]:
        """Generate filenames through combinatorial expansion."""
        var_names = self._VAR_PATTERN.findall(pattern)
        
        if not var_names:
            return [pattern]
        
        # Get value lists in order
        value_lists = [expansion_sets.get(var, ["UNKNOWN"]) for var in var_names]
        
        # Calculate all possible combinations
        total_combos = 1
        for vl in value_lists:
            total_combos *= len(vl)
        
        filenames = []
        
        if total_combos <= target_count * 2:
            # Generate all combinations
            for combo in itertools.product(*value_lists):
                filename = pattern
                for var_name, value in zip(var_names, combo):
                    filename = filename.replace(f"{{{var_name}}}", value)
                filenames.append(filename)
        else:
            # Sample randomly from the combination space
            seen = set()
            attempts = 0
            max_attempts = target_count * 3
            
            while len(filenames) < target_count and attempts < max_attempts:
                combo = tuple(self._rng.choice(vl) for vl in value_lists)
                if combo not in seen:
                    seen.add(combo)
                    filename = pattern
                    for var_name, value in zip(var_names, combo):
                        filename = filename.replace(f"{{{var_name}}}", value)
                    filenames.append(filename)
                attempts += 1
        
        return filenames
    
    def _expand_with_counter(
        self,
        pattern: str,
        expansion_sets: Dict[str, List[str]],
        target_count: int,
    ) -> List[str]:
        """Generate filenames with sequential counters."""
        # Remove counter from expansion sets (handled specially)
        expansion_sets = {k: v for k, v in expansion_sets.items() if k != "counter"}
        
        # Get other variable combinations
        var_names = [v for v in self._VAR_PATTERN.findall(pattern) if v != "counter"]
        value_lists = [expansion_sets.get(var, ["UNKNOWN"]) for var in var_names]
        
        filenames = []
        counter = 1
        
        if not value_lists:
            # No other variables - just counter
            for i in range(target_count):
                filename = pattern.replace("{counter}", f"{counter:04d}")
                filenames.append(filename)
                counter += 1
        else:
            # Combine with other variables
            combos = list(itertools.product(*value_lists))
            files_per_combo = max(1, target_count // len(combos))
            
            for combo in combos:
                for _ in range(files_per_combo):
                    if len(filenames) >= target_count:
                        break
                    filename = pattern
                    for var_name, value in zip(var_names, combo):
                        filename = filename.replace(f"{{{var_name}}}", value)
                    filename = filename.replace("{counter}", f"{counter:04d}")
                    filenames.append(filename)
                    counter += 1
        
        return filenames
    
    def _add_version_variations(
        self,
        filenames: List[str],
        version_styles: List[str],
        target_count: int,
    ) -> List[str]:
        """Add version number variations to filenames."""
        result = filenames.copy()
        
        # Add versions to a subset of files
        version_ratio = 0.3  # 30% of files get versions
        files_to_version = self._rng.sample(
            filenames,
            min(int(len(filenames) * version_ratio), len(filenames)),
        )
        
        for filename in files_to_version:
            if len(result) >= target_count:
                break
            
            # Split extension
            parts = filename.rsplit(".", 1)
            if len(parts) != 2:
                continue
            
            base, ext = parts
            
            # Add 1-4 versions
            num_versions = self._rng.randint(1, 4)
            style = self._rng.choice(version_styles)
            
            for v in range(2, num_versions + 2):
                version_str = style.format(n=v)
                versioned = f"{base}{version_str}.{ext}"
                result.append(versioned)
        
        return result
    
    def _generate_dates(
        self,
        count: int,
        formats: List[str],
    ) -> List[str]:
        """Generate date strings spread across the timeline."""
        dates = []
        
        for _ in range(count):
            days_ago = self._rng.randint(0, self._timeline_days)
            date = self._base_date - timedelta(days=days_ago)
            fmt = self._rng.choice(formats)
            dates.append(date.strftime(fmt))
        
        return list(set(dates))
    
    def _generate_times(self, count: int) -> List[str]:
        """Generate time strings."""
        times = []
        
        for _ in range(count):
            hour = self._rng.randint(0, 23)
            minute = self._rng.randint(0, 59)
            second = self._rng.randint(0, 59)
            times.append(f"{hour:02d}{minute:02d}{second:02d}")
        
        return list(set(times))
    
    def expand_from_seed(
        self,
        seed: "FilenameSeed",
    ) -> List[str]:
        """Expand a FilenameSeed into filenames.
        
        Args:
            seed: FilenameSeed instance with pattern and rules.
        
        Returns:
            List of generated filenames.
        """
        return self.expand_pattern(
            pattern=seed.pattern,
            variables=seed.variables,
            target_count=seed.expansion.target_count,
            date_formats=seed.date_formats,
            version_styles=seed.version_styles,
            suffix_options=seed.suffix_options,
            include_versions=seed.expansion.include_versions,
            include_dates=seed.expansion.include_dates,
            include_suffixes=seed.expansion.include_drafts,
        )


# Convenience function
def expand_filename_pattern(
    pattern: str,
    variables: Dict[str, List[str]],
    target_count: int = 100,
    seed: int = 42,
) -> List[str]:
    """Convenience function to expand a single pattern.
    
    Args:
        pattern: Filename pattern with {variable} placeholders.
        variables: Dict of variable name → possible values.
        target_count: Target number of filenames.
        seed: Random seed for reproducibility.
    
    Returns:
        List of unique filenames.
    """
    permutator = FilenamePermutator(seed=seed)
    return permutator.expand_pattern(
        pattern=pattern,
        variables=variables,
        target_count=target_count,
    )
