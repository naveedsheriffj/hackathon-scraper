"""
Unit tests for HackerEarth Scraper parsing, normalization, validation, and deduplication logic.
"""

import unittest
from datetime import datetime
from scraper import (
    parse_single_date,
    parse_date_range,
    parse_team_size,
    parse_prize,
    parse_location_and_mode,
    clean_tags,
    extract_domain,
    validate_record,
)


class TestScraperParsing(unittest.TestCase):

    def test_date_parsing_examples_from_prompt(self):
        # "2 Nov 2026 – 3 Nov 2026"
        sd, ed = parse_date_range("2 Nov 2026 – 3 Nov 2026")
        self.assertEqual(sd, "2026-11-02")
        self.assertEqual(ed, "2026-11-03")

        # "2 Nov 2026 - 3 Nov 2026" (normal hyphen)
        sd, ed = parse_date_range("2 Nov 2026 - 3 Nov 2026")
        self.assertEqual(sd, "2026-11-02")
        self.assertEqual(ed, "2026-11-03")

        # "2 Nov 2026" -> start_date = "2026-11-02", end_date = null
        sd, ed = parse_date_range("2 Nov 2026")
        self.assertEqual(sd, "2026-11-02")
        self.assertIsNone(ed)

        # "Registration closes: 28 Oct 2026"
        d = parse_single_date("Registration closes: 28 Oct 2026")
        self.assertEqual(d, "2026-10-28")

        # Unparseable date returns None (no guessing)
        self.assertIsNone(parse_single_date("Upcoming Soon TBD"))
        self.assertIsNone(parse_single_date(None))

    def test_team_size_examples_from_prompt(self):
        # "1-3" -> team_size_min = 1, team_size_max = 3
        tmin, tmax = parse_team_size("1-3")
        self.assertEqual((tmin, tmax), (1, 3))

        # "2–5" -> team_size_min = 2, team_size_max = 5 (en dash)
        tmin, tmax = parse_team_size("2–5")
        self.assertEqual((tmin, tmax), (2, 5))

        # "1" -> team_size_min = 1, team_size_max = 1
        tmin, tmax = parse_team_size("1")
        self.assertEqual((tmin, tmax), (1, 1))

        # is_team = False -> team_size_min = 1, team_size_max = 1
        tmin, tmax = parse_team_size(None, is_team=False)
        self.assertEqual((tmin, tmax), (1, 1))

        # Unavailable -> None
        tmin, tmax = parse_team_size(None)
        self.assertIsNone(tmin)
        self.assertIsNone(tmax)

    def test_prize_parsing_examples_from_prompt(self):
        # "₹1,00,000" -> prize = "₹1,00,000", prize_amount = 100000, currency = "INR"
        p, amt, curr = parse_prize("₹1,00,000")
        self.assertEqual(p, "₹1,00,000")
        self.assertEqual(amt, 100000.0)
        self.assertEqual(curr, "INR")

        # "$10,000" -> prize_amount = 10000, currency = "USD"
        p, amt, curr = parse_prize("$10,000")
        self.assertEqual(amt, 10000.0)
        self.assertEqual(curr, "USD")

        # "€5,000" -> prize_amount = 5000, currency = "EUR"
        p, amt, curr = parse_prize("€5,000")
        self.assertEqual(amt, 5000.0)
        self.assertEqual(curr, "EUR")

        # "Prize Pool: ₹1 Lakh"
        p, amt, curr = parse_prize("Prize Pool: ₹1 Lakh")
        self.assertEqual(amt, 100000.0)
        self.assertEqual(curr, "INR")

        # "★ ₹10 Lakh Prize Pool"
        p, amt, curr = parse_prize("★ ₹10 Lakh Prize Pool")
        self.assertEqual(amt, 1000000.0)
        self.assertEqual(curr, "INR")

        # Non-numeric prize preserves text and sets prize_amount to None
        p, amt, curr = parse_prize("Exclusive Mentorship & Swags")
        self.assertEqual(p, "Exclusive Mentorship & Swags")
        self.assertIsNone(amt)
        self.assertIsNone(curr)

    def test_location_and_mode_examples_from_prompt(self):
        # "Online (Online)" -> location = "Online", event_mode = "Online"
        loc, mode = parse_location_and_mode("Online (Online)")
        self.assertEqual(loc, "Online")
        self.assertEqual(mode, "Online")

        # Physical location -> location = "Bangalore", event_mode = "Offline"
        loc, mode = parse_location_and_mode("Bangalore")
        self.assertEqual(loc, "Bangalore")
        self.assertEqual(mode, "Offline")

        # Empty / None
        loc, mode = parse_location_and_mode(None)
        self.assertIsNone(loc)
        self.assertIsNone(mode)

    def test_tags_handling(self):
        raw_tags = ["Cloud", "AWS", "cloud", "  AWS  ", "DevOps"]
        cleaned = clean_tags(raw_tags)
        self.assertEqual(cleaned, ["Cloud", "AWS", "DevOps"])

        empty_tags = clean_tags([])
        self.assertEqual(empty_tags, [])

    def test_domain_extraction(self):
        d1 = extract_domain("https://www.hackerearth.com/challenges/hackathon/test/")
        self.assertEqual(d1, "hackerearth.com")
        d2 = extract_domain("https://codekitchen.aim.media/?c=hk_web")
        self.assertEqual(d2, "codekitchen.aim.media")

    def test_validation_integrity(self):
        valid_rec = {
            "title": "Test Challenge",
            "source_url": "https://www.hackerearth.com/challenges/test/",
            "start_date": "2026-11-02",
            "end_date": "2026-11-03",
            "registration_deadline": "2026-10-28",
            "team_size_min": 1,
            "team_size_max": 3,
            "prize": "₹1,00,000",
            "prize_amount": 100000.0,
            "currency": "INR",
            "tags": ["Cloud", "AWS"],
        }
        res = validate_record(valid_rec.copy())
        self.assertEqual(res["start_date"], "2026-11-02")
        self.assertEqual(res["end_date"], "2026-11-03")

        # Invalid dates order (start > end) -> end_date nullified
        invalid_dates = valid_rec.copy()
        invalid_dates["start_date"] = "2026-12-01"
        invalid_dates["end_date"] = "2026-11-01"
        res_inv = validate_record(invalid_dates)
        self.assertIsNone(res_inv["end_date"])


if __name__ == "__main__":
    unittest.main()
