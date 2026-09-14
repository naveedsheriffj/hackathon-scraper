# HackerEarth Ongoing & Upcoming Challenges Scraper

An accurate, anti-bot resilient web scraper for [HackerEarth Challenges](https://www.hackerearth.com/challenges/) powered by the [Scrapling](https://github.com/D4Vinci/Scrapling) framework and integrated with Supabase PostgreSQL.

---

## Features

- **Ongoing & Upcoming Only**: Exclusively filters and extracts currently active (`Ongoing`) and scheduled (`Upcoming`) hackathons and competitions. All past/concluded challenges are automatically excluded.
- **Dynamic Detail Page Extraction**: Parses Next.js React Server Component (RSC) streams, HackerEarth Event APIs, and detail landing pages for complete structured metadata without brittle positional assumptions.
- **Zero Hallucination Policy**: All fields are strictly mapped from real source data. Missing or unannounced values are strictly recorded as `None` / `null` in JSON and `"N/A"` in CSV. No placeholder or fabricated data.
- **Supabase PostgreSQL Synchronization**: Idempotent upserting directly to the Supabase `hackathons` table using `source_url` for deduplication. Supports both `supabase-py` client and standard library `urllib` PostgREST HTTP fallback.
- **Robust Parsing Standards**:
  - **Dates**: Normalized to strict ISO `YYYY-MM-DD` (supports date ranges, en/em dashes, ordinal suffixes).
  - **Team Sizes**: Dynamic integer range parsing (`team_size_min`, `team_size_max`).
  - **Prizes**: Preserves original string, calculates normalized numeric `prize_amount` and ISO `currency` code (`INR`, `USD`, `EUR`, `GBP`), with intelligent Indian denomination support (Lakh, Crore).
  - **Location & Event Mode**: Disambiguates `"Online (Online)"` into `location = "Online"` and `event_mode = "Online"`, while detecting physical offline locations.
  - **Tags**: Deduplicated string arrays preserving original meaningful text.

---

## 17 Standardized Extracted Fields

| Field | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `title` | `TEXT` | Full hackathon title | `Code Kitchen` |
| `organizer` | `TEXT` | Hosting company / organization | `AIM` |
| `status` | `TEXT` | Real-time status (`Ongoing`, `Upcoming`, etc.) | `Ongoing` |
| `location` | `TEXT` | Specific city / venue or "Online" | `Bangalore` |
| `event_mode` | `TEXT` | Mode (`Online`, `Offline`, `Hybrid`) | `Offline` |
| `start_date` | `DATE` | ISO formatted start date (`YYYY-MM-DD`) | `2026-08-24` |
| `end_date` | `DATE` | ISO formatted end date (`YYYY-MM-DD`) | `2026-09-15` |
| `registration_deadline` | `DATE` | ISO registration deadline (`YYYY-MM-DD`) | `null` |
| `team_size_min` | `INT` | Minimum allowed team members | `1` |
| `team_size_max` | `INT` | Maximum allowed team members | `1` |
| `prize` | `TEXT` | Original displayed prize string | `₹10 Lakh prize pool` |
| `prize_amount` | `NUMERIC` | Normalized numeric prize value | `1000000` |
| `currency` | `TEXT` | ISO currency code (`INR`, `USD`, etc.) | `INR` |
| `tags` | `TEXT[]` | Clean, deduplicated skill and category tags | `["live", "team"]` |
| `source_url` | `TEXT` | Canonical detail page URL (Unique key) | `https://www.hackerearth.com/challenges/hackathon/code-kitchen/` |
| `source_website` | `TEXT` | Source website domain | `hackerearth.com` |
| `scraped_at` | `TIMESTAMPTZ`| UTC timestamp of extraction | `2026-09-14T13:55:04Z` |

---

## Supabase Database Migration

If you are setting up the database table or adding newly supported columns, run the migration script in your [Supabase SQL Editor](https://supabase.com/dashboard/project/_/sql):

```sql
-- See supabase_migration.sql in the project root
```

The migration includes:
- Creation of `hackathons` table if not already present.
- Safe `ALTER TABLE hackathons ADD COLUMN IF NOT EXISTS` for existing tables.
- Unique index on `source_url` (`idx_hackathons_source_url`) ensuring idempotent deduplication during scraper runs.
- Query indexes for `source_website`, `status`, `start_date`, and `registration_deadline`.
- Row Level Security (RLS) policies for read and write access.

---

## Configuration (`.env`)

Create a `.env` file in the project root (or copy from `.env.example`):

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-supabase-anon-or-service-role-key
SUPABASE_TABLE=hackathons
```

> **Note**: If `SUPABASE_URL` and `SUPABASE_KEY` are not set, the scraper will log an informative notice and safely persist all extracted data to `hackerearth_challenges.json` and `hackerearth_challenges.csv`.

---

## Running the Scraper

Run the scraper using the virtual environment Python interpreter:

```powershell
.\.venv\Scripts\python.exe scraper.py
```

### Output Files
1. [`hackerearth_challenges.json`](file:///c:/Users/naveed%20sheriff%20j/Desktop/HackerEarth/hackerearth_challenges.json): Complete JSON dataset with all 17 standardized fields plus extended metadata.
2. [`hackerearth_challenges.csv`](file:///c:/Users/naveed%20sheriff%20j/Desktop/HackerEarth/hackerearth_challenges.csv): Tabular spreadsheet format with `"N/A"` for missing fields.

---

## Running Tests

Execute the automated test suite covering date parsing, prize calculations, team sizes, location disambiguation, tag cleanup, validation, and deduplication:

```powershell
.\.venv\Scripts\python.exe test_scraper.py
.\.venv\Scripts\python.exe test_dedupe.py
```
