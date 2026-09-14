"""Devfolio Hackathon Scraper using Scrapling.

Extracts comprehensive hackathon details from https://devfolio.co/hackathons using Scrapling's
undetectable Fetcher engine and adaptive selector system.

Extracts all structured fields from hackathon detail pages, normalizes dates,
team sizes, prizes/currencies, location and event modes, tags, and metadata,
validates records against data integrity rules, and syncs them to Supabase.
"""

import argparse
import csv
from datetime import datetime, timezone
import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from scrapling import Fetcher

# Try to import dotenv for loading environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("devfolio_scraper")

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
    "dec": 12, "december": 12
}


def identify_link_type(url: str) -> str:
    """Categorize an external link based on its domain."""
    domain = urlparse(url).netloc.lower()
    if "twitter.com" in domain or "x.com" in domain:
        return "x_twitter"
    if "discord.gg" in domain or "discord.com" in domain:
        return "discord"
    if "instagram.com" in domain:
        return "instagram"
    if "github.com" in domain:
        return "github"
    if "linkedin.com" in domain:
        return "linkedin"
    if "facebook.com" in domain:
        return "facebook"
    if "t.me" in domain or "telegram" in domain:
        return "telegram"
    if "youtube.com" in domain:
        return "youtube"
    return "website"


def extract_cover_image(card: Any) -> str:
    """Extract and unquote hackathon cover banner image if present."""
    imgs = card.css("img::attr(src)").getall()
    for img in imgs:
        if "assets.devfolio.co/hackathons" in img or "cover" in img:
            if "_next/image?url=" in img:
                parsed = urlparse(img)
                qs = parse_qs(parsed.query)
                if qs.get("url"):
                    return unquote(qs["url"][0])
            return unquote(img)
    return ""


# ==============================================================================
# Normalization & Parsing Helpers
# ==============================================================================

def parse_single_date(text: Optional[str]) -> Optional[str]:
    """Parse a date string into ISO format YYYY-MM-DD.

    Returns None if the date cannot be confidently parsed.
    """
    if not text:
        return None
    s = str(text).strip()
    if not s:
        return None

    # Already ISO format YYYY-MM-DD (e.g. "2026-11-02T00:00:00" or "2026-11-02")
    m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m_iso:
        try:
            d = datetime(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
            return d.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # Remove ordinal suffixes: 1st, 2nd, 3rd, 4th, etc.
    s_clean = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s, flags=re.IGNORECASE)

    # 1. Format: "2 Nov 2026" or "28 Oct 2026"
    m1 = re.search(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b", s_clean)
    if m1:
        day, month_str, year = int(m1.group(1)), m1.group(2).lower(), int(m1.group(3))
        month = MONTH_MAP.get(month_str[:3])
        if month:
            try:
                return datetime(year, month, day).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # 2. Format: "Nov 2, 2026" or "September 25, 2026"
    m2 = re.search(r"\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b", s_clean)
    if m2:
        month_str, day, year = m2.group(1).lower(), int(m2.group(2)), int(m2.group(3))
        month = MONTH_MAP.get(month_str[:3])
        if month:
            try:
                return datetime(year, month, day).strftime("%Y-%m-%d")
            except ValueError:
                pass

    # 3. Format: "25/09/26" or "25/09/2026"
    m3 = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", s_clean)
    if m3:
        day, month, year = int(m3.group(1)), int(m3.group(2)), int(m3.group(3))
        if year < 100:
            year += 2000
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def parse_date_range(text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Parse a date range into (start_date, end_date) in ISO format YYYY-MM-DD.

    Supports both normal hyphen '-' and en dash '–' / em dash '—'.
    """
    if not text:
        return None, None
    s = str(text).strip()
    if not s:
        return None, None

    # Format: "Sep 25 - 26, 2026" or "Sep 25 – 26, 2026"
    m_same_month = re.search(r"([A-Za-z]+)\s+(\d{1,2})\s*[-–—]\s*(\d{1,2}),?\s+(\d{4})", s)
    if m_same_month:
        month_str = m_same_month.group(1).lower()
        d1, d2, year = int(m_same_month.group(2)), int(m_same_month.group(3)), int(m_same_month.group(4))
        month = MONTH_MAP.get(month_str[:3])
        if month:
            try:
                sd = datetime(year, month, d1).strftime("%Y-%m-%d")
                ed = datetime(year, month, d2).strftime("%Y-%m-%d")
                return sd, ed
            except ValueError:
                pass

    # Format: "2 Nov 2026 – 3 Nov 2026" or "Nov 2, 2026 - Nov 3, 2026"
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


def parse_team_size(raw_val: Any) -> Tuple[Optional[int], Optional[int]]:
    """Parse team size dynamically into (team_size_min, team_size_max).

    Supports:
      "1-3" -> (1, 3)
      "2–5" -> (2, 5)
      "1" -> (1, 1)
      "2 to 4 members" -> (2, 4)
    """
    if raw_val is None:
        return None, None
    s = str(raw_val).strip()
    if not s:
        return None, None

    # Range pattern
    m_range = re.search(r"(\d+)\s*[-–—to]+\s*(\d+)", s, flags=re.IGNORECASE)
    if m_range:
        try:
            t_min = int(m_range.group(1))
            t_max = int(m_range.group(2))
            if t_min <= t_max:
                return t_min, t_max
            return t_max, t_min
        except ValueError:
            return None, None

    # Single integer pattern
    m_single = re.search(r"\b(\d+)\b", s)
    if m_single:
        try:
            val = int(m_single.group(1))
            return val, val
        except ValueError:
            return None, None

    return None, None


def parse_prize(raw_val: Any) -> Tuple[Optional[str], Optional[float], Optional[str]]:
    """Parse prize text into (prize, prize_amount, currency).

    Preserves the original prize string while dynamically computing
    the numeric amount and currency code.
    """
    if raw_val is None:
        return None, None, None
    s = str(raw_val).strip()
    if not s:
        return None, None, None

    prize_str = s
    currency = None

    if "₹" in s or "inr" in s.lower() or "rs" in s.lower():
        currency = "INR"
    elif "$" in s or "usd" in s.lower():
        currency = "USD"
    elif "€" in s or "eur" in s.lower():
        currency = "EUR"
    elif "£" in s or "gbp" in s.lower():
        currency = "GBP"

    # 1. Denominations: Lakh / Lac, Crore / Cr, k
    m_lakh = re.search(r"([\d\.]+)\s*(?:lakh|lac)s?", s, flags=re.IGNORECASE)
    if m_lakh:
        try:
            amount = float(m_lakh.group(1)) * 100000
            return prize_str, amount, currency or "INR"
        except ValueError:
            pass

    m_cr = re.search(r"([\d\.]+)\s*(?:crore|cr)s?", s, flags=re.IGNORECASE)
    if m_cr:
        try:
            amount = float(m_cr.group(1)) * 10000000
            return prize_str, amount, currency or "INR"
        except ValueError:
            pass

    m_k = re.search(r"([\d\.]+)\s*k\b", s, flags=re.IGNORECASE)
    if m_k:
        try:
            amount = float(m_k.group(1)) * 1000
            return prize_str, amount, currency
        except ValueError:
            pass

    # 2. Standard numbers: e.g. "1,00,000", "10,000", "500"
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
    is_online: Optional[bool] = None,
    is_hybrid: Optional[bool] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Extract location separately from event mode.

    Normalizes "Online (Online)" -> location="Online", event_mode="Online".
    """
    event_mode = None
    if is_hybrid:
        event_mode = "Hybrid"
    elif is_online:
        event_mode = "Online"

    loc_clean = None
    if raw_loc:
        loc_clean = raw_loc.strip()

    if loc_clean and re.match(r"^online\s*\(\s*online\s*\)$", loc_clean, flags=re.IGNORECASE):
        loc_clean = "Online"
        if not event_mode:
            event_mode = "Online"
    elif loc_clean and loc_clean.lower() == "online":
        if not event_mode:
            event_mode = "Online"

    if raw_mode:
        m = raw_mode.strip().capitalize()
        if m in ("Online", "Offline", "Hybrid"):
            event_mode = m

    if not event_mode:
        if loc_clean and loc_clean.lower() != "online":
            event_mode = "Offline"
        elif loc_clean and loc_clean.lower() == "online":
            event_mode = "Online"

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


def extract_organizer_name(desc: str, page_texts: List[str]) -> Optional[str]:
    """Extract organizer name dynamically from page description or DOM text.

    Returns None if no organizer is mentioned (no fabrication).
    """
    corpus = (desc or "") + "\n" + " ".join(page_texts[:100])
    
    # 1. Pattern: "Organized by ..."
    m1 = re.search(
        r"[Oo]rganized by (?:the )?([A-Za-z0-9\s&,\.\-\']+?)(?:\s+at\s+|\s+is\s+|\s+for\s+|,\s*|\.|\n|$)",
        corpus,
    )
    if m1:
        candidate = m1.group(1).strip()
        if 3 < len(candidate) < 90 and not candidate.lower().startswith("devfolio"):
            return candidate

    # 2. Pattern: "Organizer: ..."
    m2 = re.search(r"[Oo]rganizer\s*[:\-]\s*([A-Za-z0-9\s&,\.\-\']+?)(?:\.|\n|$)", corpus)
    if m2:
        candidate = m2.group(1).strip()
        if 3 < len(candidate) < 90:
            return candidate

    # 3. Pattern: "[Company] & [Partner] are bringing you"
    m3 = re.search(r"([A-Za-z0-9\s&\[\]\(\)\-\.\']+?)\s+are bringing you", corpus)
    if m3:
        candidate = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", m3.group(1)).strip()
        if 3 < len(candidate) < 90:
            return candidate

    return None


# ==============================================================================
# Validation & Integrity Checks
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
        if not isinstance(p_amount, (int, float)) or p_amount < 0:
            logger.warning(f"[WARNING] Invalid prize_amount '{p_amount}' for {record.get('source_url')}")
            record["prize_amount"] = None

    # 5. Tags validation
    tags = record.get("tags")
    if not isinstance(tags, list):
        record["tags"] = []
    else:
        record["tags"] = [
            str(t).strip()
            for t in tags
            if t is not None and str(t).strip() and str(t).strip().lower() != "none"
        ]

    return record


def log_scraped_hackathon(rec: Dict[str, Any]) -> None:
    """Log structured information visible on each hackathon's detail page."""
    team_str = (
        f"{rec.get('team_size_min')} - {rec.get('team_size_max')}"
        if rec.get("team_size_min") is not None or rec.get("team_size_max") is not None
        else "N/A"
    )
    tags_str = ", ".join(rec.get("tags", [])) if rec.get("tags") else "[]"
    
    logger.info(
        f"\n[SCRAPED]\n"
        f"Title: {rec.get('title')}\n"
        f"Organizer: {rec.get('organizer') or 'N/A'}\n"
        f"Status: {rec.get('status') or 'N/A'}\n"
        f"Location: {rec.get('location') or 'N/A'}\n"
        f"Event Mode: {rec.get('event_mode') or 'N/A'}\n"
        f"Start Date: {rec.get('start_date') or 'N/A'}\n"
        f"End Date: {rec.get('end_date') or 'N/A'}\n"
        f"Registration Deadline: {rec.get('registration_deadline') or 'N/A'}\n"
        f"Team Size: {team_str}\n"
        f"Prize: {rec.get('prize') or 'N/A'}\n"
        f"Prize Amount: {rec.get('prize_amount') if rec.get('prize_amount') is not None else 'N/A'}\n"
        f"Currency: {rec.get('currency') or 'N/A'}\n"
        f"Tags: {tags_str}\n"
        f"Source URL: {rec.get('source_url')}"
    )


# ==============================================================================
# Supabase Integration
# ==============================================================================

def get_supabase_client() -> Optional[Any]:
    """Initialize and return Supabase Client if credentials are configured."""
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()
    if not url or not key or "your-project" in url or "your-supabase-key" in key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as e:
        logger.warning(f"[SUPABASE] Could not initialize Supabase client: {e}")
        return None


def format_record_for_supabase(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Map extracted hackathon fields to Supabase columns with legacy aliases."""
    team_size_str = None
    if rec.get("team_size_min") is not None and rec.get("team_size_max") is not None:
        team_size_str = f"{rec['team_size_min']}-{rec['team_size_max']}"
    elif rec.get("team_size_min") is not None:
        team_size_str = str(rec["team_size_min"])

    return {
        # Core structured fields
        "title": rec.get("title"),
        "organizer": rec.get("organizer"),
        "status": rec.get("status"),
        "location": rec.get("location"),
        "event_mode": rec.get("event_mode"),
        "start_date": rec.get("start_date"),
        "end_date": rec.get("end_date"),
        "registration_deadline": rec.get("registration_deadline"),
        "team_size_min": rec.get("team_size_min"),
        "team_size_max": rec.get("team_size_max"),
        "prize": rec.get("prize"),
        "prize_amount": rec.get("prize_amount"),
        "currency": rec.get("currency"),
        "tags": rec.get("tags") or [],
        # Metadata
        "source_url": rec.get("source_url"),
        "source_website": rec.get("source_website"),
        "scraped_at": rec.get("scraped_at"),
        # Backward-compatibility aliases for legacy table schemas
        "name": rec.get("title"),
        "event_start_date": rec.get("start_date"),
        "event_end_date": rec.get("end_date"),
        "location_mode": rec.get("event_mode"),
        "team_size": team_size_str,
        "registration_link": rec.get("source_url"),
    }


def upsert_hackathons_to_supabase(
    records: List[Dict[str, Any]], table_name: Optional[str] = None
) -> bool:
    """Upsert validated records into Supabase using source_url for deduplication."""
    target_table = table_name or os.getenv("SUPABASE_TABLE", "hackathons")
    client = get_supabase_client()
    if not client:
        logger.info(
            f"[SUPABASE] Notice: SUPABASE_URL and SUPABASE_KEY not configured or incomplete in .env. "
            f"All {len(records)} records are safely saved locally to JSON and CSV."
        )
        return False

    # Deduplicate in-memory by source_url
    dedup_map = {}
    for r in records:
        key = r.get("source_url")
        if key:
            dedup_map[key] = format_record_for_supabase(r)

    unique_records = list(dedup_map.values())
    if not unique_records:
        logger.warning("[SUPABASE] No records with valid source_url to sync.")
        return False

    logger.info(
        f"[SUPABASE] Upserting {len(unique_records)} unique records to table '{target_table}'..."
    )

    try:
        response = client.table(target_table).upsert(
            unique_records, on_conflict="source_url"
        ).execute()

        inserted_count = len(response.data) if response.data else len(unique_records)
        logger.info(
            f"[SUPABASE] Inserted/Updated: {inserted_count} records into table '{target_table}'."
        )
        if response.data:
            for item in response.data[:5]:
                rec_id = item.get("id", "N/A")
                rec_title = item.get("title") or item.get("name", "N/A")
                logger.info(f"[SUPABASE] Record ID: {rec_id} | Title: {rec_title}")
        return True

    except Exception as exc:
        err_str = str(exc)
        logger.warning(f"[SUPABASE] Sync Warning: {err_str}")

        # Check for schema cache discrepancy (PGRST204)
        if "PGRST204" in err_str or "Could not find the" in err_str or "column" in err_str.lower():
            logger.warning(
                "\n  [ACTION REQUIRED] Missing columns detected in Supabase table. "
                "Please run 'supabase_migration.sql' in your Supabase SQL Editor.\n"
            )
            # Attempt fallback with legacy columns
            legacy_cols = [
                "name", "organizer", "location_mode", "event_start_date",
                "event_end_date", "registration_deadline", "team_size",
                "prize", "source_url", "registration_link"
            ]
            fallback_records = []
            for u in unique_records:
                fb = {k: v for k, v in u.items() if k in legacy_cols}
                fallback_records.append(fb)

            try:
                fb_res = client.table(target_table).upsert(
                    fallback_records, on_conflict="source_url"
                ).execute()
                logger.info(
                    f"[SUPABASE] Fallback upsert successful: {len(fallback_records)} records synced using base schema."
                )
                return True
            except Exception as fb_err:
                logger.error(f"[SUPABASE] Fallback sync failed: {fb_err}")

        elif "getaddrinfo" in err_str or "ConnectError" in err_str:
            logger.warning(
                "[SUPABASE] Connection Error: Project domain could not be resolved. "
                "If your Supabase free-tier project is paused due to inactivity, please unpause it "
                "in the Supabase Dashboard (https://supabase.com/dashboard). "
                "Records remain safely preserved locally."
            )

        return False


# ==============================================================================
# Main Scraper Class
# ==============================================================================

class DevfolioScraper:
    """Scrapes hackathons from Devfolio using Scrapling and extracts structured fields."""

    def __init__(
        self,
        fetcher_type: str = "fetcher",
        timeout: int = 30,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
    ):
        self.fetcher_type = fetcher_type
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

    def fetch_url(self, url: str) -> Optional[Any]:
        """Fetch a URL with retries and exponential backoff to handle transient 502/503s."""
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(f"Fetching {url} (Attempt {attempt}/{self.max_retries})...")
                if self.fetcher_type == "stealthy":
                    from scrapling import StealthyFetcher
                    response = StealthyFetcher.fetch(url, headless=True)
                else:
                    response = Fetcher.get(url, timeout=self.timeout)

                if response.status == 200:
                    return response
                logger.warning(
                    f"Received HTTP {response.status} from {url}. Retrying..."
                )
            except Exception as exc:
                logger.warning(f"Error fetching {url}: {exc}. Retrying...")

            if attempt < self.max_retries:
                sleep_sec = self.backoff_factor**attempt
                time.sleep(sleep_sec)

        logger.error(f"Failed to fetch {url} after {self.max_retries} attempts.")
        return None

    def parse_card(self, card: Any) -> Dict[str, Any]:
        """Extract initial structured fields from a single CompactHackathonCard element."""
        # 1. Title
        title = card.css("h3::text").get("").strip()

        # 2. Main Devfolio Subdomain URL
        main_url = card.css("a[class*='LinkBase']::attr(href)").get("").strip()
        if not main_url:
            main_url = card.css("a::attr(href)").get("").strip()

        # 3. Section / Category from ancestor section heading
        section = card.xpath("ancestor::section[1]//h2/text()").get("")
        if section:
            section = section.strip()
        else:
            section = "Featured Hero"

        # 4. Hackathon type / subtitle (e.g. "Hackathon")
        event_type = card.xpath(
            ".//p[contains(concat(' ', normalize-space(@class), ' '), ' bqaAZB ')]/text()"
        ).get("")
        if not event_type:
            event_type = card.xpath(".//h3/parent::*/following-sibling::p/text()").get("")
        event_type = event_type.strip() if event_type else "Hackathon"

        # 5. Themes / Domains
        themes = card.xpath(
            ".//p[contains(text(), 'Theme')]/following-sibling::div//p/text()"
        ).getall()
        themes = [t.strip() for t in themes if t.strip()]

        # 6. Badges (e.g. Modality, Status, Dates)
        badges = card.xpath(".//div[contains(@class, 'kvhgSq')]//p/text()").getall()
        badges = [b.strip() for b in badges if b.strip()]

        modality = ""
        status = ""
        timeline = ""
        for badge in badges:
            b_lower = badge.lower()
            if b_lower in ("online", "offline", "hybrid"):
                modality = badge
            elif b_lower in ("open", "upcoming", "live", "ended"):
                status = badge
            elif "start" in b_lower or "open" in b_lower or "/" in b_lower:
                timeline = badge

        # 7. Participants count
        participants = card.xpath(
            ".//p[contains(text(), 'participat')]/text()"
        ).get("")
        participants = participants.strip() if participants else ""

        # 8. External Links
        ext_anchors = card.xpath(".//a[not(contains(@class, 'LinkBase'))]/@href").getall()
        external_links = {}
        for link in ext_anchors:
            link_clean = link.strip()
            if link_clean and link_clean != main_url:
                l_type = identify_link_type(link_clean)
                external_links[l_type] = link_clean

        # 9. CTA button action text
        button_texts = [
            t.strip()
            for b in card.css("button")
            for t in b.css("*::text").getall()
            if t.strip() and not t.strip().isdigit()
        ]
        cta_text = button_texts[-1] if button_texts else ""

        # 10. Cover image
        cover_image = extract_cover_image(card)

        return {
            "title": title,
            "url": main_url,
            "section": section,
            "type": event_type,
            "modality": modality or "Not Specified",
            "status": status or "Not Specified",
            "timeline": timeline,
            "themes": themes,
            "participants": participants,
            "cover_image": cover_image,
            "cta_action": cta_text,
            "external_links": external_links,
            "badges": badges,
        }

    def scrape_detail_page(
        self, hackathon_url: str, card_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Extract ALL structured fields visible on an individual hackathon detail page.

        Extracts:
          title, organizer, status, location, event_mode, start_date, end_date,
          registration_deadline, team_size_min, team_size_max, prize, prize_amount,
          currency, tags, source_url, source_website, scraped_at.

        Uses embedded Next.js JSON-state as primary extraction with semantic
        DOM text & label fallbacks.
        """
        card = card_data or {}
        scraped_at = datetime.now(timezone.utc).isoformat()
        domain = urlparse(hackathon_url).netloc.lower() if hackathon_url else "devfolio.co"

        # Defaults matching the 17 requested fields
        structured: Dict[str, Any] = {
            "title": card.get("title") or None,
            "organizer": None,
            "status": card.get("status") if card.get("status") != "Not Specified" else None,
            "location": None,
            "event_mode": card.get("modality") if card.get("modality") != "Not Specified" else None,
            "start_date": None,
            "end_date": None,
            "registration_deadline": None,
            "team_size_min": None,
            "team_size_max": None,
            "prize": None,
            "prize_amount": None,
            "currency": None,
            "tags": clean_tags(card.get("themes", [])),
            "source_url": hackathon_url,
            "source_website": domain,
            "scraped_at": scraped_at,
        }

        # Preserved legacy detail sub-dictionary for existing consumers
        legacy_details: Dict[str, Any] = {
            "runs_from": "",
            "happening": "",
            "applications_close": "",
            "prize_pool": "",
            "overview": "",
            "sponsors": [],
            "logo_url": "",
        }

        if not hackathon_url or not hackathon_url.startswith("http"):
            structured["details"] = legacy_details
            return structured

        res = self.fetch_url(hackathon_url)
        if not res:
            logger.warning(f"[WARNING] Detail page could not be fetched for {hackathon_url}")
            structured["details"] = legacy_details
            return structured

        try:
            # ------------------------------------------------------------------
            # 1. Inspect Embedded Next.js Data (__NEXT_DATA__)
            # ------------------------------------------------------------------
            next_data_script = res.css("script#__NEXT_DATA__::text").get("")
            props: Dict[str, Any] = {}
            h_data: Dict[str, Any] = {}
            if next_data_script:
                try:
                    data = json.loads(next_data_script)
                    props = data.get("props", {}).get("pageProps", {})
                    h_data = props.get("hackathon", {})
                except Exception as e:
                    logger.debug(f"Could not parse __NEXT_DATA__ from {hackathon_url}: {e}")

            # ------------------------------------------------------------------
            # 2. Inspect Semantic DOM Text Elements (Fallbacks)
            # ------------------------------------------------------------------
            sidebar_texts = [
                t.strip()
                for t in res.css("[class*='InfoUl'] *::text, [class*='HackathonCard'] *::text").getall()
                if t.strip()
            ]
            all_page_texts = [t.strip() for t in res.css("*::text").getall() if t.strip()]

            runs_from_dom = ""
            happening_dom = ""
            applications_close_dom = ""
            for i, text in enumerate(sidebar_texts):
                t_lower = text.lower()
                if "runs from" in t_lower and i + 1 < len(sidebar_texts):
                    runs_from_dom = sidebar_texts[i + 1]
                elif "happening" in t_lower and i + 1 < len(sidebar_texts):
                    happening_dom = sidebar_texts[i + 1]
                elif "close" in t_lower and i + 1 < len(sidebar_texts):
                    applications_close_dom = sidebar_texts[i + 1]

            # Team size from FAQ / DOM
            team_size_dom = ""
            for i, text in enumerate(all_page_texts):
                if text.lower() == "team size" and i + 1 < len(all_page_texts):
                    team_size_dom = all_page_texts[i + 1]
                    break

            # Logo & Cover image
            logo = res.css("[class*='HackathonLogo'] img::attr(src), header img::attr(src)").get("")
            if logo:
                if "_next/image?url=" in logo:
                    parsed = urlparse(logo)
                    qs = parse_qs(parsed.query)
                    legacy_details["logo_url"] = unquote(qs.get("url", [""])[0])
                else:
                    legacy_details["logo_url"] = logo

            # Prize pool DOM string
            prize_texts = [
                t.strip()
                for t in res.css("*::text").getall()
                if re.search(r"(\$|₹|INR|USD|€|EUR|£|GBP)\s*[\d,]+", t)
            ]
            if prize_texts:
                legacy_details["prize_pool"] = prize_texts[0]

            # Overview / Description paragraphs
            desc_paragraphs = res.css(
                "[class*='Overview__MainDesktopGridItem'] [class*='Overview__StyledDescriptionCard'] p::text, "
                "[class*='Overview__StyledDescriptionCard'] p::text"
            ).getall()
            seen_p = set()
            clean_paragraphs = []
            for p in desc_paragraphs:
                p_clean = p.strip()
                if p_clean and p_clean not in seen_p and len(p_clean) > 3:
                    seen_p.add(p_clean)
                    clean_paragraphs.append(p_clean)

            desc_text = h_data.get("desc") or "\n\n".join(clean_paragraphs)
            legacy_details["overview"] = desc_text
            legacy_details["runs_from"] = runs_from_dom
            legacy_details["happening"] = happening_dom
            legacy_details["applications_close"] = applications_close_dom

            # Sponsors
            raw_sponsors = res.css("[class*='Sponsors'] img::attr(alt)").getall()
            legacy_details["sponsors"] = list(dict.fromkeys(s.strip() for s in raw_sponsors if s.strip()))

            # ------------------------------------------------------------------
            # 3. Field Normalization & Merging
            # ------------------------------------------------------------------

            # A. Title
            title = h_data.get("name") or res.css("h1::text").get("").strip() or card.get("title")
            structured["title"] = title.strip() if title else None

            # B. Dates: start_date, end_date
            sd, ed = None, None
            if h_data.get("starts_at"):
                sd = parse_single_date(h_data.get("starts_at"))
            if h_data.get("ends_at"):
                ed = parse_single_date(h_data.get("ends_at"))

            if not sd or not ed:
                dom_sd, dom_ed = parse_date_range(runs_from_dom)
                sd = sd or dom_sd
                ed = ed or dom_ed

            if not sd and card.get("timeline"):
                sd = parse_single_date(card.get("timeline"))

            structured["start_date"] = sd
            structured["end_date"] = ed

            # C. Registration Deadline
            reg_deadline = None
            if h_data.get("settings", {}).get("reg_ends_at"):
                reg_deadline = parse_single_date(h_data["settings"]["reg_ends_at"])
            if not reg_deadline and applications_close_dom:
                reg_deadline = parse_single_date(applications_close_dom)

            structured["registration_deadline"] = reg_deadline

            # D. Team Size: team_size_min, team_size_max
            t_min = h_data.get("team_min")
            t_max = h_data.get("team_max")
            if t_min is None or t_max is None:
                dom_t_min, dom_t_max = parse_team_size(team_size_dom)
                t_min = t_min if t_min is not None else dom_t_min
                t_max = t_max if t_max is not None else dom_t_max

            structured["team_size_min"] = t_min
            structured["team_size_max"] = t_max

            # E. Location & Event Mode
            is_online = h_data.get("is_online")
            is_hybrid = h_data.get("settings", {}).get("is_hybrid")
            raw_loc = h_data.get("location") or happening_dom
            card_modality = card.get("modality") if card.get("modality") != "Not Specified" else None

            loc, mode = parse_location_and_mode(raw_loc, card_modality, is_online, is_hybrid)
            structured["location"] = loc
            structured["event_mode"] = mode

            # F. Prize, Prize Amount, Currency
            raw_prize_str = legacy_details.get("prize_pool")
            agg_val = props.get("aggregatePrizeValue")
            agg_curr = props.get("aggregatePrizeCurrency")

            parsed_prize, parsed_amt, parsed_curr = parse_prize(raw_prize_str)

            # If Next.js provides structured prize numbers, use them with priority
            final_prize_str = parsed_prize or (f"{agg_curr} {agg_val}" if agg_val else None)
            final_amt = float(agg_val) if agg_val is not None else parsed_amt
            final_curr = agg_curr or parsed_curr

            structured["prize"] = final_prize_str
            structured["prize_amount"] = final_amt
            structured["currency"] = final_curr

            # G. Organizer
            org = None
            if h_data.get("brand") and isinstance(h_data["brand"], dict) and h_data["brand"].get("name"):
                org = h_data["brand"]["name"].strip()
            if not org:
                prize_details = props.get("prizeDetails", [])
                if prize_details and isinstance(prize_details, list):
                    first_pd = prize_details[0]
                    if first_pd.get("type") == "organizer" and first_pd.get("name") != structured["title"]:
                        org = first_pd.get("name")
            if not org:
                org = extract_organizer_name(desc_text, all_page_texts)

            structured["organizer"] = org

            # H. Tags
            raw_themes = []
            if h_data.get("themes") and isinstance(h_data["themes"], list):
                for th in h_data["themes"]:
                    if isinstance(th, dict) and "theme" in th and "name" in th["theme"]:
                        raw_themes.append(th["theme"]["name"])
                    elif isinstance(th, str):
                        raw_themes.append(th)
            if not raw_themes:
                raw_themes = card.get("themes", [])

            structured["tags"] = clean_tags(raw_themes)

            # I. Status
            status_val = card.get("status") if card.get("status") != "Not Specified" else None
            if not status_val:
                # Check application close string
                if applications_close_dom:
                    if "ended" in applications_close_dom.lower() or "closed" in applications_close_dom.lower():
                        status_val = "Ended"
                    elif "apply" in applications_close_dom.lower() or "open" in applications_close_dom.lower():
                        status_val = "Open"
            structured["status"] = status_val

            structured["details"] = legacy_details

        except Exception as err:
            logger.warning(f"[WARNING] Error parsing detail page {hackathon_url}: {err}")
            structured["details"] = legacy_details

        # Validate and return
        validated = validate_record(structured)
        return validated

    def scrape(
        self,
        url: str = "https://devfolio.co/hackathons",
        deep: bool = True,
        deep_limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Main scraping pipeline.

        1. Discovers hackathons from listing cards
        2. Fetches detail pages when deep=True
        3. Normalizes and validates all structured fields
        4. Logs structured information for each hackathon
        """
        response = self.fetch_url(url)
        if not response:
            logger.error(f"Failed to fetch content from {url}.")
            return []

        cards = response.css("[class*='CompactHackathonCard']")
        logger.info(f"Found {len(cards)} hackathon card elements on {url}.")

        hackathons = []
        for idx, card in enumerate(cards, start=1):
            data = self.parse_card(card)
            logger.info(
                f"[{idx}/{len(cards)}] Discovered: {data['title']} ({data['section']}) -> {data['url']}"
            )
            hackathons.append(data)

        if deep and hackathons:
            limit = deep_limit if deep_limit else len(hackathons)
            logger.info(
                f"Deep scrape enabled: Scraping detail pages for {limit} hackathon(s)..."
            )
            detailed_hackathons = []
            for idx, item in enumerate(hackathons[:limit], start=1):
                logger.info(f"Deep scraping [{idx}/{limit}]: {item['title']}...")
                detail_rec = self.scrape_detail_page(item["url"], card_data=item)

                # Merge card fields for complete coverage
                merged = {**item, **detail_rec}
                # Ensure the 17 root structured fields are preserved
                for fld in (
                    "title", "organizer", "status", "location", "event_mode",
                    "start_date", "end_date", "registration_deadline",
                    "team_size_min", "team_size_max", "prize", "prize_amount",
                    "currency", "tags", "source_url", "source_website", "scraped_at"
                ):
                    merged[fld] = detail_rec.get(fld)

                log_scraped_hackathon(merged)
                detailed_hackathons.append(merged)
                time.sleep(1)  # Respectful pause between detail requests

            # If limited, append remaining un-deep scraped items
            if limit < len(hackathons):
                detailed_hackathons.extend(hackathons[limit:])

            return detailed_hackathons

        return hackathons

    @staticmethod
    def export_json(hackathons: List[Dict[str, Any]], filepath: str) -> None:
        """Export list of hackathons to a formatted JSON file."""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(hackathons, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved {len(hackathons)} hackathons to {filepath}")

    @staticmethod
    def export_csv(hackathons: List[Dict[str, Any]], filepath: str) -> None:
        """Export list of hackathons to a CSV file."""
        if not hackathons:
            logger.warning("No hackathon data to export to CSV.")
            return

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
            # Legacy card & detail fields
            "url",
            "section",
            "type",
            "modality",
            "timeline",
            "themes",
            "participants",
            "cover_image",
            "cta_action",
            "external_links",
            "badges",
            "runs_from",
            "happening",
            "applications_close",
            "prize_pool",
            "overview",
            "sponsors",
            "logo_url",
        ]

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for h in hackathons:
                details = h.get("details", {})
                row = {
                    "title": h.get("title", ""),
                    "organizer": h.get("organizer", ""),
                    "status": h.get("status", ""),
                    "location": h.get("location", ""),
                    "event_mode": h.get("event_mode", ""),
                    "start_date": h.get("start_date", ""),
                    "end_date": h.get("end_date", ""),
                    "registration_deadline": h.get("registration_deadline", ""),
                    "team_size_min": h.get("team_size_min", ""),
                    "team_size_max": h.get("team_size_max", ""),
                    "prize": h.get("prize", ""),
                    "prize_amount": h.get("prize_amount", ""),
                    "currency": h.get("currency", ""),
                    "tags": ", ".join(h.get("tags", [])),
                    "source_url": h.get("source_url") or h.get("url", ""),
                    "source_website": h.get("source_website", ""),
                    "scraped_at": h.get("scraped_at", ""),
                    "url": h.get("url", ""),
                    "section": h.get("section", ""),
                    "type": h.get("type", ""),
                    "modality": h.get("modality", ""),
                    "timeline": h.get("timeline", ""),
                    "themes": ", ".join(h.get("themes", [])),
                    "participants": h.get("participants", ""),
                    "cover_image": h.get("cover_image", ""),
                    "cta_action": h.get("cta_action", ""),
                    "external_links": json.dumps(h.get("external_links", {})),
                    "badges": ", ".join(h.get("badges", [])),
                    "runs_from": details.get("runs_from", ""),
                    "happening": details.get("happening", ""),
                    "applications_close": details.get("applications_close", ""),
                    "prize_pool": details.get("prize_pool", ""),
                    "overview": details.get("overview", ""),
                    "sponsors": ", ".join(details.get("sponsors", [])),
                    "logo_url": details.get("logo_url", ""),
                }
                writer.writerow(row)
        logger.info(f"Saved {len(hackathons)} hackathons to {filepath}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape Devfolio Hackathon listings and structured details using Scrapling."
    )
    parser.add_argument(
        "--url",
        default="https://devfolio.co/hackathons",
        help="Target Devfolio URL (default: https://devfolio.co/hackathons)",
    )
    parser.add_argument(
        "--output",
        default="hackathons",
        help="Base name for output files without extension (default: hackathons)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv", "all"],
        default="all",
        help="Export format (json, csv, all; default: all)",
    )
    parser.add_argument(
        "--deep",
        dest="deep",
        action="store_true",
        default=True,
        help="Scrape individual hackathon microsites for in-depth details (default: True)",
    )
    parser.add_argument(
        "--no-deep",
        dest="deep",
        action="store_false",
        help="Skip detail page scraping and only extract listing cards",
    )
    parser.add_argument(
        "--deep-limit",
        type=int,
        default=None,
        help="Limit number of hackathons for deep detail scraping (e.g. --deep-limit 3)",
    )
    parser.add_argument(
        "--fetcher",
        choices=["fetcher", "stealthy"],
        default="fetcher",
        help="Fetcher type to use: 'fetcher' (fast curl_cffi) or 'stealthy' (browser-based)",
    )
    parser.add_argument(
        "--supabase",
        dest="sync_supabase",
        action="store_true",
        default=True,
        help="Sync validated records to Supabase database (default: True)",
    )
    parser.add_argument(
        "--no-supabase",
        dest="sync_supabase",
        action="store_false",
        help="Skip syncing records to Supabase database",
    )
    parser.add_argument(
        "--table",
        default=None,
        help="Target Supabase table name (default: 'hackathons')",
    )

    args = parser.parse_args()

    scraper = DevfolioScraper(fetcher_type=args.fetcher)
    hackathons = scraper.scrape(
        url=args.url,
        deep=args.deep,
        deep_limit=args.deep_limit,
    )

    if not hackathons:
        logger.warning("No hackathon details found.")
        sys.exit(1)

    # 1. Local Exports
    if args.format in ("json", "all"):
        json_path = f"{args.output}.json"
        scraper.export_json(hackathons, json_path)

    if args.format in ("csv", "all"):
        csv_path = f"{args.output}.csv"
        scraper.export_csv(hackathons, csv_path)

    # 2. Supabase Sync
    if args.sync_supabase:
        upsert_hackathons_to_supabase(hackathons, table_name=args.table)

    print(f"\n[DONE] Successfully scraped {len(hackathons)} hackathons from Devfolio!")


if __name__ == "__main__":
    main()
