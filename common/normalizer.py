"""Central Normalization and Parsing Library.

Provides reliable parsing and normalization for hackathon details extracted from
any platform (Devfolio, Devpost, HackerEarth, Knowafest, Unstop).
Strictly adheres to:
- Zero fabrication: returns None or [] if unparseable; never hallucinates or guesses.
- No silent alteration: inconsistent or inverted data yields warnings and None.
"""

from datetime import datetime
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("hackathon_scraper.normalizer")

# Month name to number mapping
MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def clean_text(text: Optional[str]) -> Optional[str]:
    """Clean whitespace and common noise characters from text."""
    if not text:
        return None
    cleaned = re.sub(r"[\r\n\t]+", " ", str(text))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else None


def extract_domain(url: Optional[str]) -> Optional[str]:
    """Extract clean domain from a URL (e.g. 'devpost.com')."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc if netloc else None
    except Exception:
        return None


def parse_single_date(text: Optional[str]) -> Optional[str]:
    """Parse a single date string into ISO format YYYY-MM-DD.

    Returns None if date cannot be confidently parsed without fabrication.
    """
    if not text:
        return None

    s = clean_text(text)
    if not s:
        return None

    # Filter out obvious non-date phrases
    lower_s = s.lower()
    if any(phrase in lower_s for phrase in ["ended", "tba", "tbd", "to be announced", "upcoming soon", "closed", "open to all"]):
        if not re.search(r"\d{4}", s):
            return None

    # Remove conversational prefixes
    s = re.sub(
        r"^(registration\s+closes|starts|ends|deadline|submission\s+deadline|begins|registered\s+by|apply\s+by|from|to|on)\s*[:\-–]?\s*",
        "",
        s,
        flags=re.IGNORECASE,
    ).strip()

    # 1. ISO format with optional time/timezone: YYYY-MM-DD
    iso_match = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", s)
    if iso_match:
        y, m, d = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
        try:
            return datetime(y, m, d).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # 2. DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY
    dmy_match = re.search(r"\b(\d{1,2})[/\.\-](\d{1,2})[/\.\-](\d{2,4})\b", s)
    if dmy_match:
        d, m, y_str = int(dmy_match.group(1)), int(dmy_match.group(2)), dmy_match.group(3)
        y = int(y_str)
        if len(y_str) == 2:
            y = 2000 + y if y < 70 else 1900 + y
        if 1 <= m <= 12 and 1 <= d <= 31:
            try:
                return datetime(y, m, d).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # 3. Day Month Year: '2 Nov 2026', '12th September 2026', '2 November, 2026'
    dmy_word = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)[,\s]+(\d{4})\b", s
    )
    if dmy_word:
        d = int(dmy_word.group(1))
        m_str = dmy_word.group(2).lower()
        y = int(dmy_word.group(3))
        m = MONTH_MAP.get(m_str) or MONTH_MAP.get(m_str[:3])
        if m:
            try:
                return datetime(y, m, d).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # 4. Month Day Year: 'Nov 2, 2026', 'September 12th, 2026'
    mdy_word = re.search(
        r"\b([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?[,\s]+(\d{4})\b", s
    )
    if mdy_word:
        m_str = mdy_word.group(1).lower()
        d = int(mdy_word.group(2))
        y = int(mdy_word.group(3))
        m = MONTH_MAP.get(m_str) or MONTH_MAP.get(m_str[:3])
        if m:
            try:
                return datetime(y, m, d).strftime("%Y-%m-%d")
            except ValueError:
                pass

    return None


def parse_date_range(text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Parse a date range string into (start_date, end_date) in ISO format YYYY-MM-DD.

    Returns (None, None) if not parseable.
    """
    if not text:
        return None, None

    s = clean_text(text)
    if not s:
        return None, None

    # Format 1: 'Sep 25 - 26, 2026' or 'Sep 25 – 26, 2026'
    m_same_month1 = re.search(r"^\s*([A-Za-z]+)\s+(\d{1,2})\s*[-–—]\s*(\d{1,2}),?\s+(\d{4})\s*$", s)
    if m_same_month1:
        month_str = m_same_month1.group(1).lower()
        d1, d2, year = int(m_same_month1.group(2)), int(m_same_month1.group(3)), int(m_same_month1.group(4))
        month = MONTH_MAP.get(month_str) or MONTH_MAP.get(month_str[:3])
        if month:
            try:
                sd = datetime(year, month, d1).strftime("%Y-%m-%d")
                ed = datetime(year, month, d2).strftime("%Y-%m-%d")
                return sd, ed
            except ValueError:
                pass

    # Format 2: '26–27 September 2026' or '26-27 Sep 2026'
    m_same_month2 = re.search(r"^\s*(\d{1,2})\s*[-–—]\s*(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})\s*$", s)
    if m_same_month2:
        d1, d2 = int(m_same_month2.group(1)), int(m_same_month2.group(2))
        month_str = m_same_month2.group(3).lower()
        year = int(m_same_month2.group(4))
        month = MONTH_MAP.get(month_str) or MONTH_MAP.get(month_str[:3])
        if month:
            try:
                sd = datetime(year, month, d1).strftime("%Y-%m-%d")
                ed = datetime(year, month, d2).strftime("%Y-%m-%d")
                return sd, ed
            except ValueError:
                pass

    # Format 3: '2 Nov 2026 – 3 Nov 2026' or 'Nov 2, 2026 - Nov 3, 2026'
    parts = re.split(r"\s*(?:–|—|-|\bto\b)\s*", s, maxsplit=1)
    if len(parts) == 2:
        sd = parse_single_date(parts[0])
        ed = parse_single_date(parts[1])

        # If first part is missing year, borrow year from second part
        if not sd and ed:
            m_year = re.search(r"\d{4}", parts[1])
            if m_year:
                sd = parse_single_date(parts[0] + " " + m_year.group(0))

        if sd and ed:
            if sd > ed:
                logger.warning("[DATE PARSER] Inconsistent date range: %s > %s. Nullifying end_date.", sd, ed)
                return sd, None
            return sd, ed

        if sd:
            return sd, None
        if ed:
            return ed, None

    # Fallback to parsing single date as start date
    single = parse_single_date(s)
    return (single, None) if single else (None, None)


def parse_team_size(raw_val: Any, is_team: Optional[bool] = None) -> Tuple[Optional[int], Optional[int]]:
    """Parse team size into (team_size_min, team_size_max).

    Returns (None, None) if missing or invalid.
    If team_size_min > team_size_max, logs a warning and returns (None, None) (no silent swapping).
    """
    if raw_val is None:
        if is_team is False:
            return 1, 1
        return None, None

    # Handle dictionary representation: e.g. {"min": 1, "max": 3}
    if isinstance(raw_val, dict):
        t_min = raw_val.get("min") or raw_val.get("min_team_size") or raw_val.get("team_size_min")
        t_max = raw_val.get("max") or raw_val.get("max_team_size") or raw_val.get("team_size_max")
        try:
            t_min = int(t_min) if t_min is not None else None
            t_max = int(t_max) if t_max is not None else None
        except (ValueError, TypeError):
            t_min, t_max = None, None

        if t_min is not None and t_max is not None and t_min > t_max:
            logger.warning("[TEAM SIZE] Inconsistent team size in dict (%s > %s). Returning NULL.", t_min, t_max)
            return None, None
        return t_min, t_max

    # Handle integer input
    if isinstance(raw_val, int):
        if raw_val >= 1:
            return raw_val, raw_val
        return None, None

    text = clean_text(str(raw_val))
    if not text:
        if is_team is False:
            return 1, 1
        return None, None

    # Pattern 1: '1-3', '2–5', '1 to 4', '3 to 5 members'
    range_match = re.search(r"(\d+)\s*(?:–|—|-|\bto\b)\s*(\d+)", text)
    if range_match:
        t_min = int(range_match.group(1))
        t_max = int(range_match.group(2))
        if t_min > t_max:
            logger.warning("[TEAM SIZE] Inconsistent team size range '%s' (min %s > max %s). Returning NULL.", text, t_min, t_max)
            return None, None
        return t_min, t_max

    # Pattern 2: 'up to 4 members', 'max of 5'
    up_to_match = re.search(r"\b(?:up to|max(?:imum)?\s*(?:of)?)\s*(\d+)", text, re.IGNORECASE)
    if up_to_match:
        return 1, int(up_to_match.group(1))

    # Pattern 3: Single number: '1', 'teams of 4 members'
    single_match = re.search(r"\b(\d+)\b", text)
    if single_match:
        val = int(single_match.group(1))
        if val >= 1:
            return val, val

    return None, None


def parse_prize(raw_val: Any) -> Tuple[Optional[str], Optional[float], Optional[str]]:
    """Parse prize text into (raw_prize_str, prize_amount, currency).

    Preserves raw prize text. Parses numeric amount and currency if present.
    If non-numeric, preserves raw string, with prize_amount=None, currency=None.
    """
    if raw_val is None:
        return None, None, None

    text = clean_text(str(raw_val))
    if not text:
        return None, None, None

    # Currency identification
    curr = None
    if "₹" in text or "rs" in text.lower() or "inr" in text.lower():
        curr = "INR"
    elif "$" in text or "usd" in text.lower():
        curr = "USD"
    elif "€" in text or "eur" in text.lower():
        curr = "EUR"
    elif "£" in text or "gbp" in text.lower():
        curr = "GBP"

    # 1. Denomination words: Lakh, Crore
    lakh_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:lakh|lac)s?", text, re.IGNORECASE)
    if lakh_match:
        amt = float(lakh_match.group(1)) * 100000.0
        return text, amt, curr or "INR"

    crore_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:crore|cr)s?", text, re.IGNORECASE)
    if crore_match:
        amt = float(crore_match.group(1)) * 10000000.0
        return text, amt, curr or "INR"

    # 2. Denomination words: K, M (e.g. 50k, 1.5M)
    km_match = re.search(r"(\d+(?:\.\d+)?)\s*([km])\b", text, re.IGNORECASE)
    if km_match:
        val = float(km_match.group(1))
        mult = 1000.0 if km_match.group(2).lower() == "k" else 1000000.0
        return text, val * mult, curr

    # 3. Formatted numbers: e.g. 1,00,000 or 10,000 or 5000.00
    num_match = re.search(r"(?:₹|\$|€|£|rs\.?|inr|usd|eur)?\s*([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
    if num_match:
        raw_num = num_match.group(1).replace(",", "")
        try:
            amt = float(raw_num)
            if amt > 0:
                return text, amt, curr
        except ValueError:
            pass

    # If non-numeric but contains text (e.g. 'Exciting Swag and Mentorship')
    if any(c.isalpha() for c in text):
        return text, None, None

    return None, None, None


def parse_location_and_mode(
    location_text: Optional[str],
    mode_hint: Optional[str] = None,
    address_dict: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Disambiguate physical location and event mode ('Online', 'Offline', 'Hybrid')."""
    loc = clean_text(location_text)
    mode = None

    # Check mode hints
    combined = f"{loc or ''} {mode_hint or ''}".lower()

    if "hybrid" in combined:
        mode = "Hybrid"
    elif "online" in combined or "virtual" in combined:
        mode = "Online"
    elif "offline" in combined or "in-person" in combined or "venue" in combined or "on-site" in combined:
        mode = "Offline"

    # Address dictionary handling
    if address_dict and isinstance(address_dict, dict):
        parts = []
        for key in ["city", "state"]:
            val = address_dict.get(key)
            if val and isinstance(val, str) and val.strip():
                parts.append(val.strip())
        country = address_dict.get("country")
        if isinstance(country, dict) and country.get("name"):
            parts.append(str(country["name"]).strip())
        elif isinstance(country, str) and country.strip():
            parts.append(country.strip())
        if parts:
            loc = ", ".join(parts)
            if not mode:
                mode = "Offline"

    # Normalize location string
    if loc:
        if loc.lower() in ("online (online)", "online", "virtual"):
            loc = "Online"
            mode = "Online"
        elif not mode:
            mode = "Offline"

    return loc, mode


def clean_tags(tags: Any) -> List[str]:
    """Clean and deduplicate tag list, preserving original capitalization."""
    if not tags:
        return []

    raw_list = tags if isinstance(tags, (list, tuple, set)) else [tags]
    cleaned = []
    seen = set()

    for item in raw_list:
        if item is None:
            continue
        # Support dict tags like {'skill': 'AI'} or {'name': 'Cloud'}
        if isinstance(item, dict):
            val = item.get("skill") or item.get("name") or item.get("tag") or item.get("label")
        else:
            val = str(item)

        if not val:
            continue

        # Strip bullet points, leading/trailing punctuation
        t = re.sub(r"^[\s•\-\*]+", "", str(val)).strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            cleaned.append(t)

    return cleaned


def validate_record(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate a dictionary record against core integrity rules.

    Returns the validated dictionary or None if the record is invalid.
    Never swaps inverted values; sets invalid fields to None.
    """
    if not record or not isinstance(record, dict):
        return None

    title = clean_text(record.get("title") or record.get("name"))
    source_url = clean_text(record.get("source_url") or record.get("url"))

    if not title:
        logger.warning("[VALIDATION] Dropping record with missing title: %s", source_url)
        return None

    if not source_url:
        logger.warning("[VALIDATION] Dropping record with missing source_url: %s", title)
        return None

    parsed = urlparse(source_url)
    if not parsed.scheme or not parsed.netloc:
        logger.warning("[VALIDATION] Dropping record with invalid source_url: %s", source_url)
        return None

    validated = dict(record)
    validated["title"] = title
    validated["source_url"] = source_url

    # Normalize dates
    s_date = parse_single_date(validated.get("start_date"))
    e_date = parse_single_date(validated.get("end_date"))
    reg_dl = parse_single_date(validated.get("registration_deadline"))

    if validated.get("start_date") and not s_date:
        logger.warning("[VALIDATION] Invalid start_date '%s' for '%s'. Setting to NULL.", validated.get("start_date"), title)
    if validated.get("end_date") and not e_date:
        logger.warning("[VALIDATION] Invalid end_date '%s' for '%s'. Setting to NULL.", validated.get("end_date"), title)
    if validated.get("registration_deadline") and not reg_dl:
        logger.warning("[VALIDATION] Invalid registration_deadline '%s' for '%s'. Setting to NULL.", validated.get("registration_deadline"), title)

    # Date ordering check
    if s_date and e_date and s_date > e_date:
        logger.warning("[VALIDATION] start_date (%s) > end_date (%s) for %s. Setting end_date to NULL.", s_date, e_date, source_url)
        e_date = None

    validated["start_date"] = s_date
    validated["end_date"] = e_date
    validated["registration_deadline"] = reg_dl

    # Team size check
    t_min, t_max = validated.get("team_size_min"), validated.get("team_size_max")
    try:
        t_min = int(t_min) if t_min is not None and str(t_min).isdigit() else (int(t_min) if isinstance(t_min, int) else None)
        t_max = int(t_max) if t_max is not None and str(t_max).isdigit() else (int(t_max) if isinstance(t_max, int) else None)
    except (ValueError, TypeError):
        t_min, t_max = None, None

    if t_min is not None and t_max is not None and t_min > t_max:
        logger.warning("[VALIDATION] team_size_min (%s) > team_size_max (%s) for %s. Setting to NULL.", t_min, t_max, source_url)
        t_min, t_max = None, None

    validated["team_size_min"] = t_min
    validated["team_size_max"] = t_max

    # Prize amount check
    p_amt = validated.get("prize_amount")
    if p_amt is not None:
        try:
            p_amt = float(p_amt)
            if p_amt < 0:
                logger.warning("[VALIDATION] Invalid prize_amount '%s' for %s. Setting to NULL.", p_amt, source_url)
                p_amt = None
        except (ValueError, TypeError):
            p_amt = None
    validated["prize_amount"] = p_amt

    # Tags cleaning
    validated["tags"] = clean_tags(validated.get("tags"))

    return validated
