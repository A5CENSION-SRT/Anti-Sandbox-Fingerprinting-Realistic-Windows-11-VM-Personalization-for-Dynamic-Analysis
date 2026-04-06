"""Loads URL and search-term seed data from disk.

Provides lazy-loaded accessors so the data files are read only
once and shared across generator modules.
"""

import json
from pathlib import Path


class UrlLoader:
    """Loads and caches URLs and search terms from data files.

    Args:
        data_dir: Either the ``data`` directory or the
            ``data/wordlists`` directory. If the caller passes only ``data``,
            this loader will automatically fall back to ``data/wordlists``.
    """

    def __init__(self, data_dir: str | Path | None = None):
        if data_dir:
            self._dir = Path(data_dir)
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
            # Callers sometimes pass the parent `data/` directory while the
            # actual file lives in `data/wordlists/`.
            candidates = [
                self._dir / "urls_by_category.json",
                self._dir / "wordlists" / "urls_by_category.json",
                self._dir.parent / "urls_by_category.json",
            ]
            path: Path | None = next((p for p in candidates if p.exists()), None)
            if path is not None:
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
            candidates = [
                self._dir / "search_terms.txt",
                self._dir / "wordlists" / "search_terms.txt",
                self._dir.parent / "search_terms.txt",
            ]
            path: Path | None = next((p for p in candidates if p.exists()), None)
            if path is not None:
                with open(path, "r", encoding="utf-8") as fh:
                    self._terms = [
                        line.strip() for line in fh if line.strip()
                    ]
            else:
                self._terms = []
        return self._terms
