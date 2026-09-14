# Unstop Hackathon Scraper & Supabase Integration

A robust, production-grade hackathon scraper for [Unstop](https://unstop.com/hackathons) powered by [Scrapling](https://github.com/D4Vinci/Scrapling). Dynamically extracts all structured details visible on Unstop hackathon pages, strictly normalizes dates, team sizes, prizes, and locations without hallucinations, and synchronizes with your Supabase PostgreSQL database using idempotent upserts on `source_url`.

---

## 1. Extracted Fields & Supabase Mapping

Every scraped hackathon record extracts all 17 standardized fields:

| Field Name | Type | Description / Normalization | Null Handling |
| :--- | :--- | :--- | :--- |
| `title` | `TEXT NOT NULL` | Title of the hackathon | Dropped if missing |
| `organizer` | `TEXT` | Hosting college, university, company, or community | `null` |
| `status` | `TEXT` | Lifecycle status (`Upcoming`, `Ongoing`, `Closed`, `Completed`) | `null` |
| `location` | `TEXT` | Cleaned city, state, country or `"Online"` | `null` |
| `event_mode` | `TEXT` | Disambiguated mode (`"Online"`, `"Offline"`, or `"Hybrid"`) | `null` |
| `start_date` | `DATE` | Strict ISO date `YYYY-MM-DD` | `null` |
| `end_date` | `DATE` | Strict ISO date `YYYY-MM-DD` | `null` |
| `registration_deadline` | `DATE` | Strict ISO date `YYYY-MM-DD` | `null` |
| `team_size_min` | `INT` | Minimum allowed team size integer | `null` |
| `team_size_max` | `INT` | Maximum allowed team size integer | `null` |
| `prize` | `TEXT` | Original verbatim prize text (e.g. `"Prize pool of Rs 1.5 Lakhs"`, `"₹50,000"`) | `null` |
| `prize_amount` | `NUMERIC` | Calculated numeric amount (e.g. `150000.0`, `50000.0`) | `null` |
| `currency` | `TEXT` | ISO currency code (`INR`, `USD`, `EUR`, `GBP`) | `null` |
| `tags` | `TEXT[]` | Cleaned, deduplicated skills, categories, and eligibility filters | `[]` |
| `source_url` | `TEXT NOT NULL` | Canonical detail page URL (Unique key) | Dropped if invalid |
| `source_website` | `TEXT` | Host domain dynamically extracted (`"unstop.com"`) | `null` |
| `scraped_at` | `TIMESTAMPTZ` | Current UTC timestamp in ISO 8601 format | Generated at runtime |

Legacy metadata fields (`organization`, `rounds_and_timeline`, `contacts`, `banner_url`, `logo_url`, `description`, `eligibility`, `views_count`, `registrations_count`) are also preserved in JSON export and positioned after the standardized 17 fields in the CSV export for full backward compatibility.

---

## 2. Supabase Setup & Migration

If you need to create or update the `hackathons` table in Supabase, execute [`supabase_migration.sql`](file:///c:/Users/naveed%20sheriff%20j/Desktop/unstop/supabase_migration.sql) in your [Supabase SQL Editor](https://supabase.com/dashboard/project/_/sql).

The migration script:
1. Creates the `hackathons` table if it does not exist.
2. Applies safe `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for all 17 fields.
3. Creates a unique index on `source_url` for idempotent upserting.
4. Adds query performance indexes and RLS policies.

Configure `.env`:
```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-or-service-role-key
SUPABASE_TABLE=hackathons
```

---

## 3. Running the Scraper

### Scrape Open Hackathons (Default)
```powershell
.\.venv\Scripts\python scraper.py
```

### Scrape With Limit (e.g., 5 items for testing)
```powershell
.\.venv\Scripts\python scraper.py --limit 5
```

### Filter by Status (`open`, `upcoming`, `closed`, `all`)
```powershell
.\.venv\Scripts\python scraper.py --status upcoming --limit 10
```

### Local Only Export (Skip Supabase Sync)
```powershell
.\.venv\Scripts\python scraper.py --skip-supabase --limit 5
```

---

## 4. Running the Tests

### Unit Tests (Parsing, Validation, Null Handling)
```powershell
.\.venv\Scripts\python test_scraper.py
```

### Deduplication Test
```powershell
.\.venv\Scripts\python test_dedupe.py
```

### Data Verification Script
```powershell
.\.venv\Scripts\python verify_data.py
```
