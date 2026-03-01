"""Shared constants for browser artifact generation.

Centralises browser paths, Chromium page-transition codes,
search engine patterns, and profile-to-template mappings so
every module references a single source of truth.
"""

import os

# ---------------------------------------------------------------
# Chromium page-transition types (stored in visits.transition)
# ---------------------------------------------------------------
TRANSITION_LINK = 0
TRANSITION_TYPED = 1
TRANSITION_AUTO_BOOKMARK = 2
TRANSITION_AUTO_SUBFRAME = 3
TRANSITION_MANUAL_SUBFRAME = 4
TRANSITION_GENERATED = 5       # e.g. search-result click
TRANSITION_START_PAGE = 6
TRANSITION_FORM_SUBMIT = 7
TRANSITION_RELOAD = 8
TRANSITION_KEYWORD = 9
TRANSITION_KEYWORD_GENERATED = 10

# ---------------------------------------------------------------
# Search engine URL prefixes
# ---------------------------------------------------------------
SEARCH_ENGINE_PREFIXES = [
    "https://www.google.com/search",
    "https://www.bing.com/search",
    "https://duckduckgo.com/",
]

# ---------------------------------------------------------------
# High-traffic domains (get more visit_count)
# ---------------------------------------------------------------
HIGH_TRAFFIC_DOMAINS = [
    "google.com", "youtube.com", "facebook.com", "reddit.com",
    "twitter.com", "github.com", "stackoverflow.com",
]

# ---------------------------------------------------------------
# Browser definitions: (display_name, relative User Data path)
# ---------------------------------------------------------------
BROWSERS = [
    (
        "Google Chrome",
        os.path.join("AppData", "Local", "Google", "Chrome", "User Data"),
    ),
    (
        "Microsoft Edge",
        os.path.join("AppData", "Local", "Microsoft", "Edge", "User Data"),
    ),
]

# ---------------------------------------------------------------
# Profile-name → bookmark template filename mapping
# ---------------------------------------------------------------
BOOKMARK_TEMPLATE_MAP = {
    "office_user": "bookmarks_office.json",
    "developer": "bookmarks_developer.json",
    "home_user": "bookmarks_home.json",
}

# ---------------------------------------------------------------
# Sub-directories inside a Default profile folder
# ---------------------------------------------------------------
PROFILE_SUBDIRS = [
    "Network",
    "Cache",
    "Code Cache",
    "GPUCache",
    "Session Storage",
    "Local Storage",
    "IndexedDB",
    "Extension State",
    "Extensions",
]
