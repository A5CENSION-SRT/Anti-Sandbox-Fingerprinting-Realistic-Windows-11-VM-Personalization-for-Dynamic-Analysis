"""Loads URL and search-term seed data from disk.

Provides lazy-loaded accessors so the data files are read only
once and shared across generator modules.
"""

import json
from pathlib import Path


class UrlLoader:
    """Loads and caches URLs and search terms from data files.

    Args:
        data_dir: Path to the ``data/wordlists/`` directory.
    """

    def __init__(self, data_dir: str | Path | None = None):
        if data_dir:
            candidate = Path(data_dir)
            # Accept both `data/wordlists` and project-level `data`.
            if (candidate / "urls_by_category.json").exists() or (
                candidate / "search_terms.txt"
            ).exists():
                self._dir = candidate
            elif (candidate / "wordlists").is_dir():
                self._dir = candidate / "wordlists"
            else:
                self._dir = candidate
        else:
            self._dir = (
                Path(__file__).resolve().parent.parent.parent.parent
                / "data" / "wordlists"
            )
        self._urls: dict | None = None
        self._terms: list[str] | None = None

    # ----- URLs by category ------------------------------------------

    def load_urls(self) -> dict[str, list[dict]]:
        """Return the full URL catalogue, keyed by category."""
        if self._urls is None:
            path = self._dir / "urls_by_category.json"
            if path.exists():
                with open(path, "r", encoding="utf-8") as fh:
                    self._urls = json.load(fh)
            else:
                self._urls = {}
        return self._urls

    def urls_for_categories(self, categories: list[str]) -> list[dict]:
        """Return deduplicated URLs matching the given categories.

        ``general`` is always included.
        """
        all_urls = self.load_urls()
        selected: list[dict] = []

        if "general" in all_urls:
            selected.extend(all_urls["general"])

        for cat in categories:
            if cat != "general" and cat in all_urls:
                selected.extend(all_urls[cat])

        seen: set[str] = set()
        unique: list[dict] = []
        for entry in selected:
            if entry["url"] not in seen:
                seen.add(entry["url"])
                unique.append(entry)
        return unique

    # ----- Search terms ----------------------------------------------

    def load_search_terms(self) -> list[str]:
        """Return all search-term strings."""
        if self._terms is None:
            path = self._dir / "search_terms.txt"
            if path.exists():
                with open(path, "r", encoding="utf-8") as fh:
                    self._terms = [
                        line.strip() for line in fh if line.strip()
                    ]
            else:
                self._terms = []
        return self._terms
