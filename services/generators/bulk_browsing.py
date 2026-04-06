"""Bulk browser history generator for massive-scale browsing artifacts.

Expands browsing seeds into 5000-10000 history entries with realistic
visit patterns, search terms, and bookmark structures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from random import Random
from typing import Any, Dict, List, Optional, Tuple

from services.ai.schemas import BrowsingSeed, BrowsingPatternSeed, PersonaContext, VisitFrequency

logger = logging.getLogger(__name__)


@dataclass
class ExpandedHistoryEntry:
    """A single browser history entry."""
    
    url: str
    title: str
    visit_time: datetime
    visit_duration_seconds: int
    transition_type: int  # 0=link, 1=typed, 2=bookmark, 3=redirect
    from_visit_id: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "visit_time": self.visit_time.isoformat(),
            "visit_duration_seconds": self.visit_duration_seconds,
            "transition_type": self.transition_type,
        }


@dataclass
class ExpandedSearchTerm:
    """An expanded search term with timestamp."""
    
    term: str
    search_time: datetime
    url_id: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "term": self.term,
            "search_time": self.search_time.isoformat(),
        }


@dataclass
class ExpandedBookmark:
    """An expanded bookmark entry."""
    
    title: str
    url: str
    folder: str
    date_added: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "folder": self.folder,
            "date_added": self.date_added.isoformat(),
        }


class BulkBrowsingGenerator:
    """Expands browsing seeds into thousands of history entries.
    
    Generates:
    - History entries with realistic visit patterns
    - Search terms matching persona interests
    - Bookmarks organized by category
    
    Args:
        seed: Random seed for reproducibility.
        timeline_days: Days to spread visits across.
        target_history: Target number of history entries.
        target_searches: Target number of search terms.
    
    Example:
        >>> generator = BulkBrowsingGenerator(target_history=7500)
        >>> history, searches, bookmarks = generator.expand_seed(seed, persona)
    """
    
    # Visits per day by frequency
    _FREQUENCY_VISITS = {
        VisitFrequency.MULTIPLE_DAILY: (3, 8),
        VisitFrequency.DAILY: (1, 2),
        VisitFrequency.WEEKLY: (0.14, 0.28),  # ~1-2 per week
        VisitFrequency.BIWEEKLY: (0.07, 0.14),
        VisitFrequency.MONTHLY: (0.03, 0.05),
        VisitFrequency.OCCASIONAL: (0.01, 0.03),
    }
    
    def __init__(
        self,
        seed: int = 42,
        timeline_days: int = 90,
        target_history: int = 7500,
        target_searches: int = 1500,
    ) -> None:
        self._rng = Random(seed)
        self._timeline_days = timeline_days
        self._target_history = target_history
        self._target_searches = target_searches
        self._base_date = datetime.now(timezone.utc)
    
    def expand_seed(
        self,
        seed: BrowsingSeed,
        persona: PersonaContext,
    ) -> Tuple[List[ExpandedHistoryEntry], List[ExpandedSearchTerm], List[ExpandedBookmark]]:
        """Expand browsing seed into history, searches, and bookmarks.
        
        Args:
            seed: BrowsingSeed instance from AI generator.
            persona: PersonaContext for work patterns.
        
        Returns:
            Tuple of (history_entries, search_terms, bookmarks).
        """
        history = self._expand_history(seed.url_patterns, persona)
        searches = self._expand_search_terms(seed.search_term_themes, persona)
        bookmarks = self._expand_bookmarks(seed.bookmark_categories, persona)
        
        logger.info(
            "Expanded browsing seed: %d history, %d searches, %d bookmarks",
            len(history), len(searches), len(bookmarks)
        )
        
        return history, searches, bookmarks
    
    def _expand_history(
        self,
        url_patterns: List[BrowsingPatternSeed],
        persona: PersonaContext,
    ) -> List[ExpandedHistoryEntry]:
        """Expand URL patterns into history entries."""
        history: List[ExpandedHistoryEntry] = []
        
        for pattern in url_patterns:
            entries = self._expand_url_pattern(pattern, persona)
            history.extend(entries)
        
        # Sort by time
        history.sort(key=lambda x: x.visit_time)
        
        # Trim to target
        if len(history) > self._target_history:
            # Sample to keep distribution
            indices = sorted(self._rng.sample(range(len(history)), self._target_history))
            history = [history[i] for i in indices]
        
        return history
    
    def _expand_url_pattern(
        self,
        pattern: BrowsingPatternSeed,
        persona: PersonaContext,
    ) -> List[ExpandedHistoryEntry]:
        """Expand a single URL pattern into visits."""
        entries = []
        
        # Calculate visits based on frequency
        min_rate, max_rate = self._FREQUENCY_VISITS.get(
            pattern.frequency, (0.5, 1.0)
        )
        daily_rate = self._rng.uniform(min_rate, max_rate)
        total_visits = int(daily_rate * self._timeline_days)
        
        # Parse typical visit times
        time_ranges = self._parse_time_ranges(pattern.typical_times)
        
        for _ in range(total_visits):
            # Pick a random day
            days_ago = self._rng.randint(0, self._timeline_days)
            visit_date = self._base_date - timedelta(days=days_ago)
            
            # Check if it's an active day for this type of site
            day_of_week = visit_date.isoweekday()
            is_work_day = day_of_week in persona.active_days
            is_work_site = self._is_work_site(pattern.url)
            
            # Skip work sites on off days (with some probability)
            if is_work_site and not is_work_day and self._rng.random() > 0.2:
                continue
            
            # Pick a time from the typical ranges
            if time_ranges:
                start_hour, end_hour = self._rng.choice(time_ranges)
            elif is_work_site and is_work_day:
                start_hour = persona.work_hours_start
                end_hour = persona.work_hours_end
            else:
                start_hour = 8
                end_hour = 23
            
            hour = self._rng.randint(start_hour, min(end_hour, 23))
            minute = self._rng.randint(0, 59)
            second = self._rng.randint(0, 59)
            
            visit_time = visit_date.replace(hour=hour, minute=minute, second=second)
            
            # Duration based on site type
            duration = self._estimate_visit_duration(pattern.url)
            
            # Transition type
            transition = self._pick_transition_type(pattern.url)
            
            entries.append(ExpandedHistoryEntry(
                url=pattern.url,
                title=pattern.title,
                visit_time=visit_time,
                visit_duration_seconds=duration,
                transition_type=transition,
            ))
        
        return entries
    
    def _parse_time_ranges(
        self,
        time_strs: List[str],
    ) -> List[Tuple[int, int]]:
        """Parse time range strings like '09:00-17:00' into hour tuples."""
        ranges = []
        for ts in time_strs:
            try:
                parts = ts.split("-")
                if len(parts) == 2:
                    start = int(parts[0].split(":")[0])
                    end = int(parts[1].split(":")[0])
                    ranges.append((start, end))
            except (ValueError, IndexError):
                continue
        return ranges
    
    def _is_work_site(self, url: str) -> bool:
        """Determine if URL is work-related."""
        work_indicators = [
            "office", "teams", "slack", "sharepoint", "outlook",
            "salesforce", "jira", "confluence", "github.com/pulls",
            "github.com/issues", "zoom.us/meeting",
        ]
        url_lower = url.lower()
        return any(ind in url_lower for ind in work_indicators)
    
    def _estimate_visit_duration(self, url: str) -> int:
        """Estimate visit duration in seconds based on site type."""
        url_lower = url.lower()
        
        if any(x in url_lower for x in ["youtube", "netflix", "twitch"]):
            return self._rng.randint(300, 3600)  # 5 min - 1 hour
        elif any(x in url_lower for x in ["mail", "gmail", "outlook"]):
            return self._rng.randint(30, 300)  # 30 sec - 5 min
        elif any(x in url_lower for x in ["google.com/search", "bing.com/search"]):
            return self._rng.randint(5, 60)  # 5 sec - 1 min
        elif any(x in url_lower for x in ["reddit", "twitter", "facebook"]):
            return self._rng.randint(60, 600)  # 1-10 min
        elif any(x in url_lower for x in ["stackoverflow", "github"]):
            return self._rng.randint(60, 900)  # 1-15 min
        else:
            return self._rng.randint(30, 300)  # Default 30 sec - 5 min
    
    def _pick_transition_type(self, url: str) -> int:
        """Pick a transition type based on URL characteristics."""
        url_lower = url.lower()
        
        # Typed URLs (direct entry)
        if any(x in url_lower for x in ["google.com/", "gmail.com", "github.com/", "amazon.com/"]):
            if self._rng.random() < 0.3:
                return 1  # TYPED
        
        # Bookmarked sites
        if any(x in url_lower for x in ["mail", "calendar", "docs.google"]):
            if self._rng.random() < 0.4:
                return 2  # BOOKMARK
        
        # Default to link click
        return 0  # LINK
    
    def _expand_search_terms(
        self,
        themes: List[str],
        persona: PersonaContext,
    ) -> List[ExpandedSearchTerm]:
        """Expand search term themes into actual search queries."""
        searches = []
        searches_per_theme = max(1, self._target_searches // len(themes)) if themes else 0
        
        for theme in themes:
            expanded = self._expand_search_theme(theme, persona, searches_per_theme)
            searches.extend(expanded)
        
        # Shuffle and trim
        self._rng.shuffle(searches)
        return searches[:self._target_searches]
    
    def _expand_search_theme(
        self,
        theme: str,
        persona: PersonaContext,
        count: int,
    ) -> List[ExpandedSearchTerm]:
        """Expand a single search theme into multiple queries."""
        searches = []
        
        # Extract variables from theme
        variables = self._extract_theme_variables(theme, persona)
        
        for _ in range(count):
            # Generate search term from theme
            term = self._instantiate_search_theme(theme, variables)
            
            # Generate search time
            days_ago = self._rng.randint(0, self._timeline_days)
            date = self._base_date - timedelta(days=days_ago)
            hour = self._rng.randint(persona.work_hours_start, 22)
            search_time = date.replace(hour=hour, minute=self._rng.randint(0, 59))
            
            searches.append(ExpandedSearchTerm(
                term=term,
                search_time=search_time,
            ))
        
        return searches
    
    def _extract_theme_variables(
        self,
        theme: str,
        persona: PersonaContext,
    ) -> Dict[str, List[str]]:
        """Extract variables for search theme expansion."""
        return {
            "task": ["create", "fix", "setup", "configure", "debug", "optimize"],
            "tool": persona.work_style.typical_tools,
            "product": ["laptop", "monitor", "keyboard", "headphones", "chair"],
            "topic": persona.interests.professional_topics,
            "category": persona.interests.hobbies,
            "library": ["pandas", "numpy", "react", "django", "express"],
            "error_message": ["not working", "failed", "error", "crash"],
            "use_case": ["home office", "gaming", "work", "travel"],
            "show": persona.interests.entertainment,
            "destination": ["japan", "italy", "mexico", "hawaii", "paris"],
            "recipe": ["pasta", "chicken", "salad", "soup", "dessert"],
            "software": persona.work_style.typical_tools,
            "command": ["merge", "rebase", "push", "pull", "clone", "commit"],
            "function": ["vlookup", "pivot table", "if", "sum", "count"],
            "industry": persona.interests.professional_topics,
        }
    
    def _instantiate_search_theme(
        self,
        theme: str,
        variables: Dict[str, List[str]],
    ) -> str:
        """Instantiate a search theme with actual values."""
        import re
        
        def replace_var(match):
            var_name = match.group(1)
            if var_name in variables and variables[var_name]:
                return self._rng.choice(variables[var_name])
            return match.group(0)
        
        return re.sub(r"\{(\w+)\}", replace_var, theme)
    
    def _expand_bookmarks(
        self,
        categories: Dict[str, List[str]],
        persona: PersonaContext,
    ) -> List[ExpandedBookmark]:
        """Expand bookmark categories into bookmark entries."""
        bookmarks = []
        
        # Date bookmarks were added (spread across last 6 months)
        for folder, urls in categories.items():
            for url in urls:
                days_ago = self._rng.randint(30, 180)
                date_added = self._base_date - timedelta(days=days_ago)
                
                # Generate title from URL
                title = self._url_to_title(url)
                
                bookmarks.append(ExpandedBookmark(
                    title=title,
                    url=url,
                    folder=folder,
                    date_added=date_added,
                ))
        
        return bookmarks
    
    def _url_to_title(self, url: str) -> str:
        """Generate a page title from URL."""
        from urllib.parse import urlparse
        
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")
        
        # Common domain → title mappings
        titles = {
            "github.com": "GitHub",
            "stackoverflow.com": "Stack Overflow",
            "google.com": "Google",
            "mail.google.com": "Gmail",
            "drive.google.com": "Google Drive",
            "amazon.com": "Amazon",
            "youtube.com": "YouTube",
            "reddit.com": "Reddit",
            "linkedin.com": "LinkedIn",
            "twitter.com": "X (Twitter)",
            "facebook.com": "Facebook",
            "netflix.com": "Netflix",
        }
        
        for key, title in titles.items():
            if key in domain:
                return title
        
        # Fallback: capitalize domain
        return domain.split(".")[0].title()
