"""Common Hackathon Data Model.

Defines the standard 17-field data structure for hackathons across all scrapers,
along with validation logic that strictly adheres to zero-fabrication and
no silent alteration of questionable source data.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger("hackathon_scraper.models")


@dataclass
class Hackathon:
    """Standard representation of a scraped hackathon event."""

    # Core required fields
    title: str
    source_url: str

    # Optional descriptive fields
    organizer: Optional[str] = None
    status: Optional[str] = None
    location: Optional[str] = None
    event_mode: Optional[str] = None

    # Temporal fields (ISO format YYYY-MM-DD or None)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    registration_deadline: Optional[str] = None

    # Team size fields
    team_size_min: Optional[int] = None
    team_size_max: Optional[int] = None

    # Prize fields
    prize: Optional[str] = None
    prize_amount: Optional[float] = None
    currency: Optional[str] = None

    # Metadata & classification
    tags: List[str] = field(default_factory=list)
    source_website: Optional[str] = None
    scraped_at: Optional[str] = None

    def __post_init__(self):
        # Ensure scraped_at is set to current UTC if missing
        if not self.scraped_at:
            self.scraped_at = datetime.now(timezone.utc).isoformat()

        # Derive source_website from source_url if not provided
        if not self.source_website and self.source_url:
            try:
                parsed = urlparse(self.source_url)
                self.source_website = parsed.netloc.lower().replace("www.", "")
            except Exception:
                self.source_website = None

        # Clean tags to ensure list of unique strings
        if self.tags is None:
            self.tags = []
        elif isinstance(self.tags, list):
            cleaned = []
            seen = set()
            for t in self.tags:
                if t is not None:
                    t_str = str(t).strip()
                    if t_str and t_str.lower() not in seen:
                        seen.add(t_str.lower())
                        cleaned.append(t_str)
            self.tags = cleaned

    def validate(self) -> bool:
        """Validate record integrity without fabricating or silently altering questionable data.

        Returns:
            True if the record meets mandatory requirements, False if invalid and must be dropped.
        """
        # 1. Mandatory title check
        if not self.title or not str(self.title).strip():
            logger.warning("[VALIDATION] Record dropped: Missing or empty title (%s)", self.source_url)
            return False

        # 2. Mandatory URL check
        if not self.source_url or not str(self.source_url).strip():
            logger.warning("[VALIDATION] Record dropped: Missing or empty source_url for '%s'", self.title)
            return False

        parsed_url = urlparse(self.source_url)
        if not parsed_url.scheme or not parsed_url.netloc:
            logger.warning("[VALIDATION] Record dropped: Invalid source_url '%s' for '%s'", self.source_url, self.title)
            return False

        # 3. Team size consistency:
        # DO NOT swap inverted values! If team_size_min > team_size_max, log warning and set to None.
        if self.team_size_min is not None and self.team_size_max is not None:
            if self.team_size_min > self.team_size_max:
                logger.warning(
                    "[VALIDATION] Inconsistent team size for '%s' (min %s > max %s). Setting to NULL.",
                    self.title,
                    self.team_size_min,
                    self.team_size_max,
                )
                self.team_size_min = None
                self.team_size_max = None

        if self.team_size_min is not None and self.team_size_min < 1:
            logger.warning("[VALIDATION] Invalid team_size_min (%s) for '%s'. Setting to NULL.", self.team_size_min, self.title)
            self.team_size_min = None

        if self.team_size_max is not None and self.team_size_max < 1:
            logger.warning("[VALIDATION] Invalid team_size_max (%s) for '%s'. Setting to NULL.", self.team_size_max, self.title)
            self.team_size_max = None

        # 4. Date consistency:
        # If start_date > end_date, do not swap or guess; log warning and set end_date to None.
        if self.start_date and self.end_date:
            try:
                s_dt = datetime.strptime(self.start_date, "%Y-%m-%d")
                e_dt = datetime.strptime(self.end_date, "%Y-%m-%d")
                if s_dt > e_dt:
                    logger.warning(
                        "[VALIDATION] Inconsistent dates for '%s' (start %s > end %s). Setting end_date to NULL.",
                        self.title,
                        self.start_date,
                        self.end_date,
                    )
                    self.end_date = None
            except ValueError:
                pass

        # 5. Prize amount non-negativity
        if self.prize_amount is not None:
            if self.prize_amount < 0:
                logger.warning(
                    "[VALIDATION] Negative prize_amount (%s) for '%s'. Setting to NULL.",
                    self.prize_amount,
                    self.title,
                )
                self.prize_amount = None

        return True

    def to_dict(self) -> Dict[str, Any]:
        """Return clean dictionary containing the 17 core fields."""
        return {
            "title": self.title,
            "organizer": self.organizer,
            "status": self.status,
            "location": self.location,
            "event_mode": self.event_mode,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "registration_deadline": self.registration_deadline,
            "team_size_min": self.team_size_min,
            "team_size_max": self.team_size_max,
            "prize": self.prize,
            "prize_amount": self.prize_amount,
            "currency": self.currency,
            "tags": list(self.tags),
            "source_url": self.source_url,
            "source_website": self.source_website,
            "scraped_at": self.scraped_at,
        }

    def to_supabase_dict(self) -> Dict[str, Any]:
        """Return dictionary formatted for Supabase upsert, including schema backward-compatibility fields."""
        d = self.to_dict()
        # Backward-compatibility columns
        d["name"] = self.title
        d["event_start_date"] = self.start_date
        d["event_end_date"] = self.end_date
        d["location_mode"] = self.event_mode
        d["registration_link"] = self.source_url
        if self.team_size_min is not None and self.team_size_max is not None:
            d["team_size"] = f"{self.team_size_min}-{self.team_size_max}"
        elif self.team_size_min is not None:
            d["team_size"] = str(self.team_size_min)
        else:
            d["team_size"] = None
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Hackathon":
        """Create Hackathon instance from dictionary, ignoring extra unrecognized keys."""
        known_keys = {
            "title", "source_url", "organizer", "status", "location",
            "event_mode", "start_date", "end_date", "registration_deadline",
            "team_size_min", "team_size_max", "prize", "prize_amount",
            "currency", "tags", "source_website", "scraped_at",
        }
        filtered = {k: v for k, v in data.items() if k in known_keys}
        # Fallback for title/url if legacy keys present
        if "title" not in filtered and "name" in data:
            filtered["title"] = data["name"]
        if "source_url" not in filtered and "url" in data:
            filtered["source_url"] = data["url"]
        return cls(**filtered)
