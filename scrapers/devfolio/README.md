# Devfolio Hackathons Scraper (using Scrapling)

A high-performance, undetectable web scraper built using Python and [D4Vinci/Scrapling](https://github.com/D4Vinci/Scrapling) to extract hackathon listings and detailed event information from [Devfolio Hackathons](https://devfolio.co/hackathons).

---

## Features

- **Built with Scrapling**: Uses Scrapling's `Fetcher` engine equipped with `curl_cffi` for realistic TLS/JA3 browser fingerprinting to evade bot detection.
- **Categorized Extraction**: Accurately extracts hackathons across all sections:
  - **Featured Hero**
  - **Open Hackathons**
  - **Upcoming Hackathons**
  - **Past Hackathons**
- **Structured Detail Page Fields**:
  - `title`: Hackathon title
  - `organizer`: Extracted organizer / host name (or `null` if absent)
  - `status`: "Open" / "Live" / "Upcoming" / "Ended" / "Closed"
  - `location`: Specific venue or city location (or "Online")
  - `event_mode`: "Online" / "Offline" / "Hybrid"
  - `start_date`: ISO format `YYYY-MM-DD`
  - `end_date`: ISO format `YYYY-MM-DD` (or `null`)
  - `registration_deadline`: ISO format `YYYY-MM-DD` (or `null`)
  - `team_size_min`: Minimum team members (integer, or `null`)
  - `team_size_max`: Maximum team members (integer, or `null`)
  - `prize`: Original displayed prize string (e.g. "$4,588", "₹1,00,000")
  - `prize_amount`: Clean numeric prize amount (float/int, or `null`)
  - `currency`: ISO currency code ("USD", "INR", "EUR", "GBP", or `null`)
  - `tags`: Deduplicated array of theme/domain tags (e.g. `["Cloud", "AWS"]`)
  - `source_url`: Canonical detail page URL
  - `source_website`: Source domain (e.g. "devfolio.co")
  - `scraped_at`: ISO UTC timestamp
- **Deep Microsite Scraping (Enabled by default)**:
  - Extracts both Next.js embedded data state (`__NEXT_DATA__`) and semantic DOM text with label fallbacks
  - Full details sub-dictionary: `runs_from`, `happening`, `applications_close`, `prize_pool`, `overview`, `sponsors`, `logo_url`
- **Automated Supabase Synchronization**:
  - Idempotent upserts on `source_url` preventing duplicate records
  - Built-in schema fallback and graceful error logging if project is paused or offline
  - Schema migration provided in `supabase_migration.sql`
- **Export Options**: Formatted JSON (`hackathons.json`) and CSV (`hackathons.csv`).
- **Resilience**: Automatic retry with exponential backoff for transient 502/503 network errors.

---

## Project Structure

```text
devfolio/
├── .venv/                   # Virtual environment (Python 3.12)
├── .env                     # Supabase credentials (SUPABASE_URL, SUPABASE_KEY)
├── .env.example             # Template for environment configuration
├── scraper.py               # Main scraper script with Scrapling & Supabase sync
├── supabase_migration.sql   # Idempotent SQL schema migration script
├── test_extraction.py       # Validation and unit test suite
├── hackathons.json          # Scraped output in JSON format
├── hackathons.csv           # Scraped output in CSV format
├── pyproject.toml           # Dependencies configuration
└── README.md                # Documentation
```

---

## Prerequisites & Installation

Scrapling requires Python 3.10+. This project is set up with Python 3.12 using `uv`.

### 1. Activate the Virtual Environment

**Windows PowerShell:**
```powershell
.\.venv\Scripts\activate
```

*(Or use `uv run python scraper.py`)*

### 2. (Optional) Reinstall Dependencies

```powershell
uv pip install "scrapling[all]" "supabase>=2.0.0" "python-dotenv>=1.0.0"
```

---

## Usage

### 1. Scrape Detail Pages & Sync to Supabase (Default)

Scrapes hackathons, extracts all structured fields from each detail page, and syncs them to Supabase:

```powershell
.\.venv\Scripts\python.exe scraper.py
```

### 2. Deep Scrape with Limit (Fast Test)

Scrapes only the first 3 detail pages for quick verification:

```powershell
.\.venv\Scripts\python.exe scraper.py --deep-limit 3
```

### 3. Skip Supabase Sync (Local JSON/CSV Only)

```powershell
.\.venv\Scripts\python.exe scraper.py --no-supabase
```

### 4. Skip Detail Pages (Listing Cards Only)

```powershell
.\.venv\Scripts\python.exe scraper.py --no-deep
```

---

## CLI Options

| Argument | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--url` | string | `https://devfolio.co/hackathons` | Target URL to scrape |
| `--output` | string | `hackathons` | Output base filename (without extension) |
| `--format` | choice | `all` | Export format: `json`, `csv`, or `all` |
| `--deep` / `--no-deep` | flag | `True` | Scrape individual microsites for in-depth data |
| `--deep-limit` | int | `None` | Max number of detail pages to scrape |
| `--supabase` / `--no-supabase` | flag | `True` | Sync records to Supabase database |
| `--table` | string | `hackathons` | Target Supabase table name |
| `--fetcher` | choice | `fetcher` | Fetcher backend: `fetcher` (curl_cffi) or `stealthy` (browser) |

---

## Sample Extracted Data (JSON)

```json
{
  "title": "CodeStorm 2026: FutureForge",
  "url": "https://codestorm-futureforge.devfolio.co/",
  "section": "Open",
  "type": "Hackathon",
  "modality": "Online",
  "status": "Live",
  "timeline": "",
  "themes": [
    "Design",
    "AI",
    "Future Mobility"
  ],
  "participants": "+1000 participating",
  "cover_image": "",
  "cta_action": "Apply now",
  "external_links": {
    "website": "https://code-storm-hackathon.pages.dev/",
    "x_twitter": "https://x.com/TeamCodeStorm"
  },
  "badges": [
    "Online",
    "Open",
    "Live"
  ]
}
```
