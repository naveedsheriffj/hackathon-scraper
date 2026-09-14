"""
Unstop Hackathon Scraper & Supabase Synchronization
Powered by Scrapling (https://github.com/D4Vinci/Scrapling)

Extracts all structured information visible on each Unstop hackathon's detail page
and synchronizes with an existing Supabase PostgreSQL database.
Strictly preserves authenticity: missing or unavailable fields are kept as null (None).
Never hallucinates, invents, or hardcodes values.
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from scrapling.fetchers import AsyncFetcher
from scrapling.parser import Adaptor

# Configure UTF-8 encoding on Windows to prevent charmap errors with currency symbols
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("unstop_scraper")

BASE_API_URL = "https://unstop.com/api/public/opportunity/search-result"
DETAIL_API_URL = "https://unstop.com/api/public/competition/{id}"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://unstop.com/hackathons",
    "Origin": "https://unstop.com",
}


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
# TEXT & STRING CLEANING HELPERS
# ==============================================================================

def clean_text(text: Optional[str]) -> Optional[str]:
    """Clean whitespace, zero-width spaces, and formatting while preserving text."""
    if not text or not isinstance(text, str):
        return None
    cleaned = text.replace("\u200b", "").replace("\xa0", " ").replace("\ufffd", "")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\r\n|\r|\n", "\n", cleaned)
    cleaned = re.sub(r"\n\s*\n+", "\n\n", cleaned).strip()
    return cleaned if cleaned else None


def clean_html(raw_html: Optional[str]) -> Optional[str]:
    """Convert HTML string to clean plain text using Scrapling Adaptor."""
    if not raw_html or not isinstance(raw_html, str) or not raw_html.strip():
        return None
    try:
        adaptor = Adaptor(raw_html)
        text_nodes = adaptor.css("::text").getall()
        chunks = [t.strip() for t in text_nodes if t.strip()]
        if not chunks:
            return None
        text = " ".join(chunks)
        text = re.sub(r"\s+", " ", text).strip()
        return text if text else None
    except Exception:
        cleaned = re.sub(r"<[^>]+>", " ", raw_html)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned if cleaned else None


# ==============================================================================
# DATE PARSING ENGINE
# ==============================================================================

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


def parse_single_date(date_str: Optional[str]) -> Optional[str]:
    """
    Parse a single date string into strict ISO YYYY-MM-DD format.
    Never hallucinates or invents dates. Returns None if unparseable.
    """
    if not date_str or not isinstance(date_str, str):
        return None

    cleaned = clean_text(date_str) or ""

    # Strip common prefixes like 'Registration closes: ', 'Deadline: ', etc.
    prefixes = [
        r"^(?:last\s+dates?\s+for\s+registration|last\s+date\s+for\s+registration|registration\s+deadline|registration\s+closes?|prelims\s+ppt\s+submission\s+deadline|important\s+dates|deadline|starts?\s+on|ends?\s+on|date)\s*[:–—\-]?\s*",
    ]
    for p in prefixes:
        cleaned = re.sub(p, "", cleaned, flags=re.IGNORECASE).strip()

    # Match ISO datetime string like 2026-08-17T16:00:00+05:30 or 2026-11-02
    m_iso = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T\s]|$)", cleaned)
    if m_iso:
        try:
            return datetime(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Remove ordinal suffixes: 1st, 2nd, 3rd, 4th, etc.
    cleaned_ord = re.sub(r"(\d+)(?:st|nd|rd|th)\b", r"\1", cleaned, flags=re.IGNORECASE)

    # Match format like: '2 Nov 2026', '02 November 2026', '2-Nov-2026'
    m_text = re.search(r"\b(\d{1,2})[\s\-\./]+([a-zA-Z]+)[\s\-\./]+(\d{4})\b", cleaned_ord)
    if m_text:
        day_str, month_str, year_str = m_text.group(1), m_text.group(2).lower(), m_text.group(3)
        month_num = MONTH_MAP.get(month_str)
        if month_num:
            try:
                return datetime(int(year_str), month_num, int(day_str)).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # Match format like: 'November 2, 2026' or 'Nov 2 2026'
    m_text_rev = re.search(r"\b([a-zA-Z]+)[\s\-\./]+(\d{1,2}),?[\s\-\./]+(\d{4})\b", cleaned_ord)
    if m_text_rev:
        month_str, day_str, year_str = m_text_rev.group(1).lower(), m_text_rev.group(2), m_text_rev.group(3)
        month_num = MONTH_MAP.get(month_str)
        if month_num:
            try:
                return datetime(int(year_str), month_num, int(day_str)).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # Match numeric formats like DD-MM-YYYY, DD/MM/YYYY, DD.MM.YYYY
    m_num = re.search(r"\b(\d{1,2})[\-/\.](\d{1,2})[\-/\.](\d{4})\b", cleaned)
    if m_num:
        d, m, y = int(m_num.group(1)), int(m_num.group(2)), int(m_num.group(3))
        # Swap day and month if month > 12
        if d > 12 >= m:
            pass
        elif m > 12 >= d:
            d, m = m, d
        try:
            return datetime(y, m, d).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def parse_date_range(range_str: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse date range text into strict ISO (start_date, end_date) in YYYY-MM-DD format.
    Supports hyphens, en-dashes, em-dashes, and 'to'.
    """
    if not range_str or not isinstance(range_str, str):
        return None, None

    cleaned = clean_text(range_str) or ""

    # Check for range separator: ' – ', ' - ', ' — ', ' to ', etc.
    split_pattern = r"\s*(?:–|—|\bto\b|\s-\s)\s*"
    parts = re.split(split_pattern, cleaned, flags=re.IGNORECASE)

    if len(parts) >= 2:
        part1 = parts[0].strip()
        part2 = parts[1].strip()

        # Check if part1 is missing month/year (e.g. '26' in '26-27 September 2026')
        m_day_only = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?$", part1, re.IGNORECASE)
        if m_day_only:
            # Extract month and year from part2
            m_m_y = re.search(r"([a-zA-Z]+)[\s\-\./]+(\d{4})", part2)
            if m_m_y:
                part1 = f"{m_day_only.group(1)} {m_m_y.group(1)} {m_m_y.group(2)}"

        # Check if part1 is missing year (e.g. '28 Oct' in '28 Oct - 2 Nov 2026')
        m_no_year = re.match(r"^(\d{1,2}(?:st|nd|rd|th)?[\s\-]+[a-zA-Z]+)$", part1, re.IGNORECASE)
        if m_no_year:
            m_year = re.search(r"\b(\d{4})\b", part2)
            if m_year:
                part1 = f"{part1} {m_year.group(1)}"

        s_date = parse_single_date(part1)
        e_date = parse_single_date(part2)

        if s_date and e_date:
            if s_date <= e_date:
                return s_date, e_date
            else:
                logger.warning(f"[WARNING] Start date {s_date} is after end date {e_date} for range: {range_str}")
                return s_date, None
        elif s_date:
            return s_date, None
        elif e_date:
            return e_date, None

    # Fallback to single date
    s_date = parse_single_date(cleaned)
    return s_date, None


def parse_iso_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 datetime string with timezone awareness."""
    if not dt_str or not isinstance(dt_str, str) or not dt_str.strip():
        return None
    try:
        dt = datetime.fromisoformat(dt_str.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def calculate_lifecycle_statuses(
    reg_start_str: Optional[str],
    reg_deadline_str: Optional[str],
    start_date_str: Optional[str],
    end_date_str: Optional[str],
    current_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Calculate dynamic registration and event lifecycle statuses.
    Eliminates ambiguous or stale flags from Unstop API:
      - registration_status: OPEN or CLOSED
      - event_status: UPCOMING, ONGOING, or FINISHED
      - is_registration_open: bool strictly synchronized with registration_status
      - days_left: integer count of days remaining until registration deadline (0 if closed)
      - time_left: human-friendly remaining time text (e.g. '19 days left', 'Ended')
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    elif current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    reg_deadline = parse_iso_datetime(reg_deadline_str)
    reg_start = parse_iso_datetime(reg_start_str)
    event_start = parse_iso_datetime(start_date_str)
    event_end = parse_iso_datetime(end_date_str)

    if reg_deadline:
        registration_status = "CLOSED" if reg_deadline < current_time else "OPEN"
    else:
        registration_status = "CLOSED" if (event_end and event_end < current_time) else "OPEN"

    is_registration_open = (registration_status == "OPEN")

    effective_start = event_start or reg_start
    effective_end = max(event_end, reg_deadline) if (event_end and reg_deadline) else (event_end or reg_deadline)

    if effective_end and effective_end < current_time:
        event_status = "FINISHED"
    elif effective_start and effective_start > current_time:
        event_status = "UPCOMING"
    else:
        event_status = "ONGOING"

    days_left = 0
    time_left = "Ended"

    if registration_status == "OPEN" and reg_deadline:
        delta = reg_deadline - current_time
        total_seconds = max(0, int(delta.total_seconds()))
        days_left = max(0, delta.days)
        hours = total_seconds // 3600

        if delta.days > 1:
            time_left = f"{delta.days} days left"
        elif delta.days == 1:
            time_left = "1 day left"
        elif hours > 1:
            time_left = f"{hours} hours left"
        elif hours == 1:
            time_left = "1 hour left"
        else:
            mins = max(1, total_seconds // 60)
            time_left = f"{mins} mins left"

    return {
        "registration_status": registration_status,
        "event_status": event_status,
        "is_registration_open": is_registration_open,
        "days_left": days_left,
        "time_left": time_left,
    }


# ==============================================================================
# TEAM SIZE PARSER
# ==============================================================================

def parse_team_size(team_input: Any) -> Tuple[Optional[int], Optional[int]]:
    """
    Parse team size into (team_size_min, team_size_max) integers.
    Supports integers, dicts, and range strings ("1-3", "2–5", "1", "1 to 5").
    """
    if team_input is None:
        return None, None

    # Handle dictionary representation
    if isinstance(team_input, dict):
        min_v = team_input.get("min_team_size") or team_input.get("min")
        max_v = team_input.get("max_team_size") or team_input.get("max")
        try:
            min_i = int(min_v) if min_v is not None else None
            max_i = int(max_v) if max_v is not None else None
            if min_i is not None and max_i is not None:
                if min_i <= max_i:
                    return min_i, max_i
                else:
                    return max_i, min_i
            elif min_i is not None:
                return min_i, min_i
            elif max_i is not None:
                return 1, max_i
        except (ValueError, TypeError):
            pass

    # Handle integer directly
    if isinstance(team_input, int):
        return team_input, team_input

    # Handle string patterns
    text = str(team_input).strip()
    if not text:
        return None, None

    # Range pattern: '1-3', '2–5', '1 to 4', '1 - 5 per team'
    m_range = re.search(r"(\d+)\s*(?:[-–—]|to)\s*(\d+)", text, re.IGNORECASE)
    if m_range:
        min_val = int(m_range.group(1))
        max_val = int(m_range.group(2))
        return (min_val, max_val) if min_val <= max_val else (max_val, min_val)

    # Single number: '1', 'teams of 3', '4 members'
    m_single = re.search(r"\b(\d+)\b", text)
    if m_single:
        val = int(m_single.group(1))
        return val, val

    return None, None


# ==============================================================================
# PRIZE & CURRENCY PARSER
# ==============================================================================

CURRENCY_SYMBOLS = {
    "₹": "INR",
    "rs": "INR",
    "inr": "INR",
    "fa-rupee": "INR",
    "$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "¥": "JPY",
    "jpy": "JPY",
    "cny": "CNY",
    "cad": "CAD",
    "aud": "AUD",
}


def parse_prize(
    prize_str: Optional[str],
    fallback_cash: Optional[float] = None,
    fallback_currency: Optional[str] = None,
) -> Tuple[Optional[str], Optional[float], Optional[str]]:
    """
    Parse prize string into (prize_text, prize_amount, currency).
    Preserves original verbatim prize text, extracts numeric amount and ISO currency.
    Supports Indian denominations ('Lakh', 'Crore', 'k').
    """
    cleaned = clean_text(prize_str)

    # Use fallback cash if provided and no text is available
    if not cleaned and fallback_cash is not None and fallback_cash > 0:
        curr_code = fallback_currency or "INR"
        symbol = "₹" if curr_code == "INR" else f"{curr_code} "
        formatted_prize = f"{symbol}{int(fallback_cash):,}" if fallback_cash.is_integer() else f"{symbol}{fallback_cash:,.2f}"
        return formatted_prize, float(fallback_cash), curr_code

    if not cleaned:
        return None, None, None

    # Detect currency code
    currency: Optional[str] = None
    if fallback_currency:
        currency = fallback_currency.upper()

    if not currency:
        cleaned_lower = cleaned.lower()
        for sym, code in CURRENCY_SYMBOLS.items():
            if sym in cleaned_lower:
                currency = code
                break

    # Extract numeric amount
    prize_amount: Optional[float] = None

    # Pattern for Indian Lakhs: '1.5 Lakhs', 'Rs 1.5 Lakhs', '₹1 Lakh'
    m_lakh = re.search(r"(\d+(?:\.\d+)?)\s*(?:lakhs?|lacs?)\b", cleaned, re.IGNORECASE)
    if m_lakh:
        try:
            val = float(m_lakh.group(1))
            prize_amount = val * 100_000.0
            if not currency:
                currency = "INR"
        except ValueError:
            pass

    # Pattern for Crores: '2 Crores', '₹1.2 Cr'
    if prize_amount is None:
        m_cr = re.search(r"(\d+(?:\.\d+)?)\s*(?:crores?|cr)\b", cleaned, re.IGNORECASE)
        if m_cr:
            try:
                val = float(m_cr.group(1))
                prize_amount = val * 10_000_000.0
                if not currency:
                    currency = "INR"
            except ValueError:
                pass

    # Pattern for 'k': '50k', '₹100k'
    if prize_amount is None:
        m_k = re.search(r"(\d+(?:\.\d+)?)\s*k\b", cleaned, re.IGNORECASE)
        if m_k:
            try:
                val = float(m_k.group(1))
                prize_amount = val * 1_000.0
            except ValueError:
                pass

    # Pattern for standard numbers: '1,00,000', '10,000', '5000'
    if prize_amount is None:
        m_num = re.search(r"(\d{1,3}(?:[,\s]\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)", cleaned)
        if m_num:
            num_str = m_num.group(1).replace(",", "").replace(" ", "")
            try:
                val = float(num_str)
                if val > 0:
                    prize_amount = val
            except ValueError:
                pass

    # Fallback to API cash if text parsing yielded no amount
    if prize_amount is None and fallback_cash is not None and fallback_cash > 0:
        prize_amount = float(fallback_cash)

    if currency is None and fallback_currency:
        currency = fallback_currency.upper()

    # If numeric amount exists but no currency detected, default to INR if Indian context
    if prize_amount is not None and not currency:
        currency = "INR"

    return cleaned, prize_amount, currency


# ==============================================================================
# LOCATION & EVENT MODE DISAMBIGUATION
# ==============================================================================

def normalize_location_and_mode(
    raw_location: Any,
    raw_mode: Optional[str],
    address_dict: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract location separately from event mode.
    Disambiguates 'Online (Online)' -> location='Online', event_mode='Online'.
    Physical locations -> location='City, State, Country', event_mode='Offline' (or 'Hybrid').
    """
    mode_str = (raw_mode or "").strip().lower()
    event_mode = None

    if "hybrid" in mode_str:
        event_mode = "Hybrid"
    elif "online" in mode_str:
        event_mode = "Online"
    elif "offline" in mode_str or "venue" in mode_str or "in-person" in mode_str:
        event_mode = "Offline"

    # Disambiguate location from address_dict
    location_parts = []
    if isinstance(address_dict, dict) and any(address_dict.values()):
        addr_text = clean_text(address_dict.get("address"))
        city = clean_text(address_dict.get("city"))
        state = clean_text(address_dict.get("state"))
        country_obj = address_dict.get("country")
        country_name = None
        if isinstance(country_obj, dict):
            country_name = clean_text(country_obj.get("name"))
        elif isinstance(country_obj, str):
            country_name = clean_text(country_obj)

        if city:
            location_parts.append(city)
        if state and state != city:
            location_parts.append(state)
        if country_name and country_name not in location_parts:
            location_parts.append(country_name)

        # Fallback to address string if parts are empty
        if not location_parts and addr_text:
            location_parts.append(addr_text)

    location: Optional[str] = ", ".join(location_parts) if location_parts else None

    # If no address_dict, check raw_location string
    if not location and raw_location:
        loc_clean = clean_text(str(raw_location))
        if loc_clean:
            if re.match(r"^online\s*\(\s*online\s*\)$", loc_clean, re.IGNORECASE):
                location = "Online"
                if not event_mode:
                    event_mode = "Online"
            elif loc_clean.lower() == "online":
                location = "Online"
                if not event_mode:
                    event_mode = "Online"
            else:
                location = loc_clean

    # If mode is Online and location is empty or generic
    if event_mode == "Online" and (not location or location.lower() == "online"):
        location = "Online"
    elif location and location.lower() == "online":
        location = "Online"
        if not event_mode:
            event_mode = "Online"
    elif location and not event_mode:
        event_mode = "Offline"

    return location, event_mode


# ==============================================================================
# TAGS EXTRACTION & CLEANING
# ==============================================================================

def clean_tags(tag_candidates: List[Any]) -> List[str]:
    """
    Extract, clean, and deduplicate tags preserving meaningful original text.
    Handles strings, dicts (skills, categories, filters), and nested lists.
    """
    seen = set()
    cleaned_tags = []

    def add_tag(item: Any):
        if not item:
            return
        if isinstance(item, str):
            val = clean_text(item)
            if val:
                val = re.sub(r"^[#•\-\*\s]+", "", val).strip()
                if 1 <= len(val) <= 60 and val.lower() not in seen:
                    seen.add(val.lower())
                    cleaned_tags.append(val)
        elif isinstance(item, dict):
            # Try various common tag keys from Unstop API
            name = (
                item.get("name")
                or item.get("skill")
                or item.get("skill_name")
                or item.get("tag")
                or item.get("title")
            )
            add_tag(name)
        elif isinstance(item, (list, tuple)):
            for sub in item:
                add_tag(sub)

    for candidate in tag_candidates:
        add_tag(candidate)

    return cleaned_tags


# ==============================================================================
# STATUS NORMALIZATION
# ==============================================================================

def normalize_status(
    raw_status: Optional[str],
    event_status: Optional[str] = None,
    registration_status: Optional[str] = None,
) -> Optional[str]:
    """
    Extract and normalize the actual status from the page/card.
    Examples: Upcoming, Ongoing, Closed, Completed, Online.
    """
    raw_clean = (raw_status or "").strip().upper()

    if raw_clean in ("LIVE", "OPEN") or registration_status == "OPEN":
        if event_status == "UPCOMING":
            return "Upcoming"
        return "Ongoing"
    elif raw_clean == "UPCOMING" or event_status == "UPCOMING":
        return "Upcoming"
    elif raw_clean in ("EXPIRED", "CLOSED") or registration_status == "CLOSED":
        if event_status == "FINISHED":
            return "Completed"
        return "Closed"
    elif raw_clean in ("FINISHED", "COMPLETED") or event_status == "FINISHED":
        return "Completed"

    # Fallback to cleaned raw status if available
    if raw_status and raw_status.strip():
        return raw_status.strip().title()

    return None


# ==============================================================================
# RECORD VALIDATION
# ==============================================================================

def validate_record(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Validate record against integrity rules before database upsert or export.
    Invalid optional fields become None rather than fabricating data.
    """
    title = record.get("title")
    source_url = record.get("source_url")

    # Critical mandatory fields
    if not title or not isinstance(title, str) or not title.strip():
        logger.warning(f"[VALIDATION FAIL] Dropping record with missing title: {record}")
        return None

    if not source_url or not isinstance(source_url, str) or not source_url.startswith("http"):
        logger.warning(f"[VALIDATION FAIL] Dropping record with invalid source_url: {source_url}")
        return None

    # Date validation
    start_date = record.get("start_date")
    end_date = record.get("end_date")
    reg_deadline = record.get("registration_deadline")

    iso_pattern = r"^\d{4}-\d{2}-\d{2}$"
    if start_date and not re.match(iso_pattern, str(start_date)):
        logger.warning(f"[WARNING] Invalid start_date '{start_date}' for '{title}'. Setting to None.")
        record["start_date"] = None
        start_date = None

    if end_date and not re.match(iso_pattern, str(end_date)):
        logger.warning(f"[WARNING] Invalid end_date '{end_date}' for '{title}'. Setting to None.")
        record["end_date"] = None
        end_date = None

    if start_date and end_date and start_date > end_date:
        logger.warning(f"[WARNING] start_date {start_date} > end_date {end_date} for '{title}'. Setting end_date=None.")
        record["end_date"] = None

    if reg_deadline and not re.match(iso_pattern, str(reg_deadline)):
        logger.warning(f"[WARNING] Invalid registration_deadline '{reg_deadline}' for '{title}'. Setting to None.")
        record["registration_deadline"] = None

    # Team size validation
    t_min = record.get("team_size_min")
    t_max = record.get("team_size_max")

    if t_min is not None and not isinstance(t_min, int):
        try:
            record["team_size_min"] = int(t_min)
            t_min = record["team_size_min"]
        except (ValueError, TypeError):
            record["team_size_min"] = None
            t_min = None

    if t_max is not None and not isinstance(t_max, int):
        try:
            record["team_size_max"] = int(t_max)
            t_max = record["team_size_max"]
        except (ValueError, TypeError):
            record["team_size_max"] = None
            t_max = None

    if t_min is not None and t_max is not None and t_min > t_max:
        logger.warning(f"[WARNING] team_size_min {t_min} > team_size_max {t_max} for '{title}'. Swapping.")
        record["team_size_min"], record["team_size_max"] = t_max, t_min

    # Prize validation
    prize_amount = record.get("prize_amount")
    currency = record.get("currency")

    if prize_amount is not None:
        try:
            p_val = float(prize_amount)
            if p_val < 0:
                record["prize_amount"] = None
            else:
                record["prize_amount"] = p_val
        except (ValueError, TypeError):
            record["prize_amount"] = None

    if record.get("prize_amount") is not None and not currency:
        record["currency"] = "INR"

    # Tags validation
    tags = record.get("tags")
    if not isinstance(tags, list):
        record["tags"] = []
    else:
        record["tags"] = [str(t) for t in tags if str(t).strip()]

    return record


def log_scraped_summary(record: Dict[str, Any]) -> None:
    """Emit formatted [SCRAPED] summary log for every hackathon."""
    team_str = (
        f"{record.get('team_size_min')} - {record.get('team_size_max')}"
        if record.get("team_size_min") is not None or record.get("team_size_max") is not None
        else "None"
    )
    summary_lines = [
        "",
        "[SCRAPED]",
        f"Title: {record.get('title')}",
        f"Organizer: {record.get('organizer')}",
        f"Status: {record.get('status')}",
        f"Location: {record.get('location')}",
        f"Event Mode: {record.get('event_mode')}",
        f"Start Date: {record.get('start_date')}",
        f"End Date: {record.get('end_date')}",
        f"Registration Deadline: {record.get('registration_deadline')}",
        f"Team Size: {team_str}",
        f"Prize: {record.get('prize')}",
        f"Prize Amount: {record.get('prize_amount')}",
        f"Currency: {record.get('currency')}",
        f"Tags: {record.get('tags')}",
        f"Source URL: {record.get('source_url')}",
    ]
    logger.info("\n".join(summary_lines))


# ==============================================================================
# PARSE HACKATHON RECORD (COMBINES SUMMARY & DETAIL)
# ==============================================================================

def parse_hackathon(
    summary: Dict[str, Any],
    detail: Optional[Dict[str, Any]] = None,
    current_time: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """
    Combine summary and detail responses into a clean, strictly authentic dictionary.
    Extracts all 17 required structured fields plus legacy metadata.
    Any missing field is explicitly set to None (null in JSON).
    """
    comp = detail.get("competition", {}) if (detail and isinstance(detail, dict)) else {}

    # Basic Identifiers
    hid = comp.get("id") or summary.get("id")
    raw_title = comp.get("title") or summary.get("title")
    title = clean_text(raw_title)

    # URLs & Source Metadata
    seo_url = comp.get("seo_url") or summary.get("seo_url")
    if not seo_url and hid:
        seo_url = f"https://unstop.com/hackathons/{hid}"
    elif seo_url and not seo_url.startswith("http"):
        seo_url = f"https://unstop.com/hackathons/{seo_url.lstrip('/')}"
    source_url = seo_url or f"https://unstop.com/hackathons/{hid}"

    domain = urlparse(source_url).netloc.replace("www.", "") if source_url else "unstop.com"
    short_url = comp.get("short_url") or summary.get("short_url")

    # Organisation / Organizer
    org_data = comp.get("organisation") or summary.get("organisation") or {}
    organization = None
    organizer_name = None
    if isinstance(org_data, dict) and any(org_data.values()):
        org_name = clean_text(org_data.get("name"))
        org_url = org_data.get("public_url")
        if org_url and not org_url.startswith("http"):
            org_url = f"https://unstop.com/{org_url.lstrip('/')}"
        org_logo = org_data.get("logoUrl2") or org_data.get("logoUrl")

        organizer_name = org_name
        organization = {
            "id": org_data.get("id"),
            "name": org_name,
            "url": org_url if (isinstance(org_url, str) and org_url.strip()) else None,
            "logo_url": org_logo if (isinstance(org_logo, str) and org_logo.strip()) else None,
        }
        if not any(organization.values()):
            organization = None

    # Media / Banner & Logo
    banner_url = None
    banner_obj = comp.get("banner") or comp.get("banner_mobile")
    if isinstance(banner_obj, dict):
        banner_url = banner_obj.get("image_url") or banner_obj.get("path")
    elif isinstance(banner_obj, str) and banner_obj.strip():
        banner_url = banner_obj.strip()

    logo_url = (
        comp.get("logoUrl2")
        or comp.get("logoUrl")
        or summary.get("logoUrl2")
    )
    logo_url = logo_url.strip() if (isinstance(logo_url, str) and logo_url.strip()) else None

    # Raw Status & Region / Mode
    raw_status = comp.get("status") or summary.get("status")
    raw_status = clean_text(raw_status)
    raw_region = comp.get("region") or summary.get("region")

    # Dates
    raw_start_date = comp.get("start_date") or summary.get("start_date")
    raw_end_date = comp.get("end_date") or summary.get("end_date")

    start_date_iso = parse_single_date(raw_start_date)
    end_date_iso = parse_single_date(raw_end_date)
    if start_date_iso and end_date_iso and start_date_iso > end_date_iso:
        end_date_iso = None

    # Registration Requirements
    req = comp.get("regnRequirements") or summary.get("regnRequirements") or {}
    team_size = None
    min_team_size = None
    max_team_size = None
    regn_start = None
    regn_end = None

    if isinstance(req, dict) and any(req.values()):
        min_team_size = req.get("min_team_size")
        max_team_size = req.get("max_team_size")
        regn_start = req.get("start_regn_dt")
        regn_end = req.get("end_regn_dt")

    # Team size parsing
    t_min, t_max = parse_team_size({"min": min_team_size, "max": max_team_size})
    if t_min is not None or t_max is not None:
        team_size = {"min_team_size": t_min, "max_team_size": t_max}

    # Registration deadline
    registration_deadline_iso = parse_single_date(regn_end)

    # Dynamic lifecycle calculations
    lifecycle = calculate_lifecycle_statuses(
        reg_start_str=regn_start,
        reg_deadline_str=regn_end,
        start_date_str=raw_start_date,
        end_date_str=raw_end_date,
        current_time=current_time,
    )
    registration_status = lifecycle["registration_status"]
    event_status = lifecycle["event_status"]
    is_regn_open = lifecycle["is_registration_open"]
    days_left = lifecycle["days_left"]
    time_left = lifecycle["time_left"]

    # Normalized Status
    status = normalize_status(raw_status, event_status, registration_status)

    # Location & Event Mode Disambiguation
    addr = comp.get("address_with_country_logo") or summary.get("address_with_country_logo") or {}
    raw_loc_str = comp.get("location") or summary.get("location")
    location, event_mode = normalize_location_and_mode(
        raw_location=raw_loc_str,
        raw_mode=raw_region,
        address_dict=addr if isinstance(addr, dict) else None,
    )

    # Paid / Fee info
    is_paid = comp.get("paid")
    if is_paid is None:
        is_paid = summary.get("isPaid")
    if is_paid is not None:
        is_paid = bool(is_paid)

    fee_details = comp.get("payment_services") or summary.get("payment_services")
    if not fee_details or (isinstance(fee_details, list) and len(fee_details) == 0):
        fee_details = None

    # Eligibility
    elig_raw = req.get("eligibility") if isinstance(req, dict) else None
    eligibility = None
    if elig_raw:
        if isinstance(elig_raw, dict):
            eligibility = elig_raw
        elif isinstance(elig_raw, str):
            try:
                eligibility = json.loads(elig_raw)
            except Exception:
                eligibility = {"raw": elig_raw}

    # Prizes & Cash normalization
    raw_prizes = comp.get("prizes") or summary.get("prizes") or []
    prizes = None
    total_cash_sum = 0.0
    api_currency_code = None

    if isinstance(raw_prizes, list) and raw_prizes:
        prize_list = []
        for p in raw_prizes:
            if not isinstance(p, dict):
                continue
            cash_val = p.get("cash")
            if isinstance(cash_val, (int, float)) and cash_val > 0:
                total_cash_sum += float(cash_val)
            curr = p.get("currencyCode") or p.get("currency")
            if curr and not api_currency_code:
                api_currency_code = curr

            prize_list.append({
                "rank": p.get("rank"),
                "cash": cash_val,
                "currency": curr,
                "certificate": bool(p.get("certificate")) if p.get("certificate") is not None else None,
                "internship": bool(p.get("pre_placement_internship")) if p.get("pre_placement_internship") is not None else None,
                "ppo": bool(p.get("pre_placement_opportunity")) if p.get("pre_placement_opportunity") is not None else None,
                "others": p.get("others"),
            })

        overall_prizes_str = comp.get("overall_prizes") or summary.get("overall_prizes")
        prizes = {
            "overall_prize_text": overall_prizes_str if overall_prizes_str else None,
            "total_cash": total_cash_sum if total_cash_sum > 0 else None,
            "currency": api_currency_code,
            "breakdown": prize_list if prize_list else None,
        }
        if not any(prizes.values()):
            prizes = None

    # Parse normalized prize, prize_amount, currency
    displayed_prize_text = comp.get("overall_prizes") or summary.get("overall_prizes")
    prize, prize_amount, currency = parse_prize(
        prize_str=displayed_prize_text,
        fallback_cash=total_cash_sum if total_cash_sum > 0 else None,
        fallback_currency=api_currency_code,
    )

    # Rounds & Timeline
    raw_rounds = comp.get("rounds") or []
    rounds = None
    if isinstance(raw_rounds, list) and raw_rounds:
        rounds_list = []
        for r in raw_rounds:
            if not isinstance(r, dict):
                continue
            r_details = r.get("details")
            r_info = r_details[0] if (isinstance(r_details, list) and r_details) else {}

            r_title = r_info.get("title") or r.get("title")
            r_desc = clean_html(r_info.get("display_text") or r_info.get("description"))
            r_start = r_info.get("start_date") or r.get("start_date")
            r_end = r_info.get("end_date") or r.get("end_date")
            r_status = r_info.get("status") or r.get("status")
            r_url = r_info.get("seo_data", {}).get("seo_url") if isinstance(r_info.get("seo_data"), dict) else None

            rounds_list.append({
                "order": r.get("round_order"),
                "title": clean_text(r_title),
                "status": r_status,
                "start_date": r_start,
                "end_date": r_end,
                "type": r.get("subtype"),
                "description": r_desc,
                "url": r_url,
            })
        if rounds_list:
            rounds = rounds_list

    # Tags aggregation (skills, workfunction categories, filters)
    raw_skills = comp.get("skills") or summary.get("required_skills") or []
    raw_workfunction = comp.get("workfunction") or summary.get("workfunction") or []
    raw_filters = comp.get("filters") or summary.get("filters") or []
    raw_hashtags = comp.get("hashtags") or []

    tags = clean_tags([raw_skills, raw_workfunction, raw_filters, raw_hashtags])

    # Backward compatibility categories and skills lists
    categories = [w.get("name") for w in raw_workfunction if isinstance(w, dict) and w.get("name")] or None
    skills_list = [s.get("skill") or s.get("name") for s in raw_skills if isinstance(s, dict) and (s.get("skill") or s.get("name"))] or None

    # Contacts
    raw_contacts = comp.get("contacts") or []
    contacts = None
    if isinstance(raw_contacts, list) and raw_contacts:
        contact_list = []
        for c in raw_contacts:
            if not isinstance(c, dict):
                continue
            c_name = clean_text(c.get("name"))
            c_email = clean_text(c.get("email"))
            c_phone = clean_text(c.get("contact_no"))
            c_desig = clean_text(c.get("designation"))
            if any([c_name, c_email, c_phone]):
                contact_list.append({
                    "name": c_name,
                    "email": c_email,
                    "contact_no": c_phone,
                    "designation": c_desig,
                })
        if contact_list:
            contacts = contact_list

    # Stats & Description
    views_count = comp.get("viewsCount") or summary.get("viewsCount")
    register_count = comp.get("registerCount") or summary.get("registerCount")
    description = clean_html(comp.get("details") or summary.get("details"))

    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Structured Record: 17 standardized fields first, followed by legacy fields
    raw_record: Dict[str, Any] = {
        # 17 Standardized Core & Metadata Fields
        "title": title,
        "organizer": organizer_name,
        "status": status,
        "location": location,
        "event_mode": event_mode,
        "start_date": start_date_iso,
        "end_date": end_date_iso,
        "registration_deadline": registration_deadline_iso,
        "team_size_min": t_min,
        "team_size_max": t_max,
        "prize": prize,
        "prize_amount": prize_amount,
        "currency": currency,
        "tags": tags,
        "source_url": source_url,
        "source_website": domain,
        "scraped_at": scraped_at,

        # Legacy Backward-Compatible Fields
        "id": hid,
        "url": source_url,
        "short_url": short_url,
        "event_status": event_status,
        "registration_status": registration_status,
        "is_registration_open": is_regn_open,
        "days_left": days_left,
        "time_left": time_left,
        "raw_status": raw_status,
        "mode": raw_region,
        "is_paid": is_paid,
        "fee_details": fee_details,
        "registration_start_date": regn_start,
        "organization": organization,
        "team_size": team_size,
        "prizes": prizes,
        "rounds_and_timeline": rounds,
        "categories": categories,
        "skills": skills_list,
        "views_count": views_count,
        "registrations_count": register_count,
        "contacts": contacts,
        "banner_url": banner_url,
        "logo_url": logo_url,
        "description": description,
        "eligibility": eligibility,
        "crawled_at": scraped_at,
    }

    # Validate record integrity
    validated = validate_record(raw_record)
    if not validated:
        return None

    log_scraped_summary(validated)
    return validated


# ==============================================================================
# SUPABASE SYNCHRONIZATION & DEDUPLICATION
# ==============================================================================

def sync_to_supabase(
    records: List[Dict[str, Any]],
    table_name: Optional[str] = None,
) -> Tuple[bool, str, Dict[str, int]]:
    """
    Sync validated hackathon records to Supabase table using:
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

    # Deduplicate on source_url in-memory
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

    # Attempt 1: supabase-py client if installed
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
        logger.warning(f"[SUPABASE] Client upsert notice: {err_str}")
        if "PGRST204" in err_str or "column" in err_str.lower():
            logger.warning("[WARNING] Supabase schema is missing columns. Please execute 'supabase_migration.sql' in Supabase SQL Editor.")

    # Attempt 2: Direct PostgREST REST API via urllib
    try:
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=representation",
            "User-Agent": "UnstopScraper/1.0",
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
        err_body = he.read().decode("utf-8", errors="ignore")
        logger.warning(f"[SUPABASE] HTTPError {he.code} during REST upsert: {err_body}")
        if "PGRST204" in err_body or "column" in err_body.lower():
            logger.warning("[WARNING] Supabase schema is missing columns. Please run 'supabase_migration.sql' in Supabase SQL Editor.")
    except Exception as exc:
        logger.warning(f"[SUPABASE] REST API connection notice: {exc}")
        logger.info("[SUPABASE] If your Supabase project was paused or unreachable, run 'supabase_migration.sql' once active.")

    stats["failed"] = len(db_records)
    return False, "Supabase upsert could not be completed at this time.", stats


# ==============================================================================
# DETAIL FETCHER
# ==============================================================================

async def fetch_competition_detail(
    semaphore: asyncio.Semaphore,
    cid: int,
    headers: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    """Fetch full detail for a single competition ID asynchronously using Scrapling."""
    url = DETAIL_API_URL.format(id=cid)
    async with semaphore:
        try:
            response = await AsyncFetcher.get(url, headers=headers)
            if response.status == 200 and response.body:
                data = json.loads(response.body)
                return data.get("data")
            else:
                logger.warning(f"Detail fetch failed for ID {cid}: status {response.status}")
                return None
        except Exception as e:
            logger.error(f"Exception fetching detail for ID {cid}: {e}")
            return None


# ==============================================================================
# MAIN SCRAPING PIPELINE
# ==============================================================================

async def scrape_hackathons(
    status_filter: str = "open",
    limit: Optional[int] = None,
    concurrency: int = 10,
    fetch_details: bool = True,
) -> List[Dict[str, Any]]:
    """
    Scrape hackathons from Unstop.
    status_filter: 'open', 'all', 'upcoming', 'closed'
    limit: max hackathons to return
    concurrency: max concurrent requests for detail endpoints
    """
    logger.info(f"Starting Unstop scraper (filter: '{status_filter}', limit: {limit}, concurrency: {concurrency})...")

    # Step 1: Paginate search-result endpoint to get summary list of hackathons
    page = 1
    per_page = 50
    all_summaries: List[Dict[str, Any]] = []

    status_param = ""
    if status_filter == "open":
        status_param = "&oppstatus=open"
    elif status_filter == "upcoming":
        status_param = "&oppstatus=upcoming"
    elif status_filter == "closed":
        status_param = "&oppstatus=closed"
    elif status_filter == "all":
        status_param = ""

    while True:
        url = f"{BASE_API_URL}?opportunity=hackathons{status_param}&page={page}&per_page={per_page}"
        logger.info(f"Fetching listing page {page} from {url}...")

        try:
            res = await AsyncFetcher.get(url, headers=DEFAULT_HEADERS)
        except Exception as e:
            logger.error(f"Failed to fetch page {page}: {e}")
            break

        if res.status != 200 or not res.body:
            logger.warning(f"Page {page} returned status {res.status}")
            break

        try:
            payload = json.loads(res.body)
            data_node = payload.get("data", {})
            items = data_node.get("data", [])
            total_items = data_node.get("total", 0)
            last_page = data_node.get("last_page", page)

            if not items:
                logger.info(f"No items found on page {page}. Done listing.")
                break

            all_summaries.extend(items)
            logger.info(
                f"Page {page}/{last_page}: retrieved {len(items)} items. Total collected: {len(all_summaries)}/{total_items}"
            )

            if limit and len(all_summaries) >= limit:
                all_summaries = all_summaries[:limit]
                logger.info(f"Reached specified limit of {limit} hackathons.")
                break

            if page >= last_page:
                break

            page += 1
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.error(f"Failed parsing response for page {page}: {e}")
            break

    logger.info(f"Total hackathons collected from listings: {len(all_summaries)}")

    if not all_summaries:
        return []

    # Step 2: Fetch detailed info for each hackathon if requested
    details_map: Dict[int, Optional[Dict[str, Any]]] = {}
    if fetch_details:
        logger.info(f"Fetching deep details for {len(all_summaries)} hackathons (concurrency: {concurrency})...")
        semaphore = asyncio.Semaphore(concurrency)

        tasks = []
        for item in all_summaries:
            cid = item.get("id")
            if cid:
                tasks.append((cid, fetch_competition_detail(semaphore, cid, DEFAULT_HEADERS)))

        completed_count = 0
        total_tasks = len(tasks)

        async def run_and_track(cid: int, coro):
            nonlocal completed_count
            res = await coro
            completed_count += 1
            if completed_count % 10 == 0 or completed_count == total_tasks:
                logger.info(f"Detail fetch progress: {completed_count}/{total_tasks} completed.")
            return cid, res

        tracked_tasks = [run_and_track(cid, t) for cid, t in tasks]
        results = await asyncio.gather(*tracked_tasks)

        for cid, d in results:
            details_map[cid] = d

    # Step 3: Combine, normalize, and validate records
    parsed_records: List[Dict[str, Any]] = []
    for item in all_summaries:
        cid = item.get("id")
        detail_data = details_map.get(cid)
        record = parse_hackathon(summary=item, detail=detail_data)
        if record:
            parsed_records.append(record)

    logger.info(f"Successfully processed {len(parsed_records)} valid hackathon records.")
    return parsed_records


# ==============================================================================
# LOCAL PERSISTENCE HELPERS
# ==============================================================================

def save_to_json(records: List[Dict[str, Any]], filepath: str) -> None:
    """Save records to structured JSON file."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(records)} records to JSON: {filepath}")


def save_to_csv(records: List[Dict[str, Any]], filepath: str) -> None:
    """Save records to CSV file, ensuring standardized 17 fields come first."""
    if not records:
        return

    standard_fields = [
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
    ]

    all_keys = list(records[0].keys())
    remaining_keys = [k for k in all_keys if k not in standard_fields]
    fieldnames = standard_fields + remaining_keys

    rows = []
    for r in records:
        row: Dict[str, Any] = {}
        for k in fieldnames:
            v = r.get(k)
            if v is None:
                row[k] = ""
            elif isinstance(v, list):
                row[k] = ", ".join(str(x) for x in v) if v else ""
            elif isinstance(v, dict):
                row[k] = json.dumps(v, ensure_ascii=False)
            else:
                row[k] = v
        rows.append(row)

    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    logger.info(f"Saved {len(rows)} records to CSV: {filepath}")


# ==============================================================================
# CLI & MAIN ENTRYPOINT
# ==============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape hackathon details from Unstop (https://unstop.com/hackathons) & sync to Supabase."
    )
    parser.add_argument(
        "--status",
        type=str,
        default="open",
        choices=["open", "all", "upcoming", "closed"],
        help="Hackathon status filter to scrape (default: 'open')",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of hackathons to scrape (default: all available)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Number of concurrent connections for fetching details (default: 10)",
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Skip fetching deep competition details (only scrape listing overview)",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default="unstop_hackathons.json",
        help="Output path for JSON export (default: unstop_hackathons.json)",
    )
    parser.add_argument(
        "--output-csv",
        type=str,
        default="unstop_hackathons.csv",
        help="Output path for CSV export (default: unstop_hackathons.csv)",
    )
    parser.add_argument(
        "--table",
        type=str,
        default=None,
        help="Target Supabase table name (overrides SUPABASE_TABLE in .env)",
    )
    parser.add_argument(
        "--skip-supabase",
        action="store_true",
        help="Skip synchronizing records to Supabase database",
    )
    return parser.parse_args()


async def main_async():
    args = parse_args()
    records = await scrape_hackathons(
        status_filter=args.status,
        limit=args.limit,
        concurrency=args.concurrency,
        fetch_details=not args.no_details,
    )

    if not records:
        logger.warning("No records were found or extracted.")
        sys.exit(1)

    # Local Persistence
    save_to_json(records, args.output_json)
    save_to_csv(records, args.output_csv)

    # Supabase Synchronization
    if not args.skip_supabase:
        sync_to_supabase(records, table_name=args.table)

    print("\n" + "=" * 60)
    print("SCRAPE & SYNC COMPLETE!")
    print(f"Total Hackathons Scraped: {len(records)}")
    print(f"JSON Export: {os.path.abspath(args.output_json)}")
    print(f"CSV Export:  {os.path.abspath(args.output_csv)}")
    print("=" * 60 + "\n")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
