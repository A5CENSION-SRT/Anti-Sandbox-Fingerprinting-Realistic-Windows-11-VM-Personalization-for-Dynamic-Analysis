# Browser History Generation — Step-by-Step Guide

This document explains how the browser artifact generation pipeline works,
from raw data files to a finished Chrome/Edge `History` SQLite database.

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
                         ├─ 6. Populate search terms
                         └─ 7. Audit-log the created file
```

---

## Step 1 — URL Selection

**Module:** `utils/url_loader.py`

The `UrlLoader` reads `data/wordlists/urls_by_category.json` and filters
URLs matching the profile's `browsing.categories` list. The `general`
category is always included. URLs are deduplicated by full URL string.

Example: a `developer` profile with `categories: [stackoverflow, github, documentation]`
will receive ~70 URLs (general + 3 dev categories).

---

## Step 2 — Schema Initialisation

**Module:** `generators/schema.py`

The Chromium History database (schema version **46**) contains these tables:

| Table                    | Purpose                                       |
|--------------------------|-----------------------------------------------|
| `meta`                   | Schema version tracking                       |
| `urls`                   | One row per unique URL, with visit counts      |
| `visits`                 | One row per page load, linked to `urls.id`     |
| `keyword_search_terms`   | Search queries linked to search-engine URLs    |
| `segments` / `segment_usage` | Internal Chrome navigation tracking       |
| `downloads` / `downloads_url_chains` | Download history (populated in W2) |

The full `CREATE TABLE` + `CREATE INDEX` SQL is applied via
`conn.executescript()`.

---

## Step 3 — URL Record Insertion

**Module:** `generators/visit_generator.py` → `assign_visit_counts()`

Each URL gets a `visit_count` based on domain popularity:
- **High-traffic** (google, youtube, github, etc.): 10–50 visits
- **Others**: 1–15 visits

`typed_count` (how often the user typed the URL) is set to `visit_count // 3`
for ~70% of URLs. All counts use a seeded `random.Random(42)` for reproducibility.

---

## Step 4 — Day-by-Day Visit Generation

**Module:** `generators/visit_generator.py` → `compute_day_visits()`, `generate_visits_for_day()`

For each calendar day in the timeline (default: 90 days):

1. **Activity level** depends on day type:
   - **Active day** (in `work_hours.active_days`): `daily_avg ± 33%` visits
   - **Inactive day** (weekend for office users): `daily_avg / 4` visits (min 2)

2. **Sessions** are created (1–5 per day), each starting at a random
   minute offset within the `work_hours` window.

3. **URLs within a session** are selected using an **exponential distribution**
   (`expovariate(0.05)`), which creates a power-law effect: URLs listed
   earlier in the data file (popular sites) are visited more frequently.

---

## Step 5 — Visit Chain Insertion

**Module:** `generators/visit_generator.py` → `visit_transition()`, `visit_datetime()`

Each visit record includes:

| Field          | How it's set                                               |
|----------------|------------------------------------------------------------|
| `url`          | Foreign key to `urls.id`                                   |
| `visit_time`   | Chrome-epoch µs timestamp (see Step 6 below)               |
| `from_visit`   | Previous visit's ID → creates browsing session chains      |
| `transition`   | `TYPED` (1) for session start, `LINK` (0) for follow-ups   |
| `visit_duration`| Random 5–300 seconds (in µs)                              |

**Timestamp construction** (`visit_datetime()`):
- Base day + hour offset from `work_hours.start` + minute offset within session
- Seconds and microseconds randomised for entropy
- Converted to Chrome epoch via `datetime_to_chrome()`:
  `unix_µs + 11644473600 × 10⁶`

---

## Step 6 — Search Term Population

**Module:** `generators/search_term_generator.py`

Selects 20–60 search terms from `data/wordlists/search_terms.txt` and
links each to a URL ID belonging to a search engine domain
(Google, Bing, DuckDuckGo). The `keyword_id` is set to `2` (Chrome's
default search provider slot).

---

## Step 7 — Audit Logging

Every created file (SQLite DB, JSON config, directory) is recorded via
`AuditLogger.log()` with service name, operation type, full path, and
browser name for post-deployment traceability.

---

## Timeline Generation — Research & Approach

### What Malware Checks

Sophisticated malware inspects browser artifacts for these evasion signals:

| Signal                      | What it checks                                    |
|-----------------------------|---------------------------------------------------|
| **History density**         | `SELECT COUNT(*) FROM urls` — expects > 20–50     |
| **Visit spread**            | Timestamps should span weeks/months, not hours    |
| **Diurnal pattern**         | Activity clustered in daytime, quiet at night      |
| **Session chains**          | `from_visit > 0` links should exist               |
| **Search terms**            | Non-empty `keyword_search_terms` table             |
| **Wear-and-tear artifacts** | Cookies, bookmarks, downloads should co-exist      |

### Existing Approaches

| Technique                     | Used by                     | Our approach       |
|-------------------------------|-----------------------------|--------------------|
| Uniform random timestamps     | Basic sandboxes             | ❌ Easily detected  |
| Poisson arrival model         | Academic research           | ✅ Partially used   |
| Circadian/diurnal weighting   | Advanced sandbox hardening  | ✅ `work_hours` window |
| Power-law inter-event times   | Human behavior modeling     | ✅ Exponential URL selection |
| Deterministic seeded RNG      | Reproducibility requirement | ✅ `Random(42)`     |

### Why Our Approach Works

1. **Diurnal model**: Visits are constrained to `work_hours` (e.g., 9–17),
   with reduced activity on non-active days. This matches the circadian
   rhythm research showing humans browse in predictable daily cycles.

2. **Power-law URL selection**: `expovariate(0.05)` creates a heavy-tailed
   distribution — a few sites dominate visit frequency while most URLs
   have low visit counts. This matches real browsing behaviour.

3. **Session chains**: `from_visit` links create realistic navigation flows
   (typed URL → clicked link → clicked link), which is exactly what
   malware expects to see.

4. **Entropy**: Randomised seconds/microseconds add timestamp entropy,
   so visits don't fall on exact minute boundaries.

5. **Deterministic seed**: `Random(42)` ensures the same profile always
   produces the same history, critical for auditability and testing.

---

## File Reference

| File | Lines | Purpose |
|------|-------|---------|
| `browser_profile.py` | ~99 | Orchestrator: creates profile dirs + config JSONs |
| `history.py` | ~146 | Orchestrator: creates History SQLite DBs |
| `utils/chrome_timestamps.py` | ~34 | Chrome epoch ↔ datetime conversion |
| `utils/constants.py` | ~83 | Transition codes, browser paths, search engines |
| `utils/url_loader.py` | ~80 | Loads URLs and search terms from data files |
| `generators/schema.py` | ~107 | Full Chromium History DB schema SQL |
| `generators/config_generator.py` | ~120 | Local State, Preferences, Secure Preferences |
| `generators/bookmark_enricher.py` | ~96 | Loads & stamps bookmarks with IDs/timestamps |
| `generators/visit_generator.py` | ~116 | Day sessions, visit chains, transition logic |
| `generators/search_term_generator.py` | ~53 | Search term ↔ search-engine URL linking |
| `__init__.py` | ~6 | Package exports |
