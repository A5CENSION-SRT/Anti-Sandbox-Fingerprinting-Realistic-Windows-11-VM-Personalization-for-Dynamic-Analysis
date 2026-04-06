"""Enriches bookmark JSON templates with timestamps and IDs.

Loads a profile-specific bookmark template, then recursively
adds ``id``, ``date_added``, and ``date_modified`` fields so
the output matches a genuine Chrome Bookmarks file.
"""

import json
from pathlib import Path

from services.browser.utils.constants import BOOKMARK_TEMPLATE_MAP


def load_and_enrich(templates_dir: Path, profile_name: str,
                    base_ts: int) -> dict:
    """Load a bookmark template and inject timestamps + IDs.

    Args:
        templates_dir: Directory containing bookmarks_*.json files.
        profile_name: Profile key (office_user / developer / home_user).
        base_ts: Chrome-epoch timestamp to use as the anchor.

    Returns:
        Complete bookmark dict ready to be written as JSON.
    """
    tpl_file = BOOKMARK_TEMPLATE_MAP.get(profile_name, "bookmarks_home.json")
    tpl_path = templates_dir / tpl_file

    if tpl_path.exists():
        with open(tpl_path, "r", encoding="utf-8") as fh:
            bookmarks = json.load(fh)
    else:
        bookmarks = _empty_bookmarks()

    _enrich_roots(bookmarks.get("roots", {}), base_ts)
    return bookmarks


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _empty_bookmarks() -> dict:
    """Fallback empty-but-valid bookmarks structure."""
    return {
        "checksum": "",
        "roots": {
            "bookmark_bar": {
                "children": [], "name": "Bookmarks bar", "type": "folder",
            },
            "other": {
                "children": [], "name": "Other bookmarks", "type": "folder",
            },
            "synced": {
                "children": [], "name": "Mobile bookmarks", "type": "folder",
            },
        },
        "version": 1,
    }


def _enrich_roots(roots: dict, base_ts: int) -> None:
    """Add id / date_added / date_modified to top-level root nodes."""
    counter = [1]
    for key in ("bookmark_bar", "other", "synced"):
        node = roots.get(key)
        if node is None:
            continue
        node.setdefault("id", str(counter[0]))
        counter[0] += 1
        node.setdefault("date_added", str(base_ts))
        node.setdefault("date_modified", str(base_ts + 1_000_000))
        _enrich_children(node, base_ts, counter)


def _enrich_children(node: dict, base_ts: int, counter: list) -> None:
    """Recursively add metadata to child nodes."""
    for i, child in enumerate(node.get("children", [])):
        child.setdefault("id", str(counter[0]))
        counter[0] += 1
        offset = i * 5_000_000  # 5 s apart
        child.setdefault("date_added", str(base_ts + offset))
        if child.get("type") == "folder":
            child.setdefault(
                "date_modified",
                str(base_ts + offset + 60_000_000),
            )
            _enrich_children(child, base_ts + offset, counter)
