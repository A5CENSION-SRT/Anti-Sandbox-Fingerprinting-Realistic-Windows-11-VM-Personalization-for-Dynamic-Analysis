# Browser History Generation — Step-by-Step Guide

This document explains how the browser artifact generation pipeline works,
from raw data files to a finished Chrome/Edge `History` SQLite database
and a populated `Downloads/` folder.

---

## Pipeline Overview

```
Profile YAML ──┐
               ├──► BrowserHistoryService.apply()
Data files ────┘         │
                         ├─ 1. Load URLs for profile categories
                         ├─ 2. Create SQLite DB with Chromium schema
                         ├─ 3. Insert URL records
                         ├─ 4. Generate day-by-day visit sessions
                         ├─ 5. Insert visit chains with timestamps
                         └─ 6. Populate search terms

               ──► BrowserDownloadService.apply()
                         │
                         ├─ 7. Select profile-specific downloads
                         ├─ 8. Create placeholder files in Downloads/
                         └─ 9. Insert SQLite download records + URL chains
```

---

## Step 1 — URL Selection

**Module:** `utils/url_loader.py`

`UrlLoader` reads `data/wordlists/urls_by_category.json` and filters
URLs matching the profile's `browsing.categories` list. The `general`
category is always included. URLs are deduplicated by full URL string.

---

## Step 2 — Schema Initialisation

**Module:** `generators/schema.py`

The Chromium History database (schema version **46**) contains these tables:

| Table | Purpose |
|-------|---------|
| `meta` | Schema version tracking |
| `urls` | One row per unique URL, with visit counts |
| `visits` | One row per page load, linked to `urls.id` |
| `keyword_search_terms` | Search queries linked to search-engine URLs |
| `segments` / `segment_usage` | Internal Chrome navigation tracking |
| `downloads` | One row per downloaded file |
| `downloads_url_chains` | Source URLs for each download |

---

## Step 3 — URL Record Insertion

**Module:** `generators/visit_generator.py` → `assign_visit_counts()`

Each URL gets a `visit_count` based on domain popularity:
- **High-traffic** (google, youtube, github, etc.): 10–50 visits
- **Others**: 1–15 visits

`typed_count` is set to `visit_count // 3` for ~70% of URLs.
All counts use a seeded `random.Random(42)` for reproducibility.

---

## Step 4 — Day-by-Day Visit Generation

**Module:** `generators/visit_generator.py` → `compute_day_visits()`, `generate_visits_for_day()`

For each calendar day in the timeline (default: 90 days):

1. **Activity level** by day type:
   - **Active day**: `daily_avg ± 33%` visits
   - **Inactive day** (e.g., weekend): `daily_avg / 4` visits (min 2)
2. **Sessions** created per day (1–5), each starting at a random minute offset within `work_hours`.
3. **URL selection** uses `expovariate(0.05)` — power-law bias toward popular sites.

---

## Step 5 — Visit Chain Insertion

**Module:** `generators/visit_generator.py` → `visit_transition()`, `visit_datetime()`

| Field | Value |
|-------|-------|
| `url` | FK to `urls.id` |
| `visit_time` | Chrome-epoch µs: `unix_µs + 11644473600 × 10⁶` |
| `from_visit` | Previous visit ID → session chain |
| `transition` | `TYPED` (1) for session start, `LINK` (0) for follow-ups |
| `visit_duration` | Random 5–300 s (in µs) |

---

## Step 6 — Search Term Population

**Module:** `generators/search_term_generator.py`

Selects 20–60 terms from `data/wordlists/search_terms.txt` and links
each to a search-engine URL (Google, Bing, DuckDuckGo) with `keyword_id=2`.

---

## Step 7 — Download Selection

**Module:** `generators/download_generator.py` → `select_downloads()`

Reads `data/wordlists/downloads_by_profile.json` and picks N entries
matching the active profile (`office_user`, `developer`, `home_user`).
Selection uses `random.Random(43)` (separate seed from visits).

**Profile-specific examples:**

| Profile | Example files |
|---------|--------------|
| `office_user` | `Q4_Financial_Report.pdf`, `Teams_installer.exe`, `budget_template.xlsx` |
| `developer` | `Python-3.12.2-amd64.exe`, `Docker Desktop Installer.exe`, `Git-2.43.0-64-bit.exe` |
| `home_user` | `Spotify-Setup.exe`, `discord-setup.exe`, `amazon_order_invoice.pdf` |

---

## Step 8 — Filesystem Stubs

**Module:** `generators/download_generator.py` → `create_placeholder_file()`

Zero-byte stub files are created in `Users/<username>/Downloads/` so that
both filesystem enumeration (`dir`, `os.listdir`) and shell preview tools
show genuine-looking download artifacts.

---

## Step 9 — SQLite Download Records

**Module:** `generators/download_generator.py` → `insert_download()`

Each download inserts into two tables:

**`downloads` row:**
| Column | Value |
|--------|-------|
| `guid` | Randomly generated UUID4 |
| `target_path` | `C:\Users\<name>\Downloads\<filename>` |
| `start_time` / `end_time` | Chrome-epoch timestamps (5–120 s apart) |
| `received_bytes` = `total_bytes` | From catalogue (realistic sizes) |
| `state` | `1` = COMPLETE |
| `danger_type` | `0` = NOT_DANGEROUS |
| `mime_type` | From catalogue (e.g., `application/pdf`) |
| `referrer` | Realistic referring URL |

**`downloads_url_chains` row:** Direct source URL at `chain_index=0`.

---

## Timeline Generation — Research & Approach

### What Malware Checks

| Signal | What it checks |
|--------|---------------|
| **History density** | `SELECT COUNT(*) FROM urls` — expects > 20–50 |
| **Visit spread** | Timestamps spanning weeks/months |
| **Diurnal pattern** | Activity in daytime, quiet at night |
| **Session chains** | `from_visit > 0` links |
| **Search terms** | Non-empty `keyword_search_terms` |
| **Downloads** | Non-empty `downloads` table + files in Downloads/ |
| **Wear-and-tear** | Cookies, bookmarks, downloads all co-existing |

### Our Timeline Approach

| Method | Evidence |
|--------|---------|
| **Diurnal (circadian) model** | `work_hours` window; reduced weekend activity |
| **Power-law URL selection** | `expovariate(0.05)` — few sites dominate |
| **Session chaining** | `from_visit` links: TYPED → LINK → LINK |
| **Entropy injection** | Random seconds/microseconds per visit |
| **Deterministic seed** | `Random(42)` for reproducibility |
| **Dual download artifacts** | SQLite rows + filesystem stubs |

---

## File Reference

| File | Lines | Purpose |
|------|-------|---------|
| `browser_profile.py` | ~99 | Orchestrator: profile dirs + config JSONs |
| `history.py` | ~146 | Orchestrator: History SQLite DB |
| `downloads.py` | ~140 | Orchestrator: download records + stubs |
| `utils/chrome_timestamps.py` | ~34 | Chrome epoch ↔ datetime |
| `utils/constants.py` | ~83 | Transition codes, browser paths |
| `utils/url_loader.py` | ~80 | Loads URLs and search terms |
| `generators/schema.py` | ~113 | Full Chromium History DB schema SQL |
| `generators/config_generator.py` | ~120 | Local State, Preferences, Secure Preferences |
| `generators/bookmark_enricher.py` | ~96 | Loads & enriches bookmark templates |
| `generators/visit_generator.py` | ~116 | Day sessions, visit chains, transitions |
| `generators/search_term_generator.py` | ~53 | Search term ↔ search-engine URL linking |
| `generators/download_generator.py` | ~104 | Download catalogue, insertion, file stubs |
| `__init__.py` | ~8 | Package exports |
