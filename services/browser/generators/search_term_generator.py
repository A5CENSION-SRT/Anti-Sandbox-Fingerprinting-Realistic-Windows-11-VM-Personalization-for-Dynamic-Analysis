"""Inserts keyword_search_terms rows into the History DB.

Links randomly selected search queries to URLs whose domain
matches a known search engine, so the resulting database looks
like a user who searched from the omnibox.
"""

import random
import sqlite3


def populate_search_terms(conn: sqlite3.Connection,
                          url_id_map: dict[str, int],
                          search_terms: list[str],
                          rng: random.Random) -> None:
    """Insert search-term entries linked to search-engine URLs.

    Args:
        conn: Open SQLite connection to the History database.
        url_id_map: Mapping of URL string → urls.id.
        search_terms: Full list of possible search queries.
        rng: Seeded Random instance for reproducibility.
    """
    if not search_terms:
        return

    # Collect url IDs that belong to search engines
    search_url_ids = [
        uid for url, uid in url_id_map.items()
        if any(se in url for se in (
            "google.com", "bing.com", "duckduckgo",
        ))
    ]
    if not search_url_ids:
        return

    count = rng.randint(
        min(20, len(search_terms)),
        min(60, len(search_terms)),
    )
    selected = rng.sample(search_terms, count)

    for term in selected:
        conn.execute(
            "INSERT INTO keyword_search_terms "
            "(keyword_id, url_id, term, normalized_term) "
            "VALUES (?, ?, ?, ?)",
            (2, rng.choice(search_url_ids), term, term.lower().strip()),
        )
