"""
HackerEarth Challenges & Competitions Scraper
Powered by Scrapling (https://github.com/D4Vinci/Scrapling)

Extracts all structured information actually visible on each hackathon's detail
page and stores it in Supabase, JSON, and CSV.
Filters strictly for Ongoing and Upcoming challenges with zero hallucinations.
Missing values are recorded strictly as None / null (or N/A in CSV).
"""

import csv
from datetime import datetime, timezone
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urljoin, urlparse

from scrapling import Fetcher
try:
    from scrapling import StealthyFetcher
except Exception:
    StealthyFetcher = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("hackerearth_scraper")

# Ensure UTF-8 output on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_URL = "https://www.hackerearth.com"
CHALLENGES_URL = "https://www.hackerearth.com/challenges/"
COMPETE_API_URL = "https://www.hackerearth.com/api/community/challenges/compete/"
EVENT_API_BASE = "https://www.hackerearth.com/challengesapp/api/events/"

MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9, "sept": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


# ==============================================================================
# ENVIRONMENT & SUPABASE HELPERS
# ==============================================================================

def load_env_file(filepath: Optional[str] = None) -> None:
    """Load key-value pairs from a .env file into os.environ without third-party deps."""
    target_files = [filepath] if filepath else [".env", os.path.join(os.path.dirname(__file__), ".env")]
    for path in target_files:
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip().strip("\"'")
                        if key and key not in os.environ:
                            os.environ[key] = val
                logger.debug(f"Loaded environment variables from: {path}")
                break
            except Exception as e:
                logger.debug(f"Could not load .env file from {path}: {e}")


def get_supabase_credentials() -> Tuple[Optional[str], Optional[str], str]:
    """Retrieve Supabase URL, API key, and target table name from environment."""
    load_env_file()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    table = os.getenv("SUPABASE_TABLE", "hackathons")
    return url, key, table


# ==============================================================================
# PARSING UTILITIES (DATE, TEAM SIZE, PRIZE, LOCATION, TAGS)
# ==============================================================================

def clean_text(text: Optional[str]) -> Optional[str]:
    """Clean extra whitespaces while preserving None for missing text."""
    if text is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    return cleaned if cleaned else None


def normalize_url(url: Optional[str]) -> Optional[str]:
    """Ensure URLs are absolute."""
    if not url:
        return None
    url = url.strip()
    if url.startswith("/"):
        return urljoin(BASE_URL, url)
    return url


def extract_domain(url: Optional[str]) -> Optional[str]:
    """Extract clean domain/source_website from URL."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc or None
    except Exception:
        return None


def parse_single_date(text: Optional[str]) -> Optional[str]:
    """Normalize a single date string into strict ISO format (YYYY-MM-DD).

    Returns None if the date cannot be confidently parsed.
    """
    if not text:
        return None
    s = str(text).strip()
    if not s:
        return None

    # Check for direct ISO prefix YYYY-MM-DD (e.g. '2026-11-02T00:00:00' or '2026-11-02')
    m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m_iso:
        try:
            d = datetime(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
            return d.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Remove ordinal suffixes: 1st, 2nd, 3rd, 4th, etc.
    s_clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s, flags=re.IGNORECASE)

    # 1. Format: "2 Nov 2026" or "28 Oct 2026"
    m1 = re.search(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b", s_clean)
    if m1:
        day, month_str, year = int(m1.group(1)), m1.group(2).lower(), int(m1.group(3))
        month = MONTH_MAP.get(month_str[:3]) or MONTH_MAP.get(month_str)
        if month:
            try:
                return datetime(year, month, day).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # 2. Format: "Nov 2, 2026" or "November 2, 2026"
    m2 = re.search(r"\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b", s_clean)
    if m2:
        month_str, day, year = m2.group(1).lower(), int(m2.group(2)), int(m2.group(3))
        month = MONTH_MAP.get(month_str[:3]) or MONTH_MAP.get(month_str)
        if month:
            try:
                return datetime(year, month, day).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # 3. Format: "25/09/2026" or "2026/09/25"
    m3 = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", s_clean)
    if m3:
        d1, d2, year = int(m3.group(1)), int(m3.group(2)), int(m3.group(3))
        try:
            return datetime(year, d2, d1).strftime("%Y-%m-%d")
        except ValueError:
            try:
                return datetime(year, d1, d2).strftime("%Y-%m-%d")
            except ValueError:
                pass

    logger.debug(f"Could not parse date string: {text}")
    return None


def parse_date_range(text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Parse date range into (start_date, end_date) in ISO format YYYY-MM-DD.

    Supports both normal hyphen '-' and en dash '–' / em dash '—'.
    """
    if not text:
        return None, None
    s = str(text).strip()
    if not s:
        return None, None

    # Format: "Sep 25 – 26, 2026" or "Sep 25 - 26, 2026"
    m_same_month = re.search(r"([A-Za-z]+)\s+(\d{1,2})\s*[-–—]\s*(\d{1,2}),?\s+(\d{4})", s)
    if m_same_month:
        month_str = m_same_month.group(1).lower()
        d1, d2, year = int(m_same_month.group(2)), int(m_same_month.group(3)), int(m_same_month.group(4))
        month = MONTH_MAP.get(month_str[:3]) or MONTH_MAP.get(month_str)
        if month:
            try:
                sd = datetime(year, month, d1).strftime("%Y-%m-%d")
                ed = datetime(year, month, d2).strftime("%Y-%m-%d")
                return sd, ed
            except ValueError:
                pass

    # Split on range separator: "2 Nov 2026 – 3 Nov 2026" or "Nov 2, 2026 - Nov 3, 2026"
    parts = re.split(r"\s+[-–—]\s+|\s+to\s+", s)
    if len(parts) == 2:
        sd = parse_single_date(parts[0])
        ed = parse_single_date(parts[1])
        if not sd and ed:
            m_year = re.search(r"\d{4}", parts[1])
            if m_year:
                sd = parse_single_date(parts[0] + " " + m_year.group(0))
        return sd, ed

    # Single date format: "2 Nov 2026"
    sd = parse_single_date(s)
    return sd, None


def parse_team_size(raw_val: Any, is_team: Optional[bool] = None) -> Tuple[Optional[int], Optional[int]]:
    """Parse team size dynamically into (team_size_min, team_size_max).

    Supports:
      "1-3" -> (1, 3)
      "2–5" -> (2, 5)
      "1" -> (1, 1)
      Individual integers or strings
    """
    if raw_val is None:
        if is_team is False:
            return 1, 1
        return None, None

    # If raw_val is already integer
    if isinstance(raw_val, int):
        return raw_val, raw_val

    s = str(raw_val).strip()
    if not s:
        if is_team is False:
            return 1, 1
        return None, None

    # Range pattern: "1-3", "2–5", "2 to 4 members"
    m_range = re.search(r"(\d+)\s*[-–—to]+\s*(\d+)", s, flags=re.IGNORECASE)
    if m_range:
        try:
            t_min = int(m_range.group(1))
            t_max = int(m_range.group(2))
            if t_min > t_max:
                t_min, t_max = t_max, t_min
            return t_min, t_max
        except ValueError:
            pass

    # Single number: "1", "2 members"
    m_single = re.search(r"\b(\d+)\b", s)
    if m_single:
        try:
            val = int(m_single.group(1))
            return val, val
        except ValueError:
            pass

    if is_team is False:
        return 1, 1

    return None, None


def parse_prize(raw_val: Any) -> Tuple[Optional[str], Optional[float], Optional[str]]:
    """Parse prize text into (prize, prize_amount, currency).

    Preserves the original prize string while dynamically computing
    the numeric amount and currency code.
    """
    if raw_val is None:
        return None, None, None
    s = str(raw_val).strip()
    if not s or s.lower() in ("false", "none", "null", "n/a"):
        return None, None, None

    prize_str = s
    currency = None

    # Currency identification with word boundaries
    if "₹" in s or re.search(r"\b(?:inr|rs\.?|rupees?)\b", s, flags=re.IGNORECASE):
        currency = "INR"
    elif "$" in s or re.search(r"\b(?:usd|dollars?)\b", s, flags=re.IGNORECASE):
        currency = "USD"
    elif "€" in s or re.search(r"\b(?:eur|euros?)\b", s, flags=re.IGNORECASE):
        currency = "EUR"
    elif "£" in s or re.search(r"\b(?:gbp|pounds?)\b", s, flags=re.IGNORECASE):
        currency = "GBP"

    # 1. Denominations: Lakh / Lac (100,000)
    m_lakh = re.search(r"([\d\.]+)\s*(?:lakh|lac|l)\b", s, flags=re.IGNORECASE)
    if m_lakh:
        try:
            amount = float(m_lakh.group(1)) * 100000
            return prize_str, amount, currency or "INR"
        except ValueError:
            pass

    # 2. Denominations: Crore / Cr (10,000,000)
    m_cr = re.search(r"([\d\.]+)\s*(?:crore|cr)\b", s, flags=re.IGNORECASE)
    if m_cr:
        try:
            amount = float(m_cr.group(1)) * 10000000
            return prize_str, amount, currency or "INR"
        except ValueError:
            pass

    # 3. Denominations: Millions 'M' (1,000,000)
    m_m = re.search(r"([\d\.]+)\s*m\b", s, flags=re.IGNORECASE)
    if m_m and "team" not in s.lower():
        try:
            amount = float(m_m.group(1)) * 1000000
            return prize_str, amount, currency
        except ValueError:
            pass

    # 4. Denominations: Thousands 'k' (1,000)
    m_k = re.search(r"([\d\.]+)\s*k\b", s, flags=re.IGNORECASE)
    if m_k:
        try:
            amount = float(m_k.group(1)) * 1000
            return prize_str, amount, currency
        except ValueError:
            pass

    # 5. Standard numbers: e.g. "1,00,000", "10,000", "500"
    m_num = re.search(r"[\$₹€£]?\s*([\d,]+(?:\.\d+)?)", s)
    if m_num:
        raw_num = m_num.group(1).replace(",", "")
        try:
            amount = float(raw_num)
            return prize_str, amount, currency
        except ValueError:
            pass

    return prize_str, None, currency


def parse_location_and_mode(
    raw_loc: Optional[str],
    raw_mode: Optional[str] = None,
    text_content: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Extract location separately from event mode.

    Normalizes "Online (Online)" -> location="Online", event_mode="Online".
    Extracts physical locations and sets event_mode="Offline" accordingly.
    """
    loc_clean = clean_text(raw_loc)
    event_mode = None

    if raw_mode:
        m = raw_mode.strip().capitalize()
        if m in ("Online", "Offline", "Hybrid"):
            event_mode = m

    # Check for text like "Online (Online)"
    if loc_clean and re.match(r"^online\s*\(\s*online\s*\)$", loc_clean, flags=re.IGNORECASE):
        loc_clean = "Online"
        if not event_mode:
            event_mode = "Online"
    elif loc_clean and loc_clean.lower() == "online":
        loc_clean = "Online"
        if not event_mode:
            event_mode = "Online"

    # Fallback to search in description/microsite text if location is missing
    if not loc_clean and text_content:
        m_loc = re.search(r"(?:Location|Venue)\s*[:\-]\s*([^\r\n<]+)", text_content, flags=re.IGNORECASE)
        if m_loc:
            cand = clean_text(m_loc.group(1))
            if cand:
                loc_clean = cand
                if cand.lower() == "online":
                    loc_clean = "Online"
                    event_mode = "Online"

    if not event_mode:
        if loc_clean:
            if loc_clean.lower() == "online":
                event_mode = "Online"
            elif any(w in loc_clean.lower() for w in ("hybrid", "virtual + in-person")):
                event_mode = "Hybrid"
            else:
                event_mode = "Offline"

    return loc_clean or None, event_mode or None


def clean_tags(tags: Any) -> List[str]:
    """Clean and deduplicate tag strings while preserving order."""
    if not tags:
        return []
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,;|]", tags)]
    result = []
    seen = set()
    for t in tags:
        if isinstance(t, str):
            clean_t = t.strip()
            if clean_t and clean_t.lower() not in seen:
                seen.add(clean_t.lower())
                result.append(clean_t)
    return result


def determine_status(start_iso: Optional[str], end_iso: Optional[str]) -> Optional[str]:
    """Accurately determine status based on current UTC time."""
    if not start_iso or not end_iso:
        return None

    try:
        now_utc = datetime.now(timezone.utc)
        start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))

        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)

        if now_utc < start_dt:
            return "Upcoming"
        elif start_dt <= now_utc <= end_dt:
            return "Ongoing"
        else:
            return "Past"
    except Exception as e:
        logger.debug(f"Failed to calculate date status for {start_iso} / {end_iso}: {e}")
        return None


# ==============================================================================
# VALIDATION & LOGGING
# ==============================================================================

def validate_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Validate extracted fields against data integrity rules.

    Invalid optional fields become None/empty rather than corrupting records.
    """
    # 1. Title validation
    if not record.get("title") or not str(record["title"]).strip():
        logger.warning(f"[WARNING] Record missing title for URL: {record.get('source_url')}")

    # 2. Date validations
    start_date = record.get("start_date")
    end_date = record.get("end_date")
    if start_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(start_date)):
        logger.warning(f"[WARNING] Invalid start_date '{start_date}' for {record.get('source_url')}")
        record["start_date"] = None
    if end_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(end_date)):
        logger.warning(f"[WARNING] Invalid end_date '{end_date}' for {record.get('source_url')}")
        record["end_date"] = None

    if record.get("start_date") and record.get("end_date"):
        if record["start_date"] > record["end_date"]:
            logger.warning(
                f"[WARNING] start_date ({record['start_date']}) > end_date ({record['end_date']}) "
                f"for {record.get('source_url')}. Setting end_date to None."
            )
            record["end_date"] = None

    reg_deadline = record.get("registration_deadline")
    if reg_deadline and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(reg_deadline)):
        logger.warning(f"[WARNING] Invalid registration_deadline '{reg_deadline}' for {record.get('source_url')}")
        record["registration_deadline"] = None

    # 3. Team size validations
    t_min = record.get("team_size_min")
    t_max = record.get("team_size_max")
    if t_min is not None and not isinstance(t_min, int):
        record["team_size_min"] = None
    if t_max is not None and not isinstance(t_max, int):
        record["team_size_max"] = None
    if record.get("team_size_min") is not None and record.get("team_size_max") is not None:
        if record["team_size_min"] > record["team_size_max"]:
            logger.warning(
                f"[WARNING] team_size_min ({record['team_size_min']}) > team_size_max ({record['team_size_max']}) "
                f"for {record.get('source_url')}."
            )
            record["team_size_min"], record["team_size_max"] = record["team_size_max"], record["team_size_min"]

    # 4. Prize validation
    p_amount = record.get("prize_amount")
    if p_amount is not None:
        if not isinstance(p_amount, (int, float)):
            record["prize_amount"] = None
        elif record.get("currency") is None:
            logger.warning(f"[WARNING] prize_amount present without currency for {record.get('source_url')}")

    # 5. Tags validation
    if not isinstance(record.get("tags"), list):
        record["tags"] = []
    else:
        record["tags"] = [str(t).strip() for t in record["tags"] if str(t).strip()]

    return record


def log_scraped_hackathon(r: Dict[str, Any]) -> None:
    """Log structured extraction details for verification."""
    team_str = (
        f"{r.get('team_size_min')} - {r.get('team_size_max')}"
        if r.get("team_size_min") is not None and r.get("team_size_max") is not None
        else (str(r.get("team_size_min")) if r.get("team_size_min") is not None else "None")
    )
    tags_str = ", ".join(r.get("tags", [])) if r.get("tags") else "[]"
    prize_amt_str = f"{r.get('prize_amount'):,.0f}" if r.get("prize_amount") is not None else "None"

    lines = [
        "\n" + "=" * 60,
        "[SCRAPED]",
        f"Title:                 {r.get('title')}",
        f"Organizer:             {r.get('organizer')}",
        f"Status:                {r.get('status')}",
        f"Location:              {r.get('location')}",
        f"Event Mode:            {r.get('event_mode')}",
        f"Start Date:            {r.get('start_date')}",
        f"End Date:              {r.get('end_date')}",
        f"Registration Deadline: {r.get('registration_deadline')}",
        f"Team Size:             {team_str}",
        f"Prize:                 {r.get('prize')}",
        f"Prize Amount:          {prize_amt_str}",
        f"Currency:              {r.get('currency')}",
        f"Tags:                  {tags_str}",
        f"Source URL:            {r.get('source_url')}",
        f"Source Website:        {r.get('source_website')}",
        f"Scraped At:            {r.get('scraped_at')}",
        "=" * 60,
    ]
    for line in lines:
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode("ascii", errors="replace").decode("ascii"))


# ==============================================================================
# SUPABASE SYNC / UPSERT
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

    # Prepare records for Supabase schema and deduplicate on source_url
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

    # Attempt 1: Try using supabase-py client if installed
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

    # Attempt 2: Direct PostgREST REST API via standard library urllib
    try:
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=representation",
            "User-Agent": "HackerEarthScraper/1.0",
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

def extract_from_rsc_or_html(
    page_html: str,
    target_url: str,
) -> Dict[str, Any]:
    """Extract structured information from detail page HTML and Next.js RSC streams."""
    extra = {
        "start_date": None,
        "end_date": None,
        "registration_deadline": None,
        "organizer": None,
        "team_size_min": None,
        "team_size_max": None,
        "prize": None,
        "prize_amount": None,
        "currency": None,
        "location": None,
        "event_mode": None,
        "status": None,
        "tags": [],
    }

    if not page_html:
        return extra

    # 1. Look for Next.js RSC stream chunks: self.__next_f.push([1, "..."])
    pushes = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', page_html)
    full_rsc = ""
    for p in pushes:
        try:
            full_rsc += p.encode("utf-8").decode("unicode_escape", errors="ignore")
        except Exception:
            full_rsc += p

    corpus = full_rsc + "\n" + page_html

    # Extract dates from RSC objects
    m_sd = re.search(r'"start_date":\s*"([^"]+)"', full_rsc)
    if m_sd:
        extra["start_date"] = parse_single_date(m_sd.group(1))

    m_ed = re.search(r'"end_date":\s*"([^"]+)"', full_rsc)
    if m_ed:
        extra["end_date"] = parse_single_date(m_ed.group(1))

    # Extract organizer from RSC organizer object
    m_org = re.search(r'"organizer":\s*\[\s*\{[^{}]*"title":\s*"([^"]+)"', full_rsc)
    if m_org:
        extra["organizer"] = clean_text(m_org.group(1))

    # Extract team size from RSC
    m_min = re.search(r'"min_team_size":\s*(\d+)', full_rsc)
    m_max = re.search(r'"max_team_size":\s*(\d+)', full_rsc)
    if m_min and m_max:
        extra["team_size_min"] = int(m_min.group(1))
        extra["team_size_max"] = int(m_max.group(1))

    # Extract location from corpus
    m_loc = re.search(r"(?:Location|Venue)\s*[:\-]\s*([^\r\n<]+)", corpus, flags=re.IGNORECASE)
    if m_loc:
        cand = clean_text(m_loc.group(1))
        # Strip common trailing noise
        if cand:
            cand = re.sub(r'["\',].*$', '', cand).strip()
            loc, mode = parse_location_and_mode(cand)
            extra["location"] = loc
            extra["event_mode"] = mode

    # Check if prize widget is explicitly disabled in HackerEarth events
    prize_disabled = False
    if re.search(r'"widgets"\s*:\s*\{[^{}]*"prize"\s*:\s*false', full_rsc):
        prize_disabled = True

    # Extract prize from corpus (look for explicit prize pool or rewards)
    # Check for formats like "★ ₹10 Lakh Prize Pool", "₹10L Total prize pool", "Prize: ₹1,00,000", "₹45,00,000 in prizes"
    prize_candidates = []
    if not prize_disabled:
        patterns = [
            r'([★\s]*[\$₹€£]\s*[\d\.,]+(?:\s*(?:lakh|lac|crore|cr|k|m))?)\s*(?:in\s*(?:cash\s*)?prizes?|total\s*prize|prize\s*pool|cash\s*prize|rewards?|prize\b)',
            r'(?:cash\s*prizes?\s*(?:worth\s*(?:up\s*to)?)?|prizes?\s*(?:pool)?|rewards?)\s*[:\-]?\s*([★\s]*[\$₹€£]?\s*[\d\.,]+(?:\s*(?:lakh|lac|crore|cr|k|m))?\b[^\r\n<]{0,35})',
            r'[★\s]*([\$₹€£]\s*[\d\.,]+\s*(?:lakh|lac|crore|cr|k|m)?\s*(?:prize\s*pool|total\s*prize))',
        ]
        for pat in patterns:
            for m in re.finditer(pat, corpus, flags=re.IGNORECASE):
                match_str = clean_text(m.group(0))
                if match_str and any(c.isdigit() for c in match_str):
                    # Discard Next.js RSC internal reference tokens like '$19'
                    if re.match(r'^\$\d+$', match_str.strip()):
                        continue
                    prize_candidates.append(match_str)

    if prize_candidates:
        # Prefer the candidate with the highest parsed amount or richest text
        best_cand = None
        best_amt = None
        best_curr = None
        for cand in prize_candidates:
            p_str, p_amt, curr = parse_prize(cand)
            if p_amt is not None and (best_amt is None or p_amt > best_amt):
                best_cand = p_str
                best_amt = p_amt
                best_curr = curr
            elif best_cand is None:
                best_cand = p_str
                best_curr = curr

        extra["prize"] = best_cand
        extra["prize_amount"] = best_amt
        extra["currency"] = best_curr

    # Extract registration deadline from corpus
    m_dl = re.search(
        r"(?:Registration\s*(?:closes|deadline|ends)|Deadline)\s*[:\-]\s*([^\r\n<]+)",
        corpus,
        flags=re.IGNORECASE,
    )
    if m_dl:
        cand_dl = clean_text(m_dl.group(1))
        parsed_dl = parse_single_date(cand_dl)
        if parsed_dl:
            extra["registration_deadline"] = parsed_dl
        else:
            logger.warning(f"[WARNING] Could not extract registration_deadline for: {target_url}")

    return extra


# ==============================================================================
# MAIN SCRAPING PIPELINE
# ==============================================================================

def scrape_challenges() -> List[Dict[str, Any]]:
    """Main scraping function using Scrapling (filters for Ongoing and Upcoming only).

    Extracts all 17 required fields dynamically with zero hallucinations,
    validates integrity, syncs to Supabase, and persists to JSON and CSV.
    """
    logger.info("Initializing HackerEarth Challenges scraping via Scrapling...")

    # 1. Verify live portal using StealthyFetcher if available
    logger.info("Verifying live challenges portal: %s", CHALLENGES_URL)
    if StealthyFetcher is not None:
        try:
            page = StealthyFetcher.fetch(CHALLENGES_URL, headless=True)
            logger.info("Portal response status: %s (Page title: '%s')", page.status, page.css("title::text").get())
        except Exception as e:
            logger.warning(f"StealthyFetcher portal preview warning: {e}. Continuing with direct Fetcher...")
    else:
        logger.info("StealthyFetcher engine not active. Continuing with direct Fetcher...")

    # 2. Fetch the structured challenges catalogue via Fetcher
    logger.info("Fetching challenges list from API: %s", COMPETE_API_URL)
    response = Fetcher.get(COMPETE_API_URL)
    if response.status != 200:
        logger.error(f"Failed to fetch challenges API. Status: {response.status}")
        sys.exit(1)

    try:
        raw_data = json.loads(response.body)
        challenges_list = raw_data.get("data", [])
        total_count = raw_data.get("total", len(challenges_list))
    except Exception as e:
        logger.error(f"Failed to parse challenges JSON: {e}")
        sys.exit(1)

    logger.info(f"Retrieved {len(challenges_list)} challenges from portal index (reported total: {total_count}).")

    # Filter strictly for Ongoing and Upcoming challenges
    active_challenges = []
    skipped_past_count = 0

    for item in challenges_list:
        status = determine_status(item.get("start"), item.get("end"))
        if status in ("Ongoing", "Upcoming"):
            active_challenges.append((item, status))
        else:
            skipped_past_count += 1

    logger.info(
        f"Identified {len(active_challenges)} active challenges to scrape "
        f"({skipped_past_count} skipped because they are Past / concluded)."
    )

    scraped_records: List[Dict[str, Any]] = []

    # 3. For each Ongoing / Upcoming challenge, extract full event details
    for i, (item, calculated_status) in enumerate(active_challenges, 1):
        slug = item.get("slug")
        title = clean_text(item.get("title"))
        challenge_type = clean_text(item.get("type"))
        start_time = item.get("start")
        start_formatted = item.get("start_str")
        end_time = item.get("end")
        end_formatted = item.get("end_str")
        raw_url = item.get("url")
        full_url = normalize_url(raw_url)
        company_name = clean_text(item.get("company_name"))
        image_url = item.get("image_url")
        listing_image = item.get("listing_image")
        min_team = item.get("min_team_size")
        max_team = item.get("max_team_size")
        subs_count = item.get("subscription_count")

        logger.info(f"[{i}/{len(active_challenges)}] Fetching details for [{calculated_status}] '{title}' (slug: {slug})...")

        description_text = None
        tags = []
        organizer_info = None
        short_url = None
        cover_image = None
        is_team = None
        event_category = None
        phases = None

        # Fetch event REST API if slug is available
        if slug:
            event_api_url = f"{EVENT_API_BASE}{slug}/"
            try:
                event_res = Fetcher.get(event_api_url)
                if event_res.status == 200:
                    event_data = json.loads(event_res.body)
                    description_text = clean_text(event_data.get("description_text"))
                    tags = clean_tags(event_data.get("tags"))
                    short_url = event_data.get("short_url")
                    cover_image = event_data.get("cover_image")
                    is_team = event_data.get("is_team")
                    event_category = event_data.get("event_category")
                    phases = event_data.get("phases")

                    # Extract organizer details if present
                    org_list = event_data.get("organizer", [])
                    if isinstance(org_list, list) and len(org_list) > 0:
                        primary_org = org_list[0]
                        organizer_info = {
                            "title": clean_text(primary_org.get("title")),
                            "description": clean_text(primary_org.get("description_text")),
                            "image": primary_org.get("image"),
                            "type": primary_org.get("type"),
                        }
                        if not company_name and primary_org.get("title"):
                            company_name = clean_text(primary_org.get("title"))

                    # Team size from event API
                    if min_team is None and event_data.get("min_team_size") is not None:
                        min_team = event_data.get("min_team_size")
                    if max_team is None and event_data.get("max_team_size") is not None:
                        max_team = event_data.get("max_team_size")
            except Exception as e:
                logger.debug(f"Could not fetch extra details from API for {slug}: {e}")

        # Fetch detail page HTML for dynamic RSC / DOM extraction
        page_html = ""
        detail_fetch_url = full_url or short_url
        if detail_fetch_url:
            try:
                page_res = Fetcher.get(detail_fetch_url)
                if page_res.status == 200:
                    page_html = page_res.body.decode("utf-8", errors="ignore")
            except Exception as e:
                logger.debug(f"Could not fetch detail page HTML for {detail_fetch_url}: {e}")

        # Extract rich structured fields from detail page RSC / HTML
        rsc_extra = extract_from_rsc_or_html(page_html, detail_fetch_url or "")

        # ----------------------------------------------------------------------
        # Field Normalization
        # ----------------------------------------------------------------------
        # Organizer
        organizer = company_name or rsc_extra.get("organizer")
        if not organizer and organizer_info and organizer_info.get("title"):
            organizer = organizer_info.get("title")

        # Start Date
        start_date = parse_single_date(start_time) or rsc_extra.get("start_date")
        if not start_date and start_formatted:
            start_date = parse_single_date(start_formatted)

        # End Date
        end_date = parse_single_date(end_time) or rsc_extra.get("end_date")
        if not end_date and end_formatted:
            end_date = parse_single_date(end_formatted)

        # Registration Deadline: check phases first, then detail page
        reg_deadline = None
        if isinstance(phases, list):
            for ph in phases:
                ph_title = ph.get("title", "").lower()
                if "registration" in ph_title or "submission" in ph_title:
                    reg_deadline = parse_single_date(ph.get("end"))
                    if reg_deadline:
                        break
        if not reg_deadline:
            reg_deadline = rsc_extra.get("registration_deadline")

        # Team Size: combine min_team, max_team, and is_team
        team_size_min, team_size_max = parse_team_size(
            f"{min_team}-{max_team}" if min_team is not None and max_team is not None else min_team,
            is_team=is_team
        )
        if team_size_min is None and rsc_extra.get("team_size_min") is not None:
            team_size_min = rsc_extra["team_size_min"]
            team_size_max = rsc_extra.get("team_size_max")

        # Location & Event Mode
        location, event_mode = parse_location_and_mode(
            rsc_extra.get("location"),
            rsc_extra.get("event_mode"),
            text_content=description_text or page_html
        )

        # Prize & Currency
        prize = rsc_extra.get("prize")
        prize_amount = rsc_extra.get("prize_amount")
        currency = rsc_extra.get("currency")

        # If prize not in RSC, check description_text
        if not prize and description_text:
            m_prz = re.search(r"(?:Prize|Rewards?)\s*[:\-]\s*([^\r\n]+)", description_text, flags=re.IGNORECASE)
            if m_prz:
                cand_prz = clean_text(m_prz.group(1))
                if cand_prz and any(c.isdigit() for c in cand_prz):
                    p_str, p_amt, p_curr = parse_prize(cand_prz)
                    prize, prize_amount, currency = p_str, p_amt, p_curr

        # Tags: combine and deduplicate
        all_tags = clean_tags(tags + (rsc_extra.get("tags") or []))

        # Metadata
        canonical_url = full_url or short_url
        source_website = extract_domain(canonical_url)
        scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        record = {
            # 17 Target Standard Fields
            "title": title,
            "organizer": organizer,
            "status": calculated_status,
            "location": location,
            "event_mode": event_mode,
            "start_date": start_date,
            "end_date": end_date,
            "registration_deadline": reg_deadline,
            "team_size_min": team_size_min,
            "team_size_max": team_size_max,
            "prize": prize,
            "prize_amount": prize_amount,
            "currency": currency,
            "tags": all_tags,
            "source_url": canonical_url,
            "source_website": source_website,
            "scraped_at": scraped_at,

            # Extended / Legacy Fields (preserved for backward compatibility)
            "slug": slug,
            "challenge_type": challenge_type,
            "company_name": company_name,
            "registrations_count": subs_count,
            "start_time_utc": start_time,
            "start_time_formatted": start_formatted,
            "end_time_utc": end_time,
            "end_time_formatted": end_formatted,
            "url": full_url,
            "short_url": short_url,
            "min_team_size": team_size_min,
            "max_team_size": team_size_max,
            "is_team": is_team,
            "event_category": event_category,
            "description_text": description_text,
            "organizer_details": organizer_info,
            "image_url": image_url,
            "listing_image": listing_image,
            "cover_image": cover_image,
        }

        # Validate record
        validated_record = validate_record(record)
        scraped_records.append(validated_record)

        # Log detailed output for verification
        log_scraped_hackathon(validated_record)

    # 4. Sync to Supabase
    sync_to_supabase(scraped_records)

    # 5. Save to JSON file
    json_filepath = "hackerearth_challenges.json"
    logger.info(f"Saving {len(scraped_records)} ongoing & upcoming items to {json_filepath}...")
    with open(json_filepath, "w", encoding="utf-8") as f:
        json.dump(scraped_records, f, indent=2, ensure_ascii=False)

    # 6. Save to CSV file (with N/A for missing tabular fields)
    csv_filepath = "hackerearth_challenges.csv"
    logger.info(f"Saving {len(scraped_records)} ongoing & upcoming items to {csv_filepath}...")
    fieldnames = [
        "title",
        "organizer",
        "status",
        "location",
        "event_mode",
        "start_date",
        "end_date",
        "registration_deadline",
        "team_size_min",
        "team_size_max",
        "prize",
        "prize_amount",
        "currency",
        "tags",
        "source_url",
        "source_website",
        "scraped_at",
        # Legacy columns
        "slug",
        "challenge_type",
        "company_name",
        "registrations_count",
        "start_time_utc",
        "start_time_formatted",
        "end_time_utc",
        "end_time_formatted",
        "url",
        "short_url",
        "min_team_size",
        "max_team_size",
        "is_team",
        "event_category",
        "organizer_name",
        "image_url",
        "listing_image",
        "cover_image",
        "description_text",
    ]

    with open(csv_filepath, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in scraped_records:
            tags_str = ", ".join(r["tags"]) if isinstance(r.get("tags"), list) and r["tags"] else "N/A"
            org_name = (
                r.get("organizer")
                or (r.get("organizer_details", {}).get("title") if r.get("organizer_details") else None)
                or "N/A"
            )

            row = {
                "title": r.get("title") or "N/A",
                "organizer": r.get("organizer") or "N/A",
                "status": r.get("status") or "N/A",
                "location": r.get("location") or "N/A",
                "event_mode": r.get("event_mode") or "N/A",
                "start_date": r.get("start_date") or "N/A",
                "end_date": r.get("end_date") or "N/A",
                "registration_deadline": r.get("registration_deadline") or "N/A",
                "team_size_min": r.get("team_size_min") if r.get("team_size_min") is not None else "N/A",
                "team_size_max": r.get("team_size_max") if r.get("team_size_max") is not None else "N/A",
                "prize": r.get("prize") or "N/A",
                "prize_amount": r.get("prize_amount") if r.get("prize_amount") is not None else "N/A",
                "currency": r.get("currency") or "N/A",
                "tags": tags_str,
                "source_url": r.get("source_url") or "N/A",
                "source_website": r.get("source_website") or "N/A",
                "scraped_at": r.get("scraped_at") or "N/A",
                # Legacy columns
                "slug": r.get("slug") or "N/A",
                "challenge_type": r.get("challenge_type") or "N/A",
                "company_name": r.get("company_name") or "N/A",
                "registrations_count": r.get("registrations_count") if r.get("registrations_count") is not None else "N/A",
                "start_time_utc": r.get("start_time_utc") or "N/A",
                "start_time_formatted": r.get("start_time_formatted") or "N/A",
                "end_time_utc": r.get("end_time_utc") or "N/A",
                "end_time_formatted": r.get("end_time_formatted") or "N/A",
                "url": r.get("url") or "N/A",
                "short_url": r.get("short_url") or "N/A",
                "min_team_size": r.get("min_team_size") if r.get("min_team_size") is not None else "N/A",
                "max_team_size": r.get("max_team_size") if r.get("max_team_size") is not None else "N/A",
                "is_team": r.get("is_team") if r.get("is_team") is not None else "N/A",
                "event_category": r.get("event_category") or "N/A",
                "organizer_name": org_name,
                "image_url": r.get("image_url") or "N/A",
                "listing_image": r.get("listing_image") or "N/A",
                "cover_image": r.get("cover_image") or "N/A",
                "description_text": (r.get("description_text") or "N/A").replace("\n", " ").replace("\r", " "),
            }
            writer.writerow(row)

    logger.info(f"Successfully completed scraping! Total ongoing & upcoming challenges: {len(scraped_records)}.")
    return scraped_records


if __name__ == "__main__":
    scrape_challenges()
