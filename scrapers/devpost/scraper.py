#!/usr/bin/env python3
"""
Devpost Hackathon Scraper using Scrapling & Supabase Integration.
Scrapes ongoing and upcoming hackathons from Devpost with high accuracy and strict fidelity.
Extracts deep structured fields: title, organizer, status, location, event_mode,
start_date, end_date, registration_deadline, team_size_min, team_size_max,
prize, prize_amount, currency, tags, source_url, source_website, scraped_at.

Strict zero-fabrication: missing details are recorded as None / null or empty array.
"""

import argparse
import csv
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
from urllib.parse import urljoin, urlsplit

from scrapling.fetchers import Fetcher

# Ensure stdout handles UTF-8 characters cleanly across Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("devpost_scraper")

BASE_API_URL = "https://devpost.com/api/hackathons"
DEFAULT_PAGE = 2

# Currency symbol mapping
CURRENCY_SYMBOLS = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "₹": "INR",
    "rs": "INR",
    "inr": "INR",
    "usd": "USD",
    "eur": "EUR",
    "gbp": "GBP",
    "cad": "CAD",
    "c$": "CAD",
    "aud": "AUD",
    "a$": "AUD",
    "sgd": "SGD",
    "s$": "SGD",
}

# Month names mapping for date parsing
MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


# ==============================================================================
# Helper Normalization & Parsing Functions (Strict, Zero-Fabrication)
# ==============================================================================

def clean_html_tags(raw_html: Optional[str]) -> Optional[str]:
    """Removes HTML markup tags from a string and normalizes whitespace."""
    if not raw_html or not isinstance(raw_html, str):
        return None
    cleaned = re.sub(r"<[^>]+>", " ", raw_html)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else None


def clean_text(text: Optional[str]) -> Optional[str]:
    """Cleans up raw text, strips redundant whitespace, or returns None if empty."""
    if not text or not isinstance(text, str):
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned if cleaned else None


def extract_domain(url: Optional[str]) -> Optional[str]:
    """Dynamically extracts domain/website from URL without hardcoding."""
    if not url or not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
        netloc = parsed.netloc.lower().split(":")[0]
        parts = netloc.split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return netloc if netloc else None
    except Exception:
        return None


def parse_iso_date(raw: Optional[str]) -> Optional[str]:
    """Extracts and validates an ISO 8601 date string as YYYY-MM-DD."""
    if not raw or not isinstance(raw, str):
        return None
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw.strip())
    if match:
        y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            dt = datetime(y, m, d)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def parse_single_date(text: Optional[str], default_year: Optional[int] = None) -> Optional[str]:
    """Parses a single textual date into YYYY-MM-DD format."""
    if not text or not isinstance(text, str):
        return None
    cleaned = text.strip()

    # 1. ISO format check
    iso = parse_iso_date(cleaned)
    if iso:
        return iso

    # 2. '2 Nov 2026' or '28 Oct 2026'
    m1 = re.search(r"\b(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{4}))?\b", cleaned)
    if m1:
        d = int(m1.group(1))
        month_str = m1.group(2).lower()
        y = int(m1.group(3)) if m1.group(3) else default_year
        if month_str in MONTH_MAP and y:
            try:
                return datetime(y, MONTH_MAP[month_str], d).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # 3. 'Nov 2, 2026' or 'Sep 13, 2026'
    m2 = re.search(r"\b([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?(?:\s+(\d{4}))?\b", cleaned)
    if m2:
        month_str = m2.group(1).lower()
        d = int(m2.group(2))
        y = int(m2.group(3)) if m2.group(3) else default_year
        if month_str in MONTH_MAP and y:
            try:
                return datetime(y, MONTH_MAP[month_str], d).strftime("%Y-%m-%d")
            except ValueError:
                pass

    return None


def parse_date_range(text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Parses date range strings such as:
    '2 Nov 2026 – 3 Nov 2026'
    'May 22 - Sep 13, 2026'
    '2 Nov 2026'
    Returns (start_date, end_date) in YYYY-MM-DD format.
    """
    if not text or not isinstance(text, str):
        return None, None

    cleaned = text.strip()

    # Detect year if present
    year_match = re.search(r"\b(20\d\d)\b", cleaned)
    year = int(year_match.group(1)) if year_match else None

    # Split on range separators: en-dash, em-dash, hyphen, or 'to'
    parts = re.split(r"\s*(?:[–—]| - | to )\s*", cleaned)
    if len(parts) == 2:
        start_raw, end_raw = parts[0].strip(), parts[1].strip()
        end_date = parse_single_date(end_raw, default_year=year)
        end_year = int(end_date.split("-")[0]) if end_date else year
        start_date = parse_single_date(start_raw, default_year=end_year)
        return start_date, end_date
    elif len(parts) == 1:
        single = parse_single_date(cleaned, default_year=year)
        return single, None

    return None, None


def parse_team_size(text: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """
    Parses team size dynamically.
    '1-3' -> (1, 3)
    '2–5' -> (2, 5)
    '1' -> (1, 1)
    'Team required: 2 to 6 members' -> (2, 6)
    'up to 4 members' -> (1, 4)
    Returns (team_size_min, team_size_max) or (None, None).
    """
    if not text or not isinstance(text, str):
        return None, None

    cleaned = text.strip()

    # 1. Range: '1-3', '2–5', '1 - 4', '2 to 6'
    m_range = re.search(r"\b(\d+)\s*(?:[–—-]|to)\s*(\d+)\b", cleaned)
    if m_range:
        min_s, max_s = int(m_range.group(1)), int(m_range.group(2))
        if min_s <= max_s:
            return min_s, max_s
        else:
            return max_s, min_s

    # 2. 'Up to 4' or 'Max 4'
    m_upto = re.search(r"(?:up\s+to|max(?:imum)?)\s*(\d+)", cleaned, re.IGNORECASE)
    if m_upto:
        return 1, int(m_upto.group(1))

    # 3. Exact single number '1'
    m_exact = re.search(r"\b(\d+)\b", cleaned)
    if m_exact:
        num = int(m_exact.group(1))
        return num, num

    return None, None


def parse_prize(raw_prize: Optional[str]) -> Tuple[Optional[str], Optional[float], Optional[str]]:
    """
    Parses prize string into:
    (prize, prize_amount, currency)
    Preserves original text, computes numeric amount and currency.
    If numeric amount cannot be determined reliably (e.g. non-cash prizes, swag),
    sets prize_amount to None and currency to None.
    """
    if not raw_prize or not isinstance(raw_prize, str):
        return None, None, None

    cleaned = re.sub(r"\s+", " ", raw_prize).strip()
    if not cleaned or cleaned.lower() in ("n/a", "none", "null"):
        return None, None, None

    lower_text = cleaned.lower()

    # Detect currency symbol or ISO code
    detected_currency = None
    for sym, curr in sorted(CURRENCY_SYMBOLS.items(), key=lambda x: -len(x[0])):
        if sym in lower_text:
            detected_currency = curr
            break

    # If it is explicitly non-cash or no monetary value
    if "non-cash" in lower_text or "swag" in lower_text:
        return cleaned, None, None

    # Multipliers (Lakh, Crore, K, M)
    amount: Optional[float] = None
    lakh_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:lakh|lac)s?\b", cleaned, re.IGNORECASE)
    crore_match = re.search(r"(\d+(?:\.\d+)?)\s*crores?\b", cleaned, re.IGNORECASE)
    m_match = re.search(r"(\d+(?:\.\d+)?)\s*m(?:illion)?\b", cleaned, re.IGNORECASE)
    k_match = re.search(r"(\d+(?:\.\d+)?)\s*k\b", cleaned, re.IGNORECASE)

    if lakh_match:
        amount = float(lakh_match.group(1)) * 100000
        if not detected_currency:
            detected_currency = "INR"
    elif crore_match:
        amount = float(crore_match.group(1)) * 10000000
        if not detected_currency:
            detected_currency = "INR"
    elif m_match:
        amount = float(m_match.group(1)) * 1000000
    elif k_match:
        amount = float(k_match.group(1)) * 1000
    else:
        # Standard digits with commas/dots, but ONLY if preceded or followed by currency indicator
        if detected_currency:
            num_match = re.search(r"(\d[\d,.]*)", cleaned)
            if num_match:
                num_str = num_match.group(1).replace(",", "")
                try:
                    val = float(num_str)
                    amount = int(val) if val.is_integer() else val
                except ValueError:
                    amount = None
        else:
            amount = None

    if amount == 0:
        amount = 0

    if amount is None:
        detected_currency = None

    return cleaned, amount, detected_currency


def parse_location_and_mode(
    raw_location: Optional[str],
    raw_attendance_mode: Optional[str] = None
) -> Tuple[Optional[str], Optional[str]]:
    """
    Extracts location separately from event_mode.
    'Online (Online)' -> location='Online', event_mode='Online'
    'Online' -> location='Online', event_mode='Online'
    'San Francisco, CA' -> location='San Francisco, CA', event_mode='Offline'
    'Bangalore (Hybrid)' -> location='Bangalore', event_mode='Hybrid'
    """
    event_mode: Optional[str] = None
    location: Optional[str] = None

    # Check attendance mode from schema.org JSON-LD if present
    if raw_attendance_mode:
        if "OnlineEventAttendanceMode" in raw_attendance_mode:
            event_mode = "Online"
        elif "OfflineEventAttendanceMode" in raw_attendance_mode:
            event_mode = "Offline"
        elif "MixedEventAttendanceMode" in raw_attendance_mode:
            event_mode = "Hybrid"

    if not raw_location or not isinstance(raw_location, str):
        if event_mode:
            return (event_mode if event_mode == "Online" else None), event_mode
        return None, None

    cleaned = re.sub(r"\s+", " ", raw_location).strip()
    lower = cleaned.lower()

    if "online (online)" in lower or lower == "online":
        location = "Online"
        event_mode = "Online"
    elif "hybrid" in lower:
        event_mode = "Hybrid"
        loc_clean = re.sub(r"\bhybrid\b", "", cleaned, flags=re.IGNORECASE)
        loc_clean = re.sub(r"[\(\)\-\,\/]", " ", loc_clean).strip()
        loc_clean = re.sub(r"\s+", " ", loc_clean)
        location = loc_clean if loc_clean else None
    elif "in-person" in lower or "offline" in lower:
        event_mode = "Offline"
        loc_clean = re.sub(r"\b(in-person|offline)\b", "", cleaned, flags=re.IGNORECASE)
        loc_clean = re.sub(r"[\(\)\-\,\/]", " ", loc_clean).strip()
        loc_clean = re.sub(r"\s+", " ", loc_clean)
        location = loc_clean if loc_clean else "Offline"
    else:
        location = cleaned
        if not event_mode:
            event_mode = "Offline"

    return location, event_mode


# ==============================================================================
# Detail Page Extraction
# ==============================================================================

def fetch_hackathon_details(hackathon_url: str) -> Dict[str, Any]:
    """
    Fetches an individual hackathon landing page using Scrapling Fetcher
    and extracts all structured fields from DOM and JSON-LD.
    Strictly preserves missing data as None (zero fabrication).
    """
    details: Dict[str, Any] = {
        "title": None,
        "organizer": None,
        "status": None,
        "location": None,
        "event_mode": None,
        "start_date": None,
        "end_date": None,
        "registration_deadline": None,
        "team_size_min": None,
        "team_size_max": None,
        "prize": None,
        "prize_amount": None,
        "currency": None,
        "tags": [],
        "tagline": None,
        "submission_deadline_iso": None,
        "submission_deadline_text": None,
        "eligibility": None,
        "rules_url": None,
        "schedule_url": None,
    }

    if not hackathon_url:
        return details

    try:
        logger.info(f"Fetching details for hackathon: {hackathon_url}")
        resp = Fetcher.get(hackathon_url)
        if resp.status != 200:
            logger.warning(f"[WARNING] Failed to fetch {hackathon_url} (HTTP {resp.status})")
            return details

        # 1. Parse JSON-LD metadata for high-fidelity structured information
        json_ld_data: Optional[Dict[str, Any]] = None
        json_ld_scripts = resp.css('script[type="application/ld+json"]::text').getall()
        for script_text in json_ld_scripts:
            try:
                parsed = json.loads(script_text)
                if isinstance(parsed, dict) and parsed.get("@type") == "Event":
                    json_ld_data = parsed
                    break
            except Exception:
                pass

        # 2. Extract Title
        title = None
        h1_text = resp.css("h1::text").getall()
        if h1_text:
            title = clean_text(" ".join(h1_text))
        if not title:
            title = resp.css("#challenge-title::text, .challenge-title::text").get()
            title = clean_text(title)
        if not title and json_ld_data:
            title = clean_text(json_ld_data.get("name"))
        if not title:
            title = resp.css('meta[property="og:title"]::attr(content)').get()
            title = clean_text(title)
        details["title"] = title

        # 3. Extract Organizer
        organizer = None
        org_elem = resp.css(".host-label::text, .challenge-host::text, .host::text, [class*='host-label']::text").get()
        if org_elem:
            organizer = clean_text(org_elem)
        if not organizer:
            org_link = resp.css("a[href*='organization']::text").get()
            organizer = clean_text(org_link)
        if not organizer and json_ld_data:
            org_obj = json_ld_data.get("organizer")
            if isinstance(org_obj, dict):
                org_name = clean_text(org_obj.get("name"))
                if org_name and org_name.lower() != "devpost, inc.":
                    organizer = org_name
        details["organizer"] = organizer

        # 4. Extract Status
        status = None
        status_elem = resp.css(".challenge-status::text, [data-status]::attr(data-status), .status::text").get()
        if status_elem:
            status = clean_text(status_elem)
        details["status"] = status

        # 5. Extract Location & Event Mode
        raw_loc = None
        attendance_mode = json_ld_data.get("eventAttendanceMode") if json_ld_data else None
        if json_ld_data and isinstance(json_ld_data.get("location"), dict):
            loc_obj = json_ld_data["location"]
            addr = loc_obj.get("address")
            if isinstance(addr, dict):
                raw_loc = addr.get("name") or addr.get("addressLocality")
        if not raw_loc:
            loc_elem = resp.css(".location::text, .challenge-location::text, [class*='location']::text").get()
            raw_loc = clean_text(loc_elem)

        loc_val, mode_val = parse_location_and_mode(raw_loc, attendance_mode)
        details["location"] = loc_val
        details["event_mode"] = mode_val

        # 6. Extract Dates (Start Date, End Date)
        start_date = None
        end_date = None
        if json_ld_data:
            start_date = parse_iso_date(json_ld_data.get("startDate"))
            end_date = parse_iso_date(json_ld_data.get("endDate"))

        if not start_date or not end_date:
            dates_text = resp.css(".dates::text, .date-range::text, [class*='date-range']::text").get()
            if dates_text:
                parsed_start, parsed_end = parse_date_range(dates_text)
                start_date = start_date or parsed_start
                end_date = end_date or parsed_end

        details["start_date"] = start_date
        details["end_date"] = end_date

        # 7. Extract Registration Deadline
        deadline_iso = resp.css("[data-iso-date]::attr(data-iso-date)").get()
        details["submission_deadline_iso"] = clean_text(deadline_iso)
        reg_deadline = parse_iso_date(deadline_iso)

        deadline_text = resp.css("[data-dates-text]::text").get()
        if not deadline_text:
            deadline_text = resp.css("#time-left::text").get()
        details["submission_deadline_text"] = clean_text(deadline_text)

        if not reg_deadline and deadline_text:
            reg_deadline = parse_single_date(deadline_text)

        if not reg_deadline:
            logger.warning(f"[WARNING] Could not extract registration_deadline for: {hackathon_url}")
        details["registration_deadline"] = reg_deadline

        # 8. Extract Eligibility criteria list
        eligibility_items = resp.css("#eligibility-list li")
        elig_list = []
        if eligibility_items:
            for item in eligibility_items:
                raw_item = item.get_all_text()
                cleaned_item = clean_text(raw_item)
                if cleaned_item:
                    elig_list.append(cleaned_item)
            details["eligibility"] = elig_list if elig_list else None
        else:
            details["eligibility"] = None

        # 9. Extract Team Size (from label, eligibility items, or page text)
        team_min, team_max = None, None
        team_elem = resp.css(".team-size::text, [data-team-size]::text, [class*='team-size']::text").get()
        if team_elem:
            team_min, team_max = parse_team_size(clean_text(team_elem))

        # Check eligibility items for team size, e.g. "Team required: 2 to 6 members"
        if team_min is None and elig_list:
            for elig_item in elig_list:
                if any(w in elig_item.lower() for w in ["team", "member", "solo"]):
                    t_min, t_max = parse_team_size(elig_item)
                    if t_min is not None:
                        team_min, team_max = t_min, t_max
                        break

        # Fallback to page text search
        if team_min is None:
            all_page_text = " ".join(resp.css("body *::text").getall())
            team_match = re.search(r"(?:team\s*size|teams?\s*of|members?)\s*[:–—\-]?\s*([0-9\s–—\-to]+)", all_page_text, re.IGNORECASE)
            if team_match:
                team_min, team_max = parse_team_size(team_match.group(0))

        details["team_size_min"] = team_min
        details["team_size_max"] = team_max

        # 10. Extract Prize, Prize Amount, and Currency
        prize_raw = None
        prize_link_text = resp.css(".prizes-link *::text").getall()
        if prize_link_text:
            prize_raw = clean_text(" ".join(prize_link_text))
        if not prize_raw:
            prize_elem = resp.css("#prizes .prize-value::text, .total-prize::text, .prize-amount::text").get()
            prize_raw = clean_text(prize_elem)

        p_str, p_amt, p_curr = parse_prize(prize_raw)
        details["prize"] = p_str
        details["prize_amount"] = p_amt
        details["currency"] = p_curr

        # 11. Extract Tags
        tags_raw = resp.css(".theme::text, [class*='theme']::text, .tag::text, #skills li::text, .skills li::text").getall()
        tag_list: List[str] = []
        for t in tags_raw:
            c = clean_text(t)
            if c and c not in tag_list and not c.startswith("http") and len(c) < 50:
                tag_list.append(c)
        details["tags"] = tag_list

        # 12. Tagline
        tagline_elem = resp.css("#introduction h3::text").get()
        if not tagline_elem:
            tagline_elem = resp.css(".challenge-description::text").get()
        if not tagline_elem:
            tagline_elem = resp.css('meta[property="og:description"]::attr(content)').get()
        details["tagline"] = clean_text(tagline_elem)

        # 13. Rules and Schedule URLs
        rules_href = resp.css('a[href*="/rules"]::attr(href)').get()
        if rules_href:
            details["rules_url"] = urljoin(hackathon_url, rules_href)

        schedule_href = resp.css("a.view-all-dates-link::attr(href)").get()
        if schedule_href:
            details["schedule_url"] = urljoin(hackathon_url, schedule_href)

    except Exception as exc:
        logger.error(f"[WARNING] Error extracting details from {hackathon_url}: {exc}")

    return details


# ==============================================================================
# Validation Layer
# ==============================================================================

def validate_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates the scraped record before persistence.
    Invalid optional fields are safely set to None rather than fabricating data.
    """
    validated = dict(record)

    # 1. Mandatory title
    if not validated.get("title") or not str(validated["title"]).strip():
        logger.warning(f"[VALIDATION] Hackathon missing mandatory title: {validated.get('source_url')}")

    # 2. Mandatory source_url
    url = validated.get("source_url")
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        logger.warning(f"[VALIDATION] Invalid source_url: {url}")

    # 3. Validate Dates
    start_d = validated.get("start_date")
    end_d = validated.get("end_date")

    if start_d and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(start_d)):
        logger.warning(f"[VALIDATION] Invalid start_date format '{start_d}' -> setting to None")
        validated["start_date"] = None
        start_d = None

    if end_d and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(end_d)):
        logger.warning(f"[VALIDATION] Invalid end_date format '{end_d}' -> setting to None")
        validated["end_date"] = None
        end_d = None

    if start_d and end_d and start_d > end_d:
        logger.warning(f"[VALIDATION] start_date ({start_d}) > end_date ({end_d}) -> setting invalid end_date to None")
        validated["end_date"] = None

    # 4. Validate Registration Deadline
    reg_d = validated.get("registration_deadline")
    if reg_d and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(reg_d)):
        logger.warning(f"[VALIDATION] Invalid registration_deadline format '{reg_d}' -> setting to None")
        validated["registration_deadline"] = None

    # 5. Validate Team Size
    t_min = validated.get("team_size_min")
    t_max = validated.get("team_size_max")

    if t_min is not None and not isinstance(t_min, int):
        validated["team_size_min"] = None
        t_min = None
    if t_max is not None and not isinstance(t_max, int):
        validated["team_size_max"] = None
        t_max = None

    if t_min is not None and t_max is not None and t_min > t_max:
        logger.warning(f"[VALIDATION] team_size_min ({t_min}) > team_size_max ({t_max}) -> setting to None")
        validated["team_size_min"] = None
        validated["team_size_max"] = None

    # 6. Validate Prize Amount & Currency
    p_amt = validated.get("prize_amount")
    if p_amt is not None:
        try:
            val = float(p_amt)
            if val < 0:
                validated["prize_amount"] = None
        except (ValueError, TypeError):
            validated["prize_amount"] = None

    # 7. Validate Tags
    tags = validated.get("tags")
    if isinstance(tags, list):
        validated["tags"] = [str(t).strip() for t in tags if str(t).strip()]
    else:
        validated["tags"] = []

    return validated


# ==============================================================================
# Supabase Integration (Existing Client & Fallback PostgREST REST API)
# ==============================================================================

def load_env_file(filepath: Optional[str] = None) -> None:
    """Loads environment variables from local .env or fallback paths."""
    search_paths = [
        filepath,
        Path.cwd() / ".env",
        Path(__file__).resolve().parent / ".env",
        Path(__file__).resolve().parent.parent / "hack" / ".env",
    ]
    for p in search_paths:
        if p and Path(p).exists():
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip().strip("\"'")
                            if k and k not in os.environ:
                                os.environ[k] = v
                break
            except Exception:
                pass


def get_supabase_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Retrieves Supabase URL and Key from environment or .env."""
    load_env_file()
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip() or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip() or os.getenv("SUPABASE_ANON_KEY", "").strip()

    if url:
        url = url.rstrip("/")
        if url.endswith("/rest/v1"):
            url = url[:-len("/rest/v1")].rstrip("/")

    return (url if url else None), (key if key else None)


def sync_to_supabase(
    records: List[Dict[str, Any]],
    table_name: str = "hackathons"
) -> Tuple[bool, str, Dict[str, int]]:
    """
    Syncs validated hackathon records to Supabase table using:
    1. supabase-py Client (if available and connected)
    2. PostgREST REST API (urllib standard library fallback)
    Deduplicates on 'source_url' to prevent duplicate records.
    """
    url, key = get_supabase_credentials()
    stats = {"scraped": len(records), "upserted": 0, "failed": 0}

    if not url or not key:
        msg = "SUPABASE_URL or SUPABASE_KEY not configured. Skipping Supabase sync."
        logger.info(f"[SUPABASE] {msg}")
        return False, msg, stats

    if not records:
        return True, "No records to sync.", stats

    # Prepare records for Supabase schema
    db_records: List[Dict[str, Any]] = []
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

    # Attempt 1: Try using supabase-py client if installed
    try:
        from supabase import create_client
        client = create_client(url, key)
        response = client.table(table_name).upsert(
            db_records,
            on_conflict="source_url"
        ).execute()
        count = len(response.data) if response.data else len(db_records)
        stats["upserted"] = count
        msg = f"Successfully synced {count} records to Supabase table '{table_name}'."
        logger.info(f"[SUPABASE] Inserted/Updated: {count} records | Conflict key: source_url")
        return True, msg, stats
    except ImportError:
        pass
    except Exception as exc:
        err_str = str(exc)
        logger.warning(f"[SUPABASE] Client upsert failed: {err_str}")
        if "PGRST204" in err_str or "column" in err_str.lower():
            logger.warning("[WARNING] Supabase schema is missing one or more columns. Please run 'supabase_migration.sql' in your Supabase SQL Editor.")

    # Attempt 2: Direct PostgREST REST API via urllib
    try:
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=representation",
            "User-Agent": "DevpostScraper/1.0",
        }
        endpoint = f"{url}/rest/v1/{table_name}?on_conflict=source_url"
        payload_bytes = json.dumps(db_records, default=str).encode("utf-8")
        req = urllib.request.Request(endpoint, data=payload_bytes, headers=headers, method="POST")

        with urllib.request.urlopen(req, timeout=30) as res:
            res_data = json.loads(res.read().decode("utf-8")) if res.status != 204 else []
            count = len(res_data) if res_data else len(db_records)
            stats["upserted"] = count
            msg = f"Successfully upserted {count} records to Supabase table '{table_name}' via PostgREST."
            logger.info(f"[SUPABASE] Inserted/Updated: {count} records")
            return True, msg, stats
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8") if err.fp else ""
        logger.warning(f"[SUPABASE] PostgREST HTTP {err.code}: {err_body}")
        if "column" in err_body.lower() or "not find the" in err_body.lower():
            logger.warning("[WARNING] Supabase schema needs migration. Run 'supabase_migration.sql' in your Supabase SQL Editor.")
        stats["failed"] = len(db_records)
        return False, f"HTTP {err.code}: {err_body}", stats
    except Exception as e:
        logger.warning(f"[SUPABASE] Error connecting to Supabase: {e}")
        stats["failed"] = len(db_records)
        return False, str(e), stats


# ==============================================================================
# Main Scraper Execution
# ==============================================================================

def scrape_devpost(
    start_page: int = 2,
    all_pages: bool = False,
    fetch_details: bool = True
) -> List[Dict[str, Any]]:
    """
    Scrapes ongoing and upcoming hackathons from Devpost API and individual detail pages.
    Filters strictly for ongoing ('open') and upcoming ('upcoming') hackathons.
    Extracts all 17 required structured fields.
    """
    results: List[Dict[str, Any]] = []
    current_page = start_page

    while True:
        query_url = (
            f"{BASE_API_URL}?"
            f"challenge_type[]=online&"
            f"challenge_type[]=in-person&"
            f"order_by=deadline&"
            f"page={current_page}&"
            f"status[]=upcoming&"
            f"status[]=open"
        )
        logger.info(f"Scraping listing page {current_page}: {query_url}")

        try:
            resp = Fetcher.get(query_url)
            if resp.status != 200:
                logger.error(f"[WARNING] Failed to fetch page {current_page} (HTTP {resp.status})")
                break

            data = resp.json()
        except Exception as exc:
            logger.error(f"[WARNING] Error fetching page {current_page}: {exc}")
            break

        hackathons_raw = data.get("hackathons", [])
        if not hackathons_raw:
            logger.info(f"No hackathons found on page {current_page}.")
            break

        for item in hackathons_raw:
            open_state = item.get("open_state")
            # Strict filter: only ongoing ('open') and upcoming ('upcoming')
            if open_state not in ("open", "upcoming"):
                logger.info(f"Skipping hackathon ID {item.get('id')} with status: {open_state}")
                continue

            # Exclude past events where winners are announced
            if item.get("winners_announced", False) is True:
                logger.info(f"Skipping hackathon ID {item.get('id')} because winners are announced")
                continue

            hackathon_url = item.get("url")
            source_website = extract_domain(hackathon_url) or "devpost.com"

            # Parse initial location from API listing
            raw_loc = item.get("displayed_location")
            raw_loc_str: Optional[str] = None
            if isinstance(raw_loc, dict):
                raw_loc_str = raw_loc.get("location")
            elif isinstance(raw_loc, str):
                raw_loc_str = raw_loc

            loc_val, mode_val = parse_location_and_mode(raw_loc_str)

            # Parse initial prize from API listing
            raw_prize = clean_html_tags(item.get("prize_amount"))
            p_str, p_amt, p_curr = parse_prize(raw_prize)

            # Themes/Tags from API listing
            raw_themes = item.get("themes") or []
            api_tags = [t.get("name") for t in raw_themes if isinstance(t, dict) and t.get("name")]

            # Initial dates from API listing
            sub_dates_text = clean_text(item.get("submission_period_dates"))
            api_start_d, api_end_d = parse_date_range(sub_dates_text)

            # Construct base record
            now_iso = datetime.now(timezone.utc).isoformat()
            record: Dict[str, Any] = {
                # 17 Core Structured Fields
                "title": clean_text(item.get("title")),
                "organizer": clean_text(item.get("organization_name")),
                "status": open_state,
                "location": loc_val,
                "event_mode": mode_val,
                "start_date": api_start_d,
                "end_date": api_end_d,
                "registration_deadline": None,
                "team_size_min": None,
                "team_size_max": None,
                "prize": p_str,
                "prize_amount": p_amt,
                "currency": p_curr,
                "tags": api_tags,
                "source_url": hackathon_url,
                "source_website": source_website,
                "scraped_at": now_iso,

                # Backward compatibility metadata
                "id": item.get("id"),
                "tagline": None,
                "url": hackathon_url,
                "location_type": mode_val or "N/A",
                "location_address": loc_val or "N/A",
                "submission_period_dates": sub_dates_text,
                "submission_deadline_text": None,
                "submission_deadline_iso": None,
                "time_left_to_submission": clean_text(item.get("time_left_to_submission")),
                "cash_prizes_count": (item.get("prizes_counts") or {}).get("cash"),
                "other_prizes_count": (item.get("prizes_counts") or {}).get("other"),
                "registrations_count": item.get("registrations_count"),
                "organization_name": clean_text(item.get("organization_name")),
                "themes": api_tags,
                "eligibility": None,
                "rules_url": None,
                "schedule_url": None,
                "thumbnail_url": ("https:" + item.get("thumbnail_url")) if (item.get("thumbnail_url") or "").startswith("//") else item.get("thumbnail_url"),
                "featured": bool(item.get("featured", False)),
                "managed_by_devpost": bool(item.get("managed_by_devpost_badge", False)),
                "invite_only": bool(item.get("invite_only", False)),
            }

            # Fetch deep detail page to extract all visible fields
            if fetch_details and hackathon_url:
                deep = fetch_hackathon_details(hackathon_url)

                # Prioritize detail page values
                if deep.get("title"):
                    record["title"] = deep["title"]
                if deep.get("organizer"):
                    record["organizer"] = deep["organizer"]
                if deep.get("status"):
                    record["status"] = deep["status"]
                if deep.get("location"):
                    record["location"] = deep["location"]
                if deep.get("event_mode"):
                    record["event_mode"] = deep["event_mode"]
                if deep.get("start_date"):
                    record["start_date"] = deep["start_date"]
                if deep.get("end_date"):
                    record["end_date"] = deep["end_date"]
                if deep.get("registration_deadline"):
                    record["registration_deadline"] = deep["registration_deadline"]
                if deep.get("team_size_min") is not None:
                    record["team_size_min"] = deep["team_size_min"]
                if deep.get("team_size_max") is not None:
                    record["team_size_max"] = deep["team_size_max"]
                if deep.get("prize"):
                    record["prize"] = deep["prize"]
                    record["prize_amount"] = deep.get("prize_amount")
                    record["currency"] = deep.get("currency")

                # Merge tags (detail page tags + API tags deduplicated)
                combined_tags = list(record["tags"])
                for t in deep.get("tags", []):
                    if t not in combined_tags:
                        combined_tags.append(t)
                record["tags"] = combined_tags

                # Metadata & backward compatibility fields
                record["tagline"] = deep.get("tagline")
                record["submission_deadline_iso"] = deep.get("submission_deadline_iso")
                record["submission_deadline_text"] = deep.get("submission_deadline_text")
                record["eligibility"] = deep.get("eligibility")
                record["rules_url"] = deep.get("rules_url")
                record["schedule_url"] = deep.get("schedule_url")

            # Fallback registration deadline from submission_deadline_iso
            if not record.get("registration_deadline") and record.get("submission_deadline_iso"):
                record["registration_deadline"] = parse_iso_date(record["submission_deadline_iso"])

            # Validate record
            validated = validate_record(record)
            results.append(validated)

            # Log formatted [SCRAPED] summary
            logger.info(
                f"\n[SCRAPED]\n"
                f"Title: {validated.get('title')}\n"
                f"Organizer: {validated.get('organizer')}\n"
                f"Status: {validated.get('status')}\n"
                f"Location: {validated.get('location')}\n"
                f"Event Mode: {validated.get('event_mode')}\n"
                f"Start Date: {validated.get('start_date')}\n"
                f"End Date: {validated.get('end_date')}\n"
                f"Registration Deadline: {validated.get('registration_deadline')}\n"
                f"Team Size: {validated.get('team_size_min')} - {validated.get('team_size_max')}\n"
                f"Prize: {validated.get('prize')}\n"
                f"Prize Amount: {validated.get('prize_amount')}\n"
                f"Currency: {validated.get('currency')}\n"
                f"Tags: {validated.get('tags')}\n"
                f"Source URL: {validated.get('source_url')}"
            )

        if not all_pages:
            break

        meta = data.get("meta") or {}
        total_count = meta.get("total_count", 0)
        per_page = meta.get("per_page", 9)
        max_page = (total_count + per_page - 1) // per_page if per_page else 1

        if current_page >= max_page:
            logger.info(f"Reached final page ({current_page}/{max_page}).")
            break

        current_page += 1

    return results


# ==============================================================================
# File Export Functions (JSON & CSV)
# ==============================================================================

def save_data(
    hackathons: List[Dict[str, Any]],
    json_path: str = "hackathons.json",
    csv_path: str = "hackathons.csv"
) -> None:
    """Saves scraped hackathon records to JSON and CSV formats."""
    # 1. Save JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(hackathons, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(hackathons)} hackathons to {json_path}")

    # 2. Save CSV
    if not hackathons:
        logger.warning("No hackathons to save to CSV.")
        return

    csv_fields = [
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
        "id",
        "tagline",
        "url",
        "submission_period_dates",
        "submission_deadline_text",
        "submission_deadline_iso",
        "time_left_to_submission",
        "cash_prizes_count",
        "other_prizes_count",
        "registrations_count",
        "eligibility",
        "rules_url",
        "schedule_url",
        "thumbnail_url",
        "featured",
        "managed_by_devpost",
        "invite_only",
    ]

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        for h in hackathons:
            row = dict(h)
            if isinstance(row.get("tags"), list):
                row["tags"] = ", ".join(row["tags"])
            if isinstance(row.get("themes"), list):
                row["themes"] = ", ".join(row["themes"])
            if isinstance(row.get("eligibility"), list):
                row["eligibility"] = " | ".join(row["eligibility"])
            writer.writerow(row)
    logger.info(f"Saved {len(hackathons)} hackathons to {csv_path}")


# ==============================================================================
# CLI Entry Point
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Scrape ongoing and upcoming Devpost hackathons with Supabase sync.")
    parser.add_argument("--page", type=int, default=DEFAULT_PAGE, help=f"Target page number (default: {DEFAULT_PAGE})")
    parser.add_argument("--all-pages", action="store_true", help="Scrape all available pages starting from --page")
    parser.add_argument("--no-details", action="store_true", help="Skip fetching individual landing pages for details")
    parser.add_argument("--no-supabase", action="store_true", help="Skip Supabase synchronization")
    parser.add_argument("--table", default="hackathons", help="Target Supabase table name (default: 'hackathons')")
    parser.add_argument("--json-out", default="hackathons.json", help="Path to output JSON file")
    parser.add_argument("--csv-out", default="hackathons.csv", help="Path to output CSV file")
    args = parser.parse_args()

    print("=" * 70)
    print("Devpost Hackathon Scraper (Powered by Scrapling & Supabase)")
    print(f"Target Page: {args.page} (All pages: {args.all_pages})")
    print(f"Fetch detail pages: {not args.no_details}")
    print(f"Sync to Supabase: {not args.no_supabase} (Table: {args.table})")
    print("=" * 70)

    hackathons = scrape_devpost(
        start_page=args.page,
        all_pages=args.all_pages,
        fetch_details=not args.no_details
    )

    save_data(hackathons, json_path=args.json_out, csv_path=args.csv_out)

    # Supabase synchronization
    if not args.no_supabase:
        print("\n" + "=" * 70)
        print("Synchronizing records with Supabase...")
        print("=" * 70)
        success, msg, stats = sync_to_supabase(hackathons, table_name=args.table)
        print(f"Supabase sync status: {'SUCCESS' if success else 'NOTICE'}")
        print(f"Details: {msg}")

    print("=" * 70)
    print(f"Successfully scraped {len(hackathons)} ongoing/upcoming hackathons.")
    print(f"Results saved to:")
    print(f"  - {args.json_out}")
    print(f"  - {args.csv_out}")
    print("=" * 70)


if __name__ == "__main__":
    main()
