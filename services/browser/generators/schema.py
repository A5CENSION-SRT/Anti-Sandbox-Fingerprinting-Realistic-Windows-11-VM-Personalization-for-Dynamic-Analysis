"""Chrome History SQLite database schema.

Contains the full CREATE TABLE / CREATE INDEX SQL that
matches a real Chromium History database (schema version 46).
"""

HISTORY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT NOT NULL UNIQUE PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    visit_count INTEGER NOT NULL DEFAULT 0,
    typed_count INTEGER NOT NULL DEFAULT 0,
    last_visit_time INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url INTEGER NOT NULL,
    visit_time INTEGER NOT NULL,
    from_visit INTEGER NOT NULL DEFAULT 0,
    transition INTEGER NOT NULL DEFAULT 0,
    segment_id INTEGER NOT NULL DEFAULT 0,
    visit_duration INTEGER NOT NULL DEFAULT 0,
    incremented_omnibox_typed_score INTEGER NOT NULL DEFAULT 0,
    opener_visit INTEGER NOT NULL DEFAULT 0,
    originator_cache_guid TEXT NOT NULL DEFAULT '',
    originator_visit_id INTEGER NOT NULL DEFAULT 0,
    originator_from_visit INTEGER NOT NULL DEFAULT 0,
    originator_opener_visit INTEGER NOT NULL DEFAULT 0,
    is_known_to_sync INTEGER NOT NULL DEFAULT 0,
    consider_for_ntp_most_visited INTEGER NOT NULL DEFAULT 0,
    externally_visited INTEGER NOT NULL DEFAULT 0,
    visited_link_id INTEGER NOT NULL DEFAULT 0,
    app_id TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (url) REFERENCES urls(id)
);

CREATE TABLE IF NOT EXISTS keyword_search_terms (
    keyword_id INTEGER NOT NULL,
    url_id INTEGER NOT NULL,
    term TEXT NOT NULL,
    normalized_term TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    url_id INTEGER
);

CREATE TABLE IF NOT EXISTS segment_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id INTEGER NOT NULL,
    time_slot INTEGER NOT NULL,
    visit_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guid TEXT NOT NULL,
    current_path TEXT NOT NULL DEFAULT '',
    target_path TEXT NOT NULL DEFAULT '',
    start_time INTEGER NOT NULL DEFAULT 0,
    received_bytes INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    state INTEGER NOT NULL DEFAULT 0,
    danger_type INTEGER NOT NULL DEFAULT 0,
    interrupt_reason INTEGER NOT NULL DEFAULT 0,
    hash BLOB NOT NULL DEFAULT x'',
    end_time INTEGER NOT NULL DEFAULT 0,
    opened INTEGER NOT NULL DEFAULT 0,
    last_access_time INTEGER NOT NULL DEFAULT 0,
    transient INTEGER NOT NULL DEFAULT 0,
    referrer TEXT NOT NULL DEFAULT '',
    site_url TEXT NOT NULL DEFAULT '',
    tab_url TEXT NOT NULL DEFAULT '',
    tab_referrer_url TEXT NOT NULL DEFAULT '',
    http_method TEXT NOT NULL DEFAULT 'GET',
    by_ext_id TEXT NOT NULL DEFAULT '',
    by_ext_name TEXT NOT NULL DEFAULT '',
    etag TEXT NOT NULL DEFAULT '',
    last_modified TEXT NOT NULL DEFAULT '',
    mime_type TEXT NOT NULL DEFAULT '',
    original_mime_type TEXT NOT NULL DEFAULT '',
    embedder_download_data TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS downloads_url_chains (
    id INTEGER NOT NULL,
    chain_index INTEGER NOT NULL,
    url TEXT NOT NULL,
    PRIMARY KEY (id, chain_index)
);

CREATE INDEX IF NOT EXISTS urls_url_index ON urls (url);
CREATE INDEX IF NOT EXISTS visits_url_index ON visits (url);
CREATE INDEX IF NOT EXISTS visits_time_index ON visits (visit_time);
CREATE INDEX IF NOT EXISTS keyword_search_terms_index1
    ON keyword_search_terms (keyword_id, term);
CREATE INDEX IF NOT EXISTS keyword_search_terms_index2
    ON keyword_search_terms (url_id);
"""

SCHEMA_VERSION = "46"
LAST_COMPATIBLE_VERSION = "16"
