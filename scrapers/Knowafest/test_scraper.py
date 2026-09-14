import unittest
from typing import Optional, Tuple, List, Dict, Any

from scraper import (
    clean_text,
    parse_single_date,
    parse_date_range,
    parse_team_size,
    parse_prize,
    parse_location_and_mode,
    clean_tags,
    validate_record,
    load_env,
    get_supabase_credentials,
)


class TestParsers(unittest.TestCase):
    def test_single_date(self):
        self.assertEqual(parse_single_date("2 Nov 2026"), "2026-11-02")
        self.assertEqual(parse_single_date("12th September 2026"), "2026-09-12")
        self.assertEqual(parse_single_date("Registration closes: 28 Oct 2026"), "2026-10-28")
        self.assertEqual(parse_single_date("13.09.2026"), "2026-09-13")
        self.assertEqual(parse_single_date("September 10, 2026 – Round 1 Registration"), "2026-09-10")

    def test_date_range(self):
        s, e = parse_date_range("2 Nov 2026 – 3 Nov 2026")
        self.assertEqual((s, e), ("2026-11-02", "2026-11-03"))

        s, e = parse_date_range("15th - 16th September 2026")
        self.assertEqual((s, e), ("2026-09-15", "2026-09-16"))

        s, e = parse_date_range("2 Nov 2026")
        self.assertEqual((s, e), ("2026-11-02", None))

    def test_team_size(self):
        self.assertEqual(parse_team_size("1-3"), (1, 3))
        self.assertEqual(parse_team_size("2–5"), (2, 5))
        self.assertEqual(parse_team_size("1"), (1, 1))
        self.assertEqual(parse_team_size("Team Size : 3 to 5 members"), (3, 5))
        self.assertEqual(parse_team_size("teams of 3 to 4 members"), (3, 4))
        self.assertEqual(parse_team_size("max of 5 partcipants"), (1, 5))
        self.assertEqual(parse_team_size("up to 4 members"), (1, 4))
        self.assertEqual(parse_team_size(None), (None, None))

    def test_prize(self):
        p, amt, curr = parse_prize("₹1,00,000")
        self.assertEqual(amt, 100000.0)
        self.assertEqual(curr, "INR")

        p, amt, curr = parse_prize("Prize Pool: ₹1 Lakh")
        self.assertEqual(amt, 100000.0)
        self.assertEqual(curr, "INR")

        p, amt, curr = parse_prize("$10,000")
        self.assertEqual(amt, 10000.0)
        self.assertEqual(curr, "USD")

        p, amt, curr = parse_prize("€5,000")
        self.assertEqual(amt, 5000.0)
        self.assertEqual(curr, "EUR")

        p, amt, curr = parse_prize("Combined Prize Pool: ₹11,000+")
        self.assertEqual(amt, 11000.0)
        self.assertEqual(curr, "INR")

        p, amt, curr = parse_prize("CASH PRIZES")
        self.assertEqual(p, "CASH PRIZES")
        self.assertIsNone(amt)
        self.assertIsNone(curr)

        p, amt, curr = parse_prize("No mention of money here")
        self.assertIsNone(p)
        self.assertIsNone(amt)
        self.assertIsNone(curr)

    def test_location_and_mode(self):
        l, m = parse_location_and_mode("Online (Online)", None)
        self.assertEqual((l, m), ("Online", "Online"))

        l, m = parse_location_and_mode("Coimbatore, Tamil Nadu", "Offline and Online Mode")
        self.assertEqual((l, m), ("Coimbatore, Tamil Nadu", "Hybrid"))

        l, m = parse_location_and_mode("Chennai, Tamil Nadu", "Venue/Offline Mode")
        self.assertEqual((l, m), ("Chennai, Tamil Nadu", "Offline"))

        l, m = parse_location_and_mode(None, None)
        self.assertEqual((l, m), (None, None))

    def test_clean_tags(self):
        tags = ["• AI", "Machine Learning", "ai", "IoT", "• AI", ""]
        cleaned = clean_tags(tags)
        self.assertEqual(cleaned, ["AI", "Machine Learning", "IoT"])

    def test_validate_record(self):
        rec = {
            "title": "Cloud Computing Challenge",
            "source_url": "https://www.knowafest.com/events/1",
            "start_date": "2026-11-02",
            "end_date": "2026-11-03",
            "registration_deadline": "2026-10-28",
            "team_size_min": "1",
            "team_size_max": "3",
            "prize": "₹1,00,000",
            "prize_amount": "100000",
            "currency": "INR",
            "tags": ["Cloud", "AWS"],
        }
        valid = validate_record(rec)
        self.assertIsNotNone(valid)
        self.assertEqual(valid["team_size_min"], 1)
        self.assertEqual(valid["team_size_max"], 3)
        self.assertEqual(valid["prize_amount"], 100000.0)
        self.assertEqual(valid["currency"], "INR")

    def test_validate_invalid_record(self):
        # Missing title
        self.assertIsNone(validate_record({"title": "", "source_url": "https://valid.com"}))
        # Invalid URL
        self.assertIsNone(validate_record({"title": "Test", "source_url": "invalid-url"}))


if __name__ == "__main__":
    unittest.main()
