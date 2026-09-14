# Central Hackathon Scraper Suite

A unified, production-grade web scraping and data integration suite that extracts structured, live hackathon event data across five premier platforms:

1. **Devfolio** ([https://devfolio.co/hackathons](https://devfolio.co/hackathons))
2. **Devpost** ([https://devpost.com](https://devpost.com))
3. **HackerEarth** ([https://www.hackerearth.com/challenges/](https://www.hackerearth.com/challenges/))
4. **Knowafest** ([https://www.knowafest.com/explore/fest-type/Hackathon](https://www.knowafest.com/explore/fest-type/Hackathon))
5. **Unstop** ([https://unstop.com/hackathons](https://unstop.com/hackathons))

---

## 1. Project Architecture

```text
hackathon-scraper/
│
├── scrapers/                   # Five independent platform scrapers
│   ├── devfolio/               # Devfolio scraper (cards + deep microsites)
│   ├── devpost/                # Devpost scraper (API + detail pages)
│   ├── HackerEarth/            # HackerEarth scraper (RSC / API challenges)
│   ├── Knowafest/              # Knowafest scraper (event table & snapshots)
│   └── unstop/                 # Unstop scraper (REST API & competition details)
│
├── common/                     # Central shared abstraction layer
│   ├── __init__.py             # Exports models, parsers, and db layer
│   ├── models.py               # Unified 17-field Hackathon data model & validation
│   ├── normalizer.py           # Shared parsers (dates, team sizes, prizes, modes, tags)
│   ├── supabase_client.py      # Secure Supabase client & environment loader
│   ├── database.py             # Idempotent upsert & in-memory deduplication layer
│   └── utils.py                # Centralized logging, banner & reporting formatters
│
├── tests/                      # Central test suite
│   ├── __init__.py
│   ├── test_models.py          # Data model integrity & validation tests
│   ├── test_normalizer.py      # Date, range, team size, and prize normalizer tests
│   └── test_database.py        # Deduplication and conflict resolution tests
│
├── data/                       # Consolidated scraped dataset exports (JSON & CSV)
├── main.py                     # Central CLI controller orchestrating all 5 scrapers
├── requirements.txt            # Unified dependencies
├── .env.example                # Clean environment template (no credentials)
├── .gitignore                  # Gitignore protecting secrets, caches, and venvs
├── supabase_migration.sql      # Central idempotent Supabase SQL migration script
├── PROVENANCE.md               # Original Git commits, authors, and remotes record
└── README.md                   # Complete documentation
```

---

## 2. Common Data Model (17 Fields)

Every platform extracts and normalizes the following 17 structured fields:

| Field | Type | Description | Example Value |
|---|---|---|---|
| `title` | `TEXT` | Event title (**required**) | `"HackSpire'26"` |
| `organizer` | `TEXT` | Hosting entity or company | `"FIEM ACM Student Chapter"` |
| `status` | `TEXT` | Lifecycle state (`"Upcoming"`, `"Ongoing"`, `"Closed"`, etc.) | `"Upcoming"` |
| `location` | `TEXT` | Physical venue / city or `"Online"` | `"Kolkata, West Bengal, India"` |
| `event_mode` | `TEXT` | `"Online"`, `"Offline"`, or `"Hybrid"` | `"Offline"` |
| `start_date` | `DATE` / `TEXT` | ISO start date (`YYYY-MM-DD`) | `"2026-10-02"` |
| `end_date` | `DATE` / `TEXT` | ISO end date (`YYYY-MM-DD`) or `NULL` | `"2026-10-03"` |
| `registration_deadline` | `DATE` / `TEXT` | ISO deadline (`YYYY-MM-DD`) or `NULL` | `"2026-09-15"` |
| `team_size_min` | `INT` | Minimum team participants or `NULL` | `2` |
| `team_size_max` | `INT` | Maximum team participants or `NULL` | `4` |
| `prize` | `TEXT` | Original displayed prize string | `"$4,588"` |
| `prize_amount` | `NUMERIC` | Clean numeric prize amount | `4588.0` |
| `currency` | `TEXT` | ISO currency code (`"USD"`, `"INR"`, `"EUR"`, `"GBP"`) | `"USD"` |
| `tags` | `TEXT[]` | Deduplicated list of themes/technologies | `["Cloud", "AI"]` |
| `source_url` | `TEXT` | Canonical event URL (**unique key**) | `"https://hackspire26.devfolio.co/"` |
| `source_website` | `TEXT` | Source platform domain | `"devfolio.co"` |
| `scraped_at` | `TIMESTAMPTZ` | Timestamp when event was scraped | `"2026-09-14T15:57:18+00:00"` |

### Data Integrity & Zero-Fabrication Rules
- **No Hallucinations**: Information not present on the source page evaluates strictly to `None` / `NULL` or empty list `[]`.
- **No Silent Inversions**: Inverted values (e.g. `team_size_min > team_size_max`) are never silently swapped; a warning is logged and the field is set to `NULL`.
- **Date Consistency**: If `start_date > end_date`, `end_date` is set to `NULL` with an explicit warning.
- **Prize Validation**: Negative amounts are rejected and set to `NULL`.

---

## 3. Setup & Installation

### Prerequisites
- Python 3.10+
- (Optional) Virtual environment

### Installation
```bash
# 1. Clone repository
git clone <repository-url>
cd hackathon-scraper

# 2. (Optional) Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate    # On Linux/macOS
.venv\Scripts\activate       # On Windows

# 3. Install dependencies
pip install -r requirements.txt
```

### Environment Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Update `.env` with your Supabase credentials:
```env
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_KEY=your-supabase-anon-or-service-key
SUPABASE_TABLE=hackathons
```

---

## 4. Running the Scrapers

### Running the Central Controller (`main.py`)
Run all five scrapers sequentially with fault isolation:
```bash
# Scrape all 5 platforms
python main.py

# Scrape with a limit per platform (ideal for testing)
python main.py --limit 3

# Run a specific platform
python main.py --platform devfolio
python main.py --platform devpost
python main.py --platform hackerearth
python main.py --platform knowafest
python main.py --platform unstop

# Export locally without syncing to Supabase
python main.py --no-supabase --limit 5
```

### Running Scrapers Independently
Each scraper retains its own independent command-line interface:
```bash
# Devfolio
python scrapers/devfolio/scraper.py --deep-limit 5 --no-supabase

# Devpost
python scrapers/devpost/scraper.py --page 1 --no-supabase

# HackerEarth
python scrapers/HackerEarth/scraper.py

# Knowafest
python scrapers/Knowafest/scraper.py --limit 5 --skip-supabase

# Unstop
python scrapers/unstop/scraper.py --limit 5 --skip-supabase
```

---

## 5. Database Architecture & Migrations

### Shared Supabase Schema
All scrapers synchronize to the `hackathons` table in PostgreSQL. Deduplication is enforced on the `source_url` column via an idempotent unique index:
```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_hackathons_source_url ON hackathons(source_url);
```

### Central SQL Migration
Review [`supabase_migration.sql`](file:///c:/Users/naveed%20sheriff%20j/Desktop/hackathon-scraper/supabase_migration.sql) for the complete schema definition.

> [!IMPORTANT]
> The migration script is safe and idempotent. Run it in your Supabase SQL Editor:
> [https://supabase.com/dashboard/project/_/sql](https://supabase.com/dashboard/project/_/sql)

---

## 6. Testing

### Run All Unit Tests
```bash
# Central test suite (models, normalizer, database)
python -m unittest discover -s tests -p "test_*.py"

# Platform-specific test suites
python -m unittest discover -s scrapers/devfolio -p "test_*.py"
python -m unittest discover -s scrapers/HackerEarth -p "test_*.py"
python -m unittest discover -s scrapers/Knowafest -p "test_*.py"
python -m unittest discover -s scrapers/unstop -p "test_*.py"
```

---

## 7. Git Provenance

The previous commit history, authors, and remote repositories for Devfolio, Devpost, and HackerEarth have been recorded in [`PROVENANCE.md`](file:///c:/Users/naveed%20sheriff%20j/Desktop/hackathon-scraper/PROVENANCE.md).
