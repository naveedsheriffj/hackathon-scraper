"""
Unit test suite for Unstop Hackathon Scraper.
Tests date normalization, team size parsing, prize extraction,
location & event mode disambiguation, tag cleaning, validation, and null handling.
Ensures zero hallucinations and strict data integrity.
"""

import unittest
from scraper import (
    parse_single_date,
    parse_date_range,
    parse_team_size,
    parse_prize,
    normalize_location_and_mode,
    clean_tags,
    normalize_status,
    validate_record,
)


class TestDateParsing(unittest.TestCase):
    def test_single_dates(self):
        self.assertEqual(parse_single_date("2 Nov 2026"), "2026-11-02")
        self.assertEqual(parse_single_date("2 November 2026"), "2026-11-02")
        self.assertEqual(parse_single_date("15th September 2026"), "2026-09-15")
        self.assertEqual(parse_single_date("2026-08-17T16:00:00+05:30"), "2026-08-17")
        self.assertEqual(parse_single_date("2026-11-02"), "2026-11-02")
        self.assertEqual(parse_single_date("Registration closes: 28 Oct 2026"), "2026-10-28")
        self.assertEqual(parse_single_date("Deadline: 15-09-2026"), "2026-09-15")

    def test_date_ranges(self):
        # En dash separator
        s, e = parse_date_range("2 Nov 2026 – 3 Nov 2026")
        self.assertEqual(s, "2026-11-02")
        self.assertEqual(e, "2026-11-03")

        # Hyphen separator
        s, e = parse_date_range("2 Nov 2026 - 3 Nov 2026")
        self.assertEqual(s, "2026-11-02")
        self.assertEqual(e, "2026-11-03")

        # Same month shorthand: '26–27 September 2026'
        s, e = parse_date_range("26–27 September 2026")
        self.assertEqual(s, "2026-09-26")
        self.assertEqual(e, "2026-09-27")

        # Single date in range parser
        s, e = parse_date_range("2 Nov 2026")
        self.assertEqual(s, "2026-11-02")
        self.assertIsNone(e)

    def test_invalid_and_missing_dates(self):
        self.assertIsNone(parse_single_date(None))
        self.assertIsNone(parse_single_date(""))
        self.assertIsNone(parse_single_date("TBA"))
        self.assertIsNone(parse_single_date("To be announced"))
        s, e = parse_date_range(None)
        self.assertIsNone(s)
        self.assertIsNone(e)


class TestTeamSizeParsing(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(parse_team_size("1-3"), (1, 3))
        self.assertEqual(parse_team_size("2–5"), (2, 5))
        self.assertEqual(parse_team_size("1 to 4"), (1, 4))
        self.assertEqual(parse_team_size("1 - 5 per team"), (1, 5))

    def test_single_number(self):
        self.assertEqual(parse_team_size("1"), (1, 1))
        self.assertEqual(parse_team_size(1), (1, 1))
        self.assertEqual(parse_team_size("teams of 4 members"), (4, 4))

    def test_dictionary_input(self):
        self.assertEqual(parse_team_size({"min": 1, "max": 5}), (1, 5))
        self.assertEqual(parse_team_size({"min_team_size": 2, "max_team_size": 4}), (2, 4))

    def test_missing_team_size(self):
        self.assertEqual(parse_team_size(None), (None, None))
        self.assertEqual(parse_team_size(""), (None, None))
        self.assertEqual(parse_team_size("Open to all"), (None, None))


class TestPrizeParsing(unittest.TestCase):
    def test_standard_currencies(self):
        txt, amt, curr = parse_prize("₹1,00,000")
        self.assertEqual(txt, "₹1,00,000")
        self.assertEqual(amt, 100000.0)
        self.assertEqual(curr, "INR")

        txt, amt, curr = parse_prize("$10,000")
        self.assertEqual(amt, 10000.0)
        self.assertEqual(curr, "USD")

        txt, amt, curr = parse_prize("€5,000")
        self.assertEqual(amt, 5000.0)
        self.assertEqual(curr, "EUR")

    def test_indian_denominations(self):
        txt, amt, curr = parse_prize("Prize Pool: ₹1 Lakh")
        self.assertEqual(amt, 100000.0)
        self.assertEqual(curr, "INR")

        txt, amt, curr = parse_prize("Prize pool of Rs 1.5 Lakhs (Cash)")
        self.assertEqual(amt, 150000.0)
        self.assertEqual(curr, "INR")

    def test_non_numeric_and_missing_prizes(self):
        txt, amt, curr = parse_prize("Certificates and Goodies")
        self.assertEqual(txt, "Certificates and Goodies")
        self.assertIsNone(amt)
        self.assertIsNone(curr)

        txt, amt, curr = parse_prize(None)
        self.assertIsNone(txt)
        self.assertIsNone(amt)
        self.assertIsNone(curr)


class TestLocationAndMode(unittest.TestCase):
    def test_online(self):
        loc, mode = normalize_location_and_mode("Online (Online)", "online")
        self.assertEqual(loc, "Online")
        self.assertEqual(mode, "Online")

        loc, mode = normalize_location_and_mode("Online", None)
        self.assertEqual(loc, "Online")
        self.assertEqual(mode, "Online")

    def test_physical_location(self):
        addr = {
            "city": "Navi Mumbai",
            "state": "Maharashtra",
            "country": {"name": "India"},
        }
        loc, mode = normalize_location_and_mode(None, "offline", addr)
        self.assertEqual(loc, "Navi Mumbai, Maharashtra, India")
        self.assertEqual(mode, "Offline")

    def test_hybrid_mode(self):
        loc, mode = normalize_location_and_mode("Chennai, Tamil Nadu", "hybrid")
        self.assertEqual(loc, "Chennai, Tamil Nadu")
        self.assertEqual(mode, "Hybrid")


class TestTagsCleaning(unittest.TestCase):
    def test_deduplication_and_cleaning(self):
        raw = [
            {"skill": "Artificial Intelligence (AI)"},
            {"skill": "Artificial Intelligence (AI)"},
            {"name": "Software Development"},
            "Cloud",
            "AWS",
            "Cloud",
            "",
            None,
        ]
        cleaned = clean_tags(raw)
        self.assertEqual(cleaned, ["Artificial Intelligence (AI)", "Software Development", "Cloud", "AWS"])

    def test_empty_tags(self):
        self.assertEqual(clean_tags([]), [])
        self.assertEqual(clean_tags([None, ""]), [])


class TestStatusNormalization(unittest.TestCase):
    def test_statuses(self):
        self.assertEqual(normalize_status("LIVE", registration_status="OPEN"), "Ongoing")
        self.assertEqual(normalize_status("UPCOMING", event_status="UPCOMING"), "Upcoming")
        self.assertEqual(normalize_status("EXPIRED", registration_status="CLOSED"), "Closed")
        self.assertEqual(normalize_status(None, event_status="FINISHED"), "Completed")


class TestRecordValidation(unittest.TestCase):
    def test_valid_record(self):
        rec = {
            "title": "Cloud Computing Challenge",
            "source_url": "https://unstop.com/hackathons/cloud-challenge-12345",
            "start_date": "2026-11-02",
            "end_date": "2026-11-03",
            "registration_deadline": "2026-10-28",
            "team_size_min": 1,
            "team_size_max": 3,
            "prize_amount": 100000.0,
            "currency": "INR",
            "tags": ["Cloud", "AWS"],
        }
        val = validate_record(rec)
        self.assertIsNotNone(val)
        self.assertEqual(val["title"], "Cloud Computing Challenge")

    def test_invalid_title_dropped(self):
        rec = {
            "title": "",
            "source_url": "https://unstop.com/hackathons/test",
        }
        self.assertIsNone(validate_record(rec))

    def test_invalid_dates_nullified_without_error(self):
        rec = {
            "title": "Test Hackathon",
            "source_url": "https://unstop.com/hackathons/test",
            "start_date": "invalid-date",
            "end_date": "2026-11-03",
            "registration_deadline": "bad-deadline",
        }
        val = validate_record(rec)
        self.assertIsNotNone(val)
        self.assertIsNone(val["start_date"])
        self.assertIsNone(val["registration_deadline"])
        self.assertEqual(val["end_date"], "2026-11-03")


if __name__ == "__main__":
    unittest.main()
