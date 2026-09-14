"""Unit tests for HackerEarth scraper behavior.

Covers:
- Successful extraction from public upcoming events API
- Strict zero-fabrication for missing optional fields
- HTTP 403 Forbidden handling (proper exception vs. zero-result or sys.exit)
- Legitimate zero-result scrape returning an empty list
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from scrapers.HackerEarth.scraper import (
    clean_tags,
    clean_text,
    determine_status,
    normalize_url,
    parse_date_range,
    parse_location_and_mode,
    parse_prize,
    parse_single_date,
    parse_team_size,
    scrape_challenges,
    validate_record,
)


class TestHackerEarthScraper(unittest.TestCase):
    """Test suite verifying HackerEarth scraper parsing, zero-fabrication, and error handling."""

    def test_determine_status_logic(self):
        """Test status determination for ongoing, upcoming, and past events."""
        # Future dates -> Upcoming
        self.assertEqual(
            determine_status("2099-01-01T00:00:00Z", "2099-01-02T00:00:00Z"),
            "Upcoming",
        )
        # Past dates -> Past
        self.assertEqual(
            determine_status("2020-01-01T00:00:00Z", "2020-01-02T00:00:00Z"),
            "Past",
        )
        # Missing dates -> None
        self.assertIsNone(determine_status(None, "2099-01-01T00:00:00Z"))
        self.assertIsNone(determine_status("2099-01-01T00:00:00Z", None))

    def test_missing_optional_fields_are_null(self):
        """Verify strict zero-fabrication: missing fields remain None / empty."""
        minimal_record = {
            "title": "Minimal Challenge",
            "source_url": "https://www.hackerearth.com/challenges/hackathon/minimal-test/",
            "source_website": "hackerearth.com",
            "status": "Upcoming",
            "start_date": "2026-11-01",
            "end_date": "2026-11-02",
            "registration_deadline": None,
            "organizer": None,
            "location": None,
            "event_mode": None,
            "team_size_min": None,
            "team_size_max": None,
            "prize": None,
            "prize_amount": None,
            "currency": None,
            "tags": [],
            "scraped_at": "2026-09-14T00:00:00Z",
        }
        validated = validate_record(minimal_record)
        self.assertIsNone(validated["organizer"])
        self.assertIsNone(validated["prize"])
        self.assertIsNone(validated["prize_amount"])
        self.assertIsNone(validated["currency"])
        self.assertIsNone(validated["location"])
        self.assertIsNone(validated["event_mode"])
        self.assertEqual(validated["tags"], [])

    def test_prize_parsing_zero_fabrication(self):
        """Test that non-monetary or missing prizes do not fabricate amounts."""
        # Non-numeric prize -> prize_amount is None
        p, amt, curr = parse_prize("Mentorship and Swags")
        self.assertEqual(p, "Mentorship and Swags")
        self.assertIsNone(amt)
        self.assertIsNone(curr)

        # Empty / None
        p, amt, curr = parse_prize(None)
        self.assertIsNone(p)
        self.assertIsNone(amt)
        self.assertIsNone(curr)

    @patch("scrapers.HackerEarth.scraper.Fetcher.get")
    def test_http_403_handling_raises_runtime_error(self, mock_fetcher_get):
        """Verify HTTP 403 is NOT treated as success(0 items) and raises RuntimeError."""
        # Mock both primary and fallback endpoints returning 403 Forbidden
        mock_response_403 = MagicMock()
        mock_response_403.status = 403
        mock_response_403.body = b"<html><title>403 Forbidden</title></html>"
        mock_fetcher_get.return_value = mock_response_403

        # Must raise RuntimeError and not call sys.exit
        with self.assertRaises(RuntimeError) as ctx:
            scrape_challenges()

        self.assertIn("403", str(ctx.exception))

    @patch("scrapers.HackerEarth.scraper.Fetcher.get")
    def test_zero_result_scrape_succeeds_without_error(self, mock_fetcher_get):
        """Verify that when endpoint cleanly returns 0 challenges, an empty list is returned."""
        mock_response_200 = MagicMock()
        mock_response_200.status = 200
        mock_response_200.body = json.dumps({"response": []}).encode("utf-8")
        mock_fetcher_get.return_value = mock_response_200

        with patch("scrapers.HackerEarth.scraper.sync_to_supabase"):
            records = scrape_challenges()

        self.assertEqual(records, [])

    @patch("scrapers.HackerEarth.scraper.Fetcher.get")
    def test_successful_extraction_from_upcoming_api(self, mock_fetcher_get):
        """Verify end-to-end extraction from public upcoming events API."""
        mock_events = [
            {
                "title": "Global AI Challenge 2026",
                "description": "Annual AI and ML competitive hackathon.",
                "url": "https://www.hackerearth.com/challenges/hackathon/global-ai-2026/",
                "status": "ONGOING",
                "date": "Aug 14, 2026",
                "end_date": "Sep 20, 2026",
                "start_tz": "2026-08-14 13:00:00+05:30",
                "end_tz": "2026-09-20 23:59:00+05:30",
                "challenge_type": "Hackathon",
            }
        ]

        def fetcher_side_effect(url, **kwargs):
            res = MagicMock()
            res.status = 200
            if "events/upcoming" in url:
                res.body = json.dumps({"response": mock_events}).encode("utf-8")
            elif "challengesapp/api/events" in url:
                res.body = json.dumps({
                    "tags": ["AI", "Machine Learning"],
                    "organizer": [{"title": "Global AI Foundation"}],
                    "min_team_size": 1,
                    "max_team_size": 4,
                }).encode("utf-8")
            else:
                res.body = b"<html><title>Global AI Challenge 2026</title></html>"
            return res

        mock_fetcher_get.side_effect = fetcher_side_effect

        with patch("scrapers.HackerEarth.scraper.sync_to_supabase"):
            records = scrape_challenges()

        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["title"], "Global AI Challenge 2026")
        self.assertEqual(rec["organizer"], "Global AI Foundation")
        self.assertEqual(rec["status"], "Ongoing")
        self.assertEqual(rec["start_date"], "2026-08-14")
        self.assertEqual(rec["end_date"], "2026-09-20")
        self.assertEqual(rec["team_size_min"], 1)
        self.assertEqual(rec["team_size_max"], 4)
        self.assertIn("AI", rec["tags"])
        self.assertEqual(rec["source_website"], "hackerearth.com")


if __name__ == "__main__":
    unittest.main()
