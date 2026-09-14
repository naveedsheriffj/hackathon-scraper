#!/usr/bin/env python3
"""
Knowafest Hackathon Scraper & Supabase Sync
Extracts accurate, structured details for hackathons from Knowafest using Scrapling.
Dynamically extracts and normalizes all 17 standardized fields:
- title, organizer, status, location, event_mode, start_date, end_date,
  registration_deadline, team_size_min, team_size_max, prize, prize_amount,
  currency, tags, source_url, source_website, scraped_at.
Strictly handles missing data as null/None without hallucination or interpolation.
Upserts records idempotently into the Supabase 'hackathons' table with deduplication on 'source_url'.
"""

import csv
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from scrapling import Fetcher

# Configure stdout encoding for Windows console compatibility
sys.stdout.reconfigure(encoding='utf-8')

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("KnowafestScraper")

BASE_URL = "https://www.knowafest.com/explore/fest-type/Hackathon"
REFERENCE_DATE = date.today()


# ==============================================================================
# ENVIRONMENT & SUPABASE CREDENTIALS
# ==============================================================================

def load_env(env_path: str = ".env") -> Dict[str, str]:
    """Parse local .env file without external dependencies and populate os.environ."""
    env_vars: Dict[str, str] = {}
    if not os.path.exists(env_path):
        return env_vars
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                k = key.strip()
                v = val.strip().strip("'\"")
                env_vars[k] = v
                if k not in os.environ:
                    os.environ[k] = v
    except Exception as e:
        logger.warning(f"Could not read .env from {env_path}: {e}")
    return env_vars


def get_supabase_credentials() -> Tuple[Optional[str], Optional[str], str]:
    """Retrieve Supabase URL, Key, and target table name from environment or .env."""
    load_env()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    table = os.environ.get("SUPABASE_TABLE", "hackathons")
    return url, key, table


# ==============================================================================
# TEXT & STRING HELPERS
# ==============================================================================

def clean_text(text: Optional[str]) -> Optional[str]:
    """Clean whitespace and formatting while preserving legitimate text content."""
    if not text:
        return None
    cleaned = text.replace('\ufffd', '•')
    cleaned = re.sub(r'[ \t]+', ' ', cleaned)
    cleaned = re.sub(r'\r\n|\r|\n', '\n', cleaned)
    cleaned = re.sub(r'\n\s*\n+', '\n\n', cleaned).strip()
    return cleaned if cleaned else None


def extract_snapshot_field(text: str, label: str) -> Optional[str]:
    """Extract field value from the Event Snapshot sidebar text."""
    pattern = rf"{label}\s*\n\s*([^\n\r]+)"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        val = match.group(1).strip()
        return val if val else None
    return None


# ==============================================================================
# DATE PARSING ENGINE
# ==============================================================================

def parse_single_date(date_str: Optional[str]) -> Optional[str]:
    """Parse a single date string into strict ISO YYYY-MM-DD format.
    Never hallucinates or invents dates. Returns None if unparseable.
    """
    if not date_str:
        return None

    cleaned = clean_text(date_str) or ""

    # Strip common prefixes like 'Registration closes: ', 'Deadline: ', etc.
    prefixes = [
        r'^(?:last\s+dates?\s+for\s+registration|last\s+date\s+for\s+registration|registration\s+deadline|registration\s+closes?|prelims\s+ppt\s+submission\s+deadline|important\s+dates|deadline)\s*[:–-]?\s*',
        r'^(?:round\s+\d+\s*[-–]\s*idea\s+submission\s*&\s*shortlisting\s*[:–-]?\s*)',
    ]
    for p in prefixes:
        cleaned = re.sub(p, '', cleaned, flags=re.IGNORECASE).strip()

    # Try ISO YYYY-MM-DD directly
    m = re.search(r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b', cleaned)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime('%Y-%m-%d')
        except ValueError:
            pass

    # Try DD.MM.YYYY, DD-MM-YYYY, DD/MM/YYYY
    dmy_m = re.search(r'\b(\d{1,2})[\./-](\d{1,2})[\./-](\d{4})\b', cleaned)
    if dmy_m:
        day, month, year = int(dmy_m.group(1)), int(dmy_m.group(2)), int(dmy_m.group(3))
        try:
            return datetime(year, month, day).strftime('%Y-%m-%d')
        except ValueError:
            pass

    # Strip ordinals like 1st, 2nd, 3rd, 4th
    clean_ord = re.sub(r'(\d{1,2})(?:st|nd|rd|th)', r'\1', cleaned)

    # Try DD Mon YYYY or DD Month YYYY
    pattern = r'\b\d{1,2}\s+[A-Za-z]+,?\s+\d{4}\b|\b[A-Za-z]+\s+\d{1,2},?\s+\d{4}\b'
    match = re.search(pattern, clean_ord)
    if match:
        cand = match.group(0).replace(',', '')
        for test_fmt in ('%d %b %Y', '%d %B %Y', '%b %d %Y', '%B %d %Y'):
            try:
                return datetime.strptime(cand, test_fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue

    return None


def parse_date_range(date_text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Parse start and end dates from a date range string into ISO (YYYY-MM-DD, YYYY-MM-DD).
    Supports normal hyphen '-', en dash '–', em dash '—', and 'to'.
    If single date, returns (start_date, None).
    """
    if not date_text:
        return None, None

    date_text = clean_text(date_text) or ""

    # Check multi-month pattern: "2 Nov 2026 – 3 Nov 2026" or "25 Sep 2026 - 27 Sep 2026"
    multi_month_match = re.search(
        r'\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})\s*(?:–|-|to)\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})\b',
        date_text,
        re.IGNORECASE
    )
    if multi_month_match:
        s_day, s_mon, s_yr, e_day, e_mon, e_yr = multi_month_match.groups()
        for mfmt in ("%d %B %Y", "%d %b %Y"):
            try:
                dt_start = datetime.strptime(f"{s_day} {s_mon} {s_yr}", mfmt).strftime("%Y-%m-%d")
                dt_end = datetime.strptime(f"{e_day} {e_mon} {e_yr}", mfmt).strftime("%Y-%m-%d")
                return dt_start, dt_end
            except ValueError:
                continue

    # Check same-month pattern: "15th - 16th September 2026" or "15th to 16th September 2026"
    range_match = re.search(
        r'\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:–|-|to)\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})\b',
        date_text,
        re.IGNORECASE
    )
    if range_match:
        start_day, end_day, month_str, year = range_match.groups()
        for mfmt in ("%d %B %Y", "%d %b %Y"):
            try:
                dt_start = datetime.strptime(f"{start_day} {month_str} {year}", mfmt).strftime("%Y-%m-%d")
                dt_end = datetime.strptime(f"{end_day} {month_str} {year}", mfmt).strftime("%Y-%m-%d")
                return dt_start, dt_end
            except ValueError:
                continue

    # Check month-first pattern: "September 15 - 16, 2026"
    month_first_range = re.search(
        r'\b([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s*(?:–|-|to)\s*(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b',
        date_text,
        re.IGNORECASE
    )
    if month_first_range:
        month_str, start_day, end_day, year = month_first_range.groups()
        for mfmt in ("%B %d %Y", "%b %d %Y"):
            try:
                dt_start = datetime.strptime(f"{month_str} {start_day} {year}", mfmt).strftime("%Y-%m-%d")
                dt_end = datetime.strptime(f"{month_str} {end_day} {year}", mfmt).strftime("%Y-%m-%d")
                return dt_start, dt_end
            except ValueError:
                continue

    # Fallback to single date
    single = parse_single_date(date_text)
    return single, None


# ==============================================================================
# TEAM SIZE PARSING
# ==============================================================================

def parse_team_size(text: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """Parse team size dynamically from description or rules.
    Examples:
    "1-3" -> (1, 3)
    "2–5" -> (2, 5)
    "1" -> (1, 1)
    "Team Size : 3 to 5 members" -> (3, 5)
    "teams of 3 to 4 members" -> (3, 4)
    "max of 5 partcipants" -> (1, 5)
    "up to 4 members" -> (1, 4)
    Returns (team_size_min, team_size_max) or (None, None).
    """
    if not text:
        return None, None

    # Pattern 1: Explicit team range "Team Size : 3 to 5", "teams of 3 to 4", "Team Size: 1–4"
    explicit_range = re.search(
        r'(?:team\s*size|teams?\s*of|members?)\s*[:=–-]?\s*(?:up\s*to\s*)?(\d{1,2})\s*(?:–|-|to)\s*(\d{1,2})',
        text,
        re.IGNORECASE
    )
    if explicit_range:
        mn, mx = int(explicit_range.group(1)), int(explicit_range.group(2))
        return (min(mn, mx), max(mn, mx))

    # Pattern 2: "max of 5 participants" or "up to 4 members"
    max_match = re.search(
        r'(?:max(?:imum)?\s*of|up\s*to)\s*(\d{1,2})\s*(?:members?|partcipants?|participants?|students?)',
        text,
        re.IGNORECASE
    )
    if max_match:
        mx = int(max_match.group(1))
        return (1, mx)

    # Pattern 3: Standalone "1-3" or "2–5"
    range_match = re.search(r'\b(\d{1,2})\s*(?:–|-|to)\s*(\d{1,2})\b', text)
    if range_match:
        mn, mx = int(range_match.group(1)), int(range_match.group(2))
        if 1 <= mn <= 50 and 1 <= mx <= 50:
            return (min(mn, mx), max(mn, mx))

    # Pattern 4: Explicit single team size "Team size: 1" or "teams of 4"
    single_explicit = re.search(r'(?:team\s*size|teams?\s*of)\s*[:=–-]?\s*(\d{1,2})\b', text, re.IGNORECASE)
    if single_explicit:
        val = int(single_explicit.group(1))
        if 1 <= val <= 50:
            return (val, val)

    exact_single = re.search(r'^\s*(\d{1,2})\s*$', text)
    if exact_single:
        val = int(exact_single.group(1))
        if 1 <= val <= 50:
            return (val, val)

    return None, None


# ==============================================================================
# PRIZE PARSING
# ==============================================================================

def parse_prize(text: Optional[str]) -> Tuple[Optional[str], Optional[float], Optional[str]]:
    """Extract prize details: original display text, normalized numeric amount, and ISO currency.
    Supports INR (₹, Rs), USD ($), EUR (€), GBP (£), and Indian denominations (Lakh, Crore).
    If numeric amount is unquantifiable, returns original prize text with prize_amount = None.
    If no prize is mentioned, returns (None, None, None).
    """
    if not text:
        return None, None, None

    # Search for prize phrases without cutting on commas
    prize_match = re.search(
        r'(?:(?:combined\s+)?prize\s*(?:pool|money|worth)?|cash\s*prizes?|reward\s*pool)[\s:]*([^\n\r;•|]+)',
        text,
        re.IGNORECASE
    )
    if not prize_match:
        standalone = re.search(r'[\$₹€£]\s*\d+(?:,\d+)*(?:\.\d+)?(?:\s*(?:lakhs?|lac|crores?|cr|k|m))?', text, re.IGNORECASE)
        if standalone:
            extracted_text = standalone.group(0).strip()
        else:
            return None, None, None
    else:
        extracted_text = prize_match.group(0).strip()

    # Detect currency
    currency = None
    if '₹' in extracted_text or 'INR' in extracted_text.upper() or 'RS' in extracted_text.upper():
        currency = "INR"
    elif '$' in extracted_text or 'USD' in extracted_text.upper():
        currency = "USD"
    elif '€' in extracted_text or 'EUR' in extracted_text.upper():
        currency = "EUR"
    elif '£' in extracted_text or 'GBP' in extracted_text.upper():
        currency = "GBP"

    # Multipliers
    multiplier = 1.0
    if re.search(r'\b(?:crores?|cr)\b', extracted_text, re.IGNORECASE):
        multiplier = 10000000.0
    elif re.search(r'\b(?:lakhs?|lac|lacs)\b', extracted_text, re.IGNORECASE):
        multiplier = 100000.0
    elif re.search(r'\b(?:k)\b', extracted_text, re.IGNORECASE) and not re.search(r'2k\d\d', extracted_text, re.IGNORECASE):
        multiplier = 1000.0
    elif re.search(r'\b(?:million|m)\b', extracted_text, re.IGNORECASE):
        multiplier = 1000000.0

    # Extract numeric value
    num_match = re.search(r'[\$₹€£]?\s*(\d+(?:,\d+)*(?:\.\d+)?)', extracted_text)
    prize_amount = None
    if num_match:
        num_str = num_match.group(1).replace(',', '')
        try:
            base_val = float(num_str)
            if base_val == 2026 and multiplier == 1.0 and not any(sym in extracted_text for sym in ['₹', '$', '€', '£', 'Rs']):
                prize_amount = None
            else:
                prize_amount = base_val * multiplier
        except ValueError:
            prize_amount = None

    display_prize = extracted_text.strip()
    if not any(sym in display_prize for sym in ['₹', '$', '€', '£']) and not re.search(r'\b(?:prize|cash|reward|inr|usd|eur|gbp|rs\.?)\b', display_prize, re.IGNORECASE):
        return None, None, None

    if prize_amount is not None and not currency:
        currency = "USD" if '$' in display_prize else ("INR" if '₹' in display_prize or 'Rs' in display_prize else None)

    return display_prize, prize_amount, currency


# ==============================================================================
# LOCATION & EVENT MODE DISAMBIGUATION
# ==============================================================================

def parse_location_and_mode(
    location_raw: Optional[str],
    event_mode_raw: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    """Disambiguate location and event mode.
    "Online (Online)" -> location="Online", event_mode="Online"
    "Venue/Offline Mode" -> event_mode="Offline"
    "Offline and Online Mode" -> event_mode="Hybrid"
    Physical location string is retained separately.
    """
    loc = clean_text(location_raw)
    mode = clean_text(event_mode_raw)

    if loc and "online (online)" in loc.lower():
        return "Online", "Online"

    if loc and loc.strip().lower() == "online":
        return "Online", "Online"

    event_mode = None
    if mode:
        m_lower = mode.lower()
        if "offline and online" in m_lower or "online and offline" in m_lower or "hybrid" in m_lower:
            event_mode = "Hybrid"
        elif "offline" in m_lower or "venue" in m_lower:
            event_mode = "Offline"
        elif "online" in m_lower:
            event_mode = "Online"

    if loc:
        loc = re.sub(r'\s+', ' ', loc).strip()
        if not event_mode:
            event_mode = "Offline"

    return loc, event_mode


# ==============================================================================
# TAGS EXTRACTION
# ==============================================================================

def clean_tags(tags_list: List[str]) -> List[str]:
    """Clean and deduplicate tags list while preserving original casing and order."""
    seen = set()
    cleaned = []
    for t in tags_list:
        if not t:
            continue
        s = re.sub(r'^[•\-\*–—\s]+|[•\-\*–—\s]+$', '', str(t)).strip()
        if not s or len(s) > 60:
            continue
        key = s.lower()
        if key not in seen:
            seen.add(key)
            cleaned.append(s)
    return cleaned


# ==============================================================================
# RECORD VALIDATION
# ==============================================================================

def validate_record(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate all 17 fields according to data integrity standards.
    Ensures zero hallucinated values. Invalid optional fields become None.
    """
    out = dict(rec)

    # Title is mandatory
    if not out.get("title") or not str(out["title"]).strip():
        logger.warning(f"[VALIDATION] Dropping record with missing title: {out.get('source_url')}")
        return None
    out["title"] = str(out["title"]).strip()

    # Source URL is mandatory
    source_url = out.get("source_url")
    if not source_url or not str(source_url).startswith(("http://", "https://")):
        logger.warning(f"[VALIDATION] Dropping record with invalid source_url: {out.get('title')}")
        return None
    out["source_url"] = str(source_url).strip()

    # Validate dates are YYYY-MM-DD
    for d_field in ("start_date", "end_date", "registration_deadline"):
        val = out.get(d_field)
        if val:
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', str(val)):
                logger.warning(f"[VALIDATION] Nullifying invalid {d_field} format: {val}")
                out[d_field] = None

    # Date range logic
    if out.get("start_date") and out.get("end_date"):
        if out["start_date"] > out["end_date"]:
            logger.warning(f"[VALIDATION] start_date ({out['start_date']}) > end_date ({out['end_date']}), resetting end_date to None.")
            out["end_date"] = None

    # Team size validation
    t_min = out.get("team_size_min")
    t_max = out.get("team_size_max")
    if t_min is not None:
        try:
            out["team_size_min"] = int(t_min)
        except (ValueError, TypeError):
            out["team_size_min"] = None
    if t_max is not None:
        try:
            out["team_size_max"] = int(t_max)
        except (ValueError, TypeError):
            out["team_size_max"] = None

    if out.get("team_size_min") is not None and out.get("team_size_max") is not None:
        if out["team_size_min"] > out["team_size_max"]:
            out["team_size_min"], out["team_size_max"] = out["team_size_max"], out["team_size_min"]

    # Prize validation
    p_amt = out.get("prize_amount")
    if p_amt is not None:
        try:
            out["prize_amount"] = float(p_amt)
        except (ValueError, TypeError):
            out["prize_amount"] = None

    if out.get("prize_amount") is None and not out.get("prize"):
        out["currency"] = None

    # Tags validation
    tags = out.get("tags") or []
    if isinstance(tags, list):
        out["tags"] = clean_tags(tags)
    else:
        out["tags"] = []

    return out


# ==============================================================================
# STRUCTURED LOGGING
# ==============================================================================

def log_scraped_summary(rec: Dict[str, Any]) -> None:
    """Log structured information for each scraped hackathon."""
    team_str = (
        f"{rec.get('team_size_min')} - {rec.get('team_size_max')}"
        if rec.get('team_size_min') is not None
        else "N/A"
    )
    logger.info(
        f"\n[SCRAPED]\n"
        f"Title: {rec.get('title')}\n"
        f"Organizer: {rec.get('organizer')}\n"
        f"Status: {rec.get('status')}\n"
        f"Location: {rec.get('location')}\n"
        f"Event Mode: {rec.get('event_mode')}\n"
        f"Start Date: {rec.get('start_date')}\n"
        f"End Date: {rec.get('end_date')}\n"
        f"Registration Deadline: {rec.get('registration_deadline')}\n"
        f"Team Size: {team_str}\n"
        f"Prize: {rec.get('prize')}\n"
        f"Prize Amount: {rec.get('prize_amount')}\n"
        f"Currency: {rec.get('currency')}\n"
        f"Tags: {rec.get('tags')}\n"
        f"Source URL: {rec.get('source_url')}"
    )


# ==============================================================================
# SUPABASE INTEGRATION & UPSERT
# ==============================================================================

def sync_to_supabase(
    records: List[Dict[str, Any]],
    table_name: Optional[str] = None,
) -> Tuple[bool, str, Dict[str, int]]:
    """Sync validated hackathon records to Supabase table using:
    1. supabase-py Client (if available and connected)
    2. PostgREST REST API (urllib standard library fallback)
    Deduplicates on 'source_url' to prevent duplicate records.
    """
    url, key, default_table = get_supabase_credentials()
    target_table = table_name or default_table
    stats = {"scraped": len(records), "upserted": 0, "failed": 0}

    if not url or not key:
        msg = "SUPABASE_URL or SUPABASE_KEY not configured. Records safely saved locally to JSON/CSV."
        logger.info(f"[SUPABASE] {msg}")
        return False, msg, stats

    if not records:
        return True, "No records to sync.", stats

    # Deduplicate on source_url
    unique_map: Dict[str, Dict[str, Any]] = {}
    for r in records:
        source_url = r.get("source_url")
        if not source_url:
            continue

        item = {
            "title": r.get("title"),
            "organizer": r.get("organizer"),
            "status": r.get("status"),
            "location": r.get("location"),
            "event_mode": r.get("event_mode"),
            "start_date": r.get("start_date"),
            "end_date": r.get("end_date"),
            "registration_deadline": r.get("registration_deadline"),
            "team_size_min": r.get("team_size_min"),
            "team_size_max": r.get("team_size_max"),
            "prize": r.get("prize"),
            "prize_amount": r.get("prize_amount"),
            "currency": r.get("currency"),
            "tags": r.get("tags") or [],
            "source_url": source_url,
            "source_website": r.get("source_website"),
            "scraped_at": r.get("scraped_at"),
        }
        unique_map[source_url] = item

    db_records = list(unique_map.values())
    if not db_records:
        return True, "No unique records to sync.", stats

    logger.info(f"[SUPABASE] Upserting {len(db_records)} unique records to table '{target_table}'...")

    # Attempt 1: supabase-py client
    try:
        from supabase import create_client
        client = create_client(url, key)
        response = client.table(target_table).upsert(
            db_records,
            on_conflict="source_url"
        ).execute()
        count = len(response.data) if response.data else len(db_records)
        stats["upserted"] = count
        msg = f"Successfully synced {count} records to Supabase table '{target_table}'."
        logger.info(f"[SUPABASE] Inserted/Updated: {count} records | Conflict key: source_url")
        if response.data:
            for item in response.data[:5]:
                logger.info(f"[SUPABASE] Record ID: {item.get('id', 'N/A')} | Title: {item.get('title')}")
        return True, msg, stats
    except ImportError:
        pass
    except Exception as exc:
        err_str = str(exc)
        logger.warning(f"[SUPABASE] Client upsert failed: {err_str}")
        if "PGRST204" in err_str or "column" in err_str.lower():
            logger.warning("[WARNING] Supabase schema is missing columns. Please execute 'supabase_migration.sql' in Supabase SQL Editor.")

    # Attempt 2: Direct PostgREST REST API via urllib
    try:
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=representation",
            "User-Agent": "KnowafestScraper/1.0",
        }
        endpoint = f"{url.rstrip('/')}/rest/v1/{target_table}?on_conflict=source_url"
        payload_bytes = json.dumps(db_records, default=str).encode("utf-8")
        req = urllib.request.Request(endpoint, data=payload_bytes, headers=headers, method="POST")

        with urllib.request.urlopen(req, timeout=15) as res:
            if res.status in (200, 201):
                body = res.read().decode("utf-8")
                data = json.loads(body) if body else []
                count = len(data) if data else len(db_records)
                stats["upserted"] = count
                msg = f"Successfully synced {count} records to Supabase via REST API."
                logger.info(f"[SUPABASE] Inserted/Updated: {count} records | Conflict key: source_url")
                if data:
                    for item in data[:5]:
                        logger.info(f"[SUPABASE] Record ID: {item.get('id', 'N/A')} | Title: {item.get('title')}")
                return True, msg, stats
    except urllib.error.HTTPError as he:
        body = he.read().decode("utf-8", errors="ignore")
        logger.warning(f"[SUPABASE] HTTP {he.code} Error: {body}")
        if "column" in body.lower() or "pgrst204" in body.lower():
            logger.warning("[WARNING] Column mismatch detected. Please execute 'supabase_migration.sql' in Supabase SQL Editor.")
    except Exception as exc:
        logger.warning(f"[SUPABASE] REST API connection notice: {exc}")
        logger.info("[SUPABASE] If your Supabase project was paused or unreachable, run 'supabase_migration.sql' once active.")

    stats["failed"] = len(db_records)
    return False, "Supabase sync could not complete.", stats


# ==============================================================================
# DETAIL PAGE DYNAMIC EXTRACTION
# ==============================================================================

def parse_detail_page(event_url: str) -> Dict[str, Any]:
    """Fetch and parse detailed information from an event's page using Scrapling."""
    detail_data: Dict[str, Any] = {
        "event_name": None,
        "date_snapshot": None,
        "location_snapshot": None,
        "category": None,
        "event_mode": None,
        "organiser": None,
        "registration_url": None,
        "official_website_url": None,
        "brochure_url": None,
        "organizer_email": None,
        "eligible_departments": [],
        "registration_fee": None,
        "registration_deadline": None,
        "contact_details": None,
        "venue_address": None,
        "about_event": None,
        "events_details": None,
        "certificates_provided": None,
        "is_closed": False,
        "prize": None,
        "prize_amount": None,
        "currency": None,
        "team_size_min": None,
        "team_size_max": None,
    }

    try:
        page = Fetcher.get(event_url)
    except Exception as e:
        logger.error(f"Error fetching detail page {event_url}: {e}")
        return detail_data

    if page.status != 200:
        logger.warning(f"Detail page returned HTTP {page.status}: {event_url}")
        return detail_data

    # Event Name from main card header
    title_el = page.css('div.card.bg-light h3, h3')
    if title_el:
        detail_data["event_name"] = clean_text(title_el[0].get_all_text())

    # Sidebar inspection (div.order-lg-2)
    sidebar = page.css('div.order-lg-2')
    if sidebar:
        sidebar_text = sidebar[0].get_all_text()
        detail_data["date_snapshot"] = extract_snapshot_field(sidebar_text, "Date")
        detail_data["location_snapshot"] = extract_snapshot_field(sidebar_text, "Location")
        detail_data["category"] = extract_snapshot_field(sidebar_text, "Category")
        detail_data["event_mode"] = extract_snapshot_field(sidebar_text, "Event Type")
        detail_data["organiser"] = extract_snapshot_field(sidebar_text, "Organiser")

        if re.search(r'Registrations Closed|Closed', sidebar_text, re.IGNORECASE):
            detail_data["is_closed"] = True

        # Sidebar CTA buttons and links
        for a in sidebar[0].css('a'):
            href = a.attrib.get('href', '').strip()
            text = a.get_all_text().strip().lower()
            if not href or href.startswith('javascript:'):
                continue
            if 'register' in text and not detail_data["registration_url"]:
                detail_data["registration_url"] = href
            elif 'website' in text and not detail_data["official_website_url"]:
                detail_data["official_website_url"] = href
            elif 'brochure' in text and not detail_data["brochure_url"]:
                detail_data["brochure_url"] = href
            elif ('contact' in text or href.startswith('mailto:')) and not detail_data["organizer_email"]:
                if href.startswith('mailto:'):
                    detail_data["organizer_email"] = href.replace('mailto:', '').split('?')[0].strip()
                else:
                    detail_data["organizer_email"] = href

    # Main content column (div.col-lg-8)
    col8 = page.css('div.col-lg-8')
    about_text = ""
    events_text = ""
    deadline_text = ""
    fee_text = ""

    if col8:
        # About Event
        about_el = col8[0].css('div.row.about')
        if about_el:
            t = about_el[0].get_all_text().strip()
            t = re.sub(r'^About Event\s*', '', t, flags=re.IGNORECASE)
            about_text = t
            detail_data["about_event"] = clean_text(t)

        # Events (Tracks / Sub-events)
        events_el = col8[0].css('div.row.events')
        if events_el:
            t = events_el[0].get_all_text().strip()
            t = re.sub(r'^Events\s*', '', t, flags=re.IGNORECASE)
            events_text = t
            detail_data["events_details"] = clean_text(t)

        # Eligible Departments
        depts: List[str] = []
        for a in col8[0].css('a.btn-soft-secondary'):
            href = a.attrib.get('href', '')
            if '/explore/fest-departments/' in href:
                dept_name = a.get_all_text().strip()
                if dept_name and dept_name not in depts:
                    depts.append(dept_name)
        detail_data["eligible_departments"] = depts

        # Content rows
        for div in col8[0].css('div.row'):
            text = div.get_all_text().strip()

            if (text.startswith('Contact Details') or 'CONTACT DETAILS:' in text) and not detail_data["contact_details"]:
                t = re.sub(r'^Contact Details\s*', '', text, flags=re.IGNORECASE)
                detail_data["contact_details"] = clean_text(t)
                if not detail_data["organizer_email"]:
                    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', t)
                    if email_match:
                        detail_data["organizer_email"] = email_match.group(0)

            elif text.startswith('Last Dates for Registration') and not deadline_text:
                deadline_text = re.sub(r'^Last Dates for Registration\s*', '', text, flags=re.IGNORECASE)
                detail_data["registration_deadline"] = clean_text(deadline_text)

            elif text.startswith('Registration Fees') and not fee_text:
                fee_text = re.sub(r'^Registration Fees\s*', '', text, flags=re.IGNORECASE)
                detail_data["registration_fee"] = clean_text(fee_text)

            elif 'How to reach' in text and not detail_data["venue_address"]:
                t = re.sub(r'^How to reach.*?\n', '', text, flags=re.IGNORECASE)
                detail_data["venue_address"] = clean_text(t)

        # Certificates
        cert_el = col8[0].css('div.alert')
        if cert_el:
            detail_data["certificates_provided"] = clean_text(cert_el[0].get_all_text())

    # Fallback for registration URL
    if not detail_data["registration_url"]:
        combined_text = (detail_data["about_event"] or "") + "\n" + (detail_data["events_details"] or "")
        link_match = re.search(
            r'https?://(?:forms\.gle|docs\.google\.com/forms|unstop\.com|devfolio\.co|[\w\.-]+\.(?:edu|ac|org|in|com)/[\w\./\?=&%#~_-]+)',
            combined_text
        )
        if link_match:
            detail_data["registration_url"] = link_match.group(0).rstrip('.,;)')

    # Parse Prize from about_event or events_details (explicitly avoiding registration fee text)
    prize_search_content = f"{about_text}\n{events_text}"
    prize_str, prize_amt, prize_curr = parse_prize(prize_search_content)
    detail_data["prize"] = prize_str
    detail_data["prize_amount"] = prize_amt
    detail_data["currency"] = prize_curr

    # Parse Team Size from about_event, events_details, and fee_text (which often notes 'per team of 4')
    team_search_content = f"{about_text}\n{events_text}\n{fee_text}"
    t_min, t_max = parse_team_size(team_search_content)
    detail_data["team_size_min"] = t_min
    detail_data["team_size_max"] = t_max

    # Registration deadline date ISO
    detail_data["registration_deadline_iso"] = parse_single_date(deadline_text)

    return detail_data


# ==============================================================================
# MAIN SCRAPER EXECUTION
# ==============================================================================

def scrape_knowafest_hackathons(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Main execution function to scrape hackathons from Knowafest,
    validate records, sync to Supabase, and export to JSON/CSV.
    """
    logger.info(f"Fetching listing page: {BASE_URL}")
    page = Fetcher.get(BASE_URL)

    if page.status != 200:
        logger.error(f"Failed to fetch {BASE_URL}, HTTP status {page.status}")
        return []

    tables = page.css('table')
    if not tables:
        logger.error("No table found on listing page.")
        return []

    rows = tables[0].css('tr')[1:]  # Exclude header row
    logger.info(f"Found {len(rows)} entries in listing table.")

    if limit and limit > 0:
        rows = rows[:limit]
        logger.info(f"Limiting scrape to first {limit} entries as requested.")

    scraped_hackathons: List[Dict[str, Any]] = []
    dropped_past_count = 0

    for idx, r in enumerate(rows, 1):
        onclick = r.attrib.get('onclick', '')
        url_match = re.search(r"window\.open\(\s*['\"](.*?)['\"]\s*\)", onclick)
        raw_path = url_match.group(1).strip() if url_match else None
        resolved_url = urljoin(BASE_URL, raw_path) if raw_path else None

        # Schema.org microdata from listing row
        name_el = r.css('[itemprop="name"]')
        listing_name = name_el[0].get_all_text().strip() if name_el else None

        date_el = r.css('[itemprop="startDate"]')
        listing_date_iso = date_el[0].attrib.get('content') if date_el else None
        listing_date_formatted = date_el[0].get_all_text().strip() if date_el else None

        college_el = r.css('[itemprop="location"] [itemprop="name"]')
        listing_college = college_el[0].get_all_text().strip() if college_el else None

        address_el = r.css('[itemprop="location"] [itemprop="address"]')
        listing_city = address_el[0].get_all_text().strip() if address_el else None

        tds = r.css('td')
        listing_summary = clean_text(tds[2].get_all_text()) if len(tds) > 2 else None

        # Detail page scraping
        logger.info(f"[{idx}/{len(rows)}] Fetching: {listing_name} ({listing_date_formatted or listing_date_iso})")
        detail_data: Dict[str, Any] = {}
        if resolved_url:
            detail_data = parse_detail_page(resolved_url)
            time.sleep(0.25)

        # 1. Title
        title = detail_data.get("event_name") or listing_name

        # 2. Organizer
        organizer = detail_data.get("organiser") or listing_college

        # 3. Dates
        detail_date_str = detail_data.get("date_snapshot") or listing_date_formatted
        start_date_iso, end_date_iso = parse_date_range(detail_date_str)
        if not start_date_iso and listing_date_iso:
            start_date_iso = parse_single_date(listing_date_iso)

        # 4. Location & Event Mode
        location_snapshot = detail_data.get("location_snapshot") or listing_city
        event_mode_snapshot = detail_data.get("event_mode")
        location, event_mode = parse_location_and_mode(location_snapshot, event_mode_snapshot)

        # Split city/state if available for backward compatibility
        city = listing_city
        state = None
        if location_snapshot:
            parts = [p.strip() for p in location_snapshot.split(',') if p.strip()]
            if len(parts) >= 2:
                city = parts[0]
                state = parts[1]
            elif len(parts) == 1:
                city = parts[0]

        # 5. Status
        is_closed = detail_data.get("is_closed", False)
        if is_closed:
            status = "Closed"
        else:
            ref = REFERENCE_DATE
            s_dt = datetime.strptime(start_date_iso, "%Y-%m-%d").date() if start_date_iso else None
            e_dt = datetime.strptime(end_date_iso, "%Y-%m-%d").date() if end_date_iso else None
            if e_dt:
                if e_dt < ref:
                    status = "Completed"
                elif s_dt and s_dt <= ref <= e_dt:
                    status = "Ongoing"
                else:
                    status = "Upcoming"
            elif s_dt:
                if s_dt < ref:
                    status = "Completed"
                elif s_dt == ref:
                    status = "Ongoing"
                else:
                    status = "Upcoming"
            else:
                status = "Upcoming"

        # 6. Registration Deadline
        registration_deadline = detail_data.get("registration_deadline_iso")
        if not registration_deadline:
            logger.warning(f"[WARNING] Could not extract registration_deadline for: {title}")

        # 7. Team Size
        team_size_min = detail_data.get("team_size_min")
        team_size_max = detail_data.get("team_size_max")

        # 8. Prize
        prize = detail_data.get("prize")
        prize_amount = detail_data.get("prize_amount")
        currency = detail_data.get("currency")

        # 9. Tags
        raw_tags = []
        if detail_data.get("category"):
            raw_tags.append(detail_data["category"])
        raw_tags.extend(detail_data.get("eligible_departments") or [])
        events_details = detail_data.get("events_details") or ""
        for line in events_details.split('\n'):
            line = line.strip()
            if line.startswith(('•', '-', '*')):
                cleaned_line = re.sub(r'^[•\-\*–—\s]+', '', line).strip()
                if 3 <= len(cleaned_line) <= 40:
                    raw_tags.append(cleaned_line)
        tags = clean_tags(raw_tags)

        # 10. Metadata
        domain = urlparse(resolved_url).netloc.replace('www.', '') if resolved_url else "knowafest.com"
        scraped_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        # Raw record containing all 17 standardized fields
        raw_record: Dict[str, Any] = {
            "title": title,
            "organizer": organizer,
            "status": status,
            "location": location,
            "event_mode": event_mode,
            "start_date": start_date_iso,
            "end_date": end_date_iso,
            "registration_deadline": registration_deadline,
            "team_size_min": team_size_min,
            "team_size_max": team_size_max,
            "prize": prize,
            "prize_amount": prize_amount,
            "currency": currency,
            "tags": tags,
            "source_url": resolved_url,
            "source_website": domain,
            "scraped_at": scraped_at,
        }

        validated = validate_record(raw_record)
        if not validated:
            continue

        # Add backward-compatible fields
        validated["id"] = len(scraped_hackathons) + 1
        validated["event_name"] = title
        validated["college_name"] = organizer
        validated["city"] = city
        validated["state"] = state
        validated["country"] = "India" if (state or city) else None
        validated["start_date_formatted"] = detail_date_str or listing_date_formatted
        validated["category"] = detail_data.get("category")
        validated["eligible_departments"] = detail_data.get("eligible_departments")
        validated["registration_fee"] = detail_data.get("registration_fee")
        validated["registration_url"] = detail_data.get("registration_url")
        validated["official_website_url"] = detail_data.get("official_website_url")
        validated["brochure_url"] = detail_data.get("brochure_url")
        validated["organizer_email"] = detail_data.get("organizer_email")
        validated["contact_details"] = detail_data.get("contact_details")
        validated["venue_address"] = detail_data.get("venue_address")
        validated["about_event"] = detail_data.get("about_event")
        validated["events_summary"] = detail_data.get("events_details") or listing_summary
        validated["certificates_provided"] = detail_data.get("certificates_provided")
        validated["event_url"] = resolved_url

        log_scraped_summary(validated)
        scraped_hackathons.append(validated)

    logger.info(f"Scraping complete. Total valid records extracted: {len(scraped_hackathons)}.")
    return scraped_hackathons


# ==============================================================================
# LOCAL PERSISTENCE HELPERS
# ==============================================================================

def save_to_json(data: List[Dict[str, Any]], filepath: str) -> None:
    """Save records to a clean, indented JSON file."""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(data)} records to JSON: {filepath}")


def save_to_csv(data: List[Dict[str, Any]], filepath: str) -> None:
    """Save records to a CSV file, substituting missing values with N/A."""
    if not data:
        logger.warning("No data to save to CSV.")
        return

    # Standardized 17 fields first, followed by legacy fields
    standard_fields = [
        "title", "organizer", "status", "location", "event_mode",
        "start_date", "end_date", "registration_deadline",
        "team_size_min", "team_size_max", "prize", "prize_amount",
        "currency", "tags", "source_url", "source_website", "scraped_at"
    ]
    all_keys = list(data[0].keys())
    remaining_keys = [k for k in all_keys if k not in standard_fields]
    fieldnames = standard_fields + remaining_keys

    with open(filepath, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for item in data:
            row: Dict[str, Any] = {}
            for k in fieldnames:
                v = item.get(k)
                if v is None:
                    row[k] = "N/A"
                elif isinstance(v, list):
                    row[k] = ", ".join(str(x) for x in v) if v else "N/A"
                else:
                    row[k] = v
            writer.writerow(row)

    logger.info(f"Saved {len(data)} records to CSV: {filepath}")


# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Knowafest Hackathon Scraper & Supabase Sync")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of hackathons to scrape")
    parser.add_argument("--table", type=str, default=None, help="Supabase table name")
    parser.add_argument("--skip-supabase", action="store_true", help="Skip Supabase sync")
    args = parser.parse_args()

    json_path = "knowafest_hackathons.json"
    csv_path = "knowafest_hackathons.csv"

    results = scrape_knowafest_hackathons(limit=args.limit)

    if not results:
        logger.error("No hackathon records were extracted.")
        sys.exit(1)

    # Save local files
    save_to_json(results, json_path)
    save_to_csv(results, csv_path)

    # Sync to Supabase
    if not args.skip_supabase:
        sync_to_supabase(results, table_name=args.table)

    logger.info("Scraping, local export, and Supabase synchronization finished successfully.")


if __name__ == "__main__":
    main()
