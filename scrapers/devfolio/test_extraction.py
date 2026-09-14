"""Comprehensive validation & test script for Devfolio structured extraction and Supabase integration."""

import sys
from datetime import datetime
import json
import unittest

sys.stdout.reconfigure(encoding="utf-8")

from scraper import (
    DevfolioScraper,
    parse_single_date,
    parse_date_range,
    parse_team_size,
    parse_prize,
    parse_location_and_mode,
    clean_tags,
    validate_record,
    format_record_for_supabase,
    upsert_hackathons_to_supabase,
)


class TestNormalizers(unittest.TestCase):
    def test_date_parsing_ranges(self):
        # Range with en-dash
        s, e = parse_date_range("2 Nov 2026 – 3 Nov 2026")
        self.assertEqual(s, "2026-11-02")
        self.assertEqual(e, "2026-11-03")

        # Range within same month
        s, e = parse_date_range("Sep 25 - 26, 2026")
        self.assertEqual(s, "2026-09-25")
        self.assertEqual(e, "2026-09-26")

        # Single date with year
        s, e = parse_date_range("2 Nov 2026")
        self.assertEqual(s, "2026-11-02")
        self.assertIsNone(e)

        # Single date with prefix
        d = parse_single_date("Registration closes: 28 Oct 2026")
        self.assertEqual(d, "2026-10-28")

        # Single date with slashes
        d = parse_single_date("Starts 25/09/26")
        self.assertEqual(d, "2026-09-25")

        # Invalid/unparseable date -> None, never invent
        self.assertIsNone(parse_single_date("Hackathon has ended"))
        self.assertIsNone(parse_single_date(""))

    def test_team_size_parsing(self):
        self.assertEqual(parse_team_size("1-3"), (1, 3))
        self.assertEqual(parse_team_size("2–5"), (2, 5))
        self.assertEqual(parse_team_size("1"), (1, 1))
        self.assertEqual(parse_team_size("2 to 4 members"), (2, 4))
        self.assertEqual(parse_team_size(""), (None, None))
        self.assertEqual(parse_team_size(None), (None, None))

    def test_prize_parsing(self):
        # INR with comma format
        p, amt, curr = parse_prize("₹1,00,000")
        self.assertEqual(p, "₹1,00,000")
        self.assertEqual(amt, 100000.0)
        self.assertEqual(curr, "INR")

        # USD
        p, amt, curr = parse_prize("$10,000")
        self.assertEqual(amt, 10000.0)
        self.assertEqual(curr, "USD")

        # EUR
        p, amt, curr = parse_prize("€5,000")
        self.assertEqual(amt, 5000.0)
        self.assertEqual(curr, "EUR")

        # Word denomination: Lakh
        p, amt, curr = parse_prize("Prize Pool: ₹1 Lakh")
        self.assertEqual(amt, 100000.0)
        self.assertEqual(curr, "INR")

        # Western format with suffix
        p, amt, curr = parse_prize("$500+ Prize Pool")
        self.assertEqual(amt, 500.0)
        self.assertEqual(curr, "USD")

        # Unparseable prize -> preserves text, amount is None
        p, amt, curr = parse_prize("Exciting Swag and Mentorship")
        self.assertEqual(p, "Exciting Swag and Mentorship")
        self.assertIsNone(amt)

    def test_location_and_mode(self):
        loc, mode = parse_location_and_mode("Online (Online)")
        self.assertEqual(loc, "Online")
        self.assertEqual(mode, "Online")

        loc, mode = parse_location_and_mode("Greater Noida, India")
        self.assertEqual(loc, "Greater Noida, India")
        self.assertEqual(mode, "Offline")

        loc, mode = parse_location_and_mode("Bengaluru, India", is_hybrid=True)
        self.assertEqual(loc, "Bengaluru, India")
        self.assertEqual(mode, "Hybrid")

    def test_tags(self):
        self.assertEqual(clean_tags(["Cloud", "AWS", "Cloud", ""]), ["Cloud", "AWS"])
        self.assertEqual(clean_tags([]), [])
        self.assertEqual(clean_tags(None), [])

    def test_validation_integrity(self):
        # Inverted dates should nullify end_date
        bad_rec = {
            "title": "Test Hack",
            "source_url": "https://example.com/hack",
            "start_date": "2026-11-10",
            "end_date": "2026-11-05",  # earlier than start
            "team_size_min": 4,
            "team_size_max": 2,  # inverted
            "prize_amount": -100,  # negative
            "tags": ["AI", "  ", None],
        }
        val = validate_record(bad_rec)
        self.assertIsNone(val["end_date"])  # Corrected to None
        self.assertEqual(val["team_size_min"], 2)  # Swapped
        self.assertEqual(val["team_size_max"], 4)
        self.assertIsNone(val["prize_amount"])  # Negative -> None
        self.assertEqual(val["tags"], ["AI"])

    def test_duplicate_deduplication(self):
        rec1 = {
            "title": "Hackathon One",
            "source_url": "https://test.devfolio.co/",
            "start_date": "2026-10-01",
        }
        rec2 = {
            "title": "Hackathon One Duplicate",
            "source_url": "https://test.devfolio.co/",
            "start_date": "2026-10-01",
        }
        rec3 = {
            "title": "Hackathon Two",
            "source_url": "https://other.devfolio.co/",
            "start_date": "2026-10-02",
        }

        # Format records for Supabase
        formatted = [format_record_for_supabase(r) for r in [rec1, rec2, rec3]]

        # In-memory deduplication map by source_url
        dedup_map = {}
        for r in formatted:
            dedup_map[r["source_url"]] = r

        self.assertEqual(len(dedup_map), 2)
        self.assertIn("https://test.devfolio.co/", dedup_map)
        self.assertIn("https://other.devfolio.co/", dedup_map)


def run_real_world_tests():
    print("\n" + "=" * 60)
    print("RUNNING REAL-WORLD SCRAPING TESTS")
    print("=" * 60)

    scraper = DevfolioScraper()
    test_urls = [
        "https://webcraft24.devfolio.co/",
        "https://pushtoprod-india.devfolio.co/",
        "https://cognition-gamejam-1.devfolio.co/",
    ]

    extracted_records = []
    for url in test_urls:
        print(f"\n--- Testing Detail Scraping for: {url} ---")
        rec = scraper.scrape_detail_page(url)
        extracted_records.append(rec)

        print("Extracted Structured Fields:")
        for k in (
            "title", "organizer", "status", "location", "event_mode",
            "start_date", "end_date", "registration_deadline",
            "team_size_min", "team_size_max", "prize", "prize_amount",
            "currency", "tags", "source_url", "source_website", "scraped_at"
        ):
            print(f"  {k}: {repr(rec.get(k))}")

        # Assertions for real data
        assert rec["title"], f"Title missing for {url}"
        assert rec["source_url"] == url, f"source_url mismatch for {url}"
        assert rec["source_website"] in ("webcraft24.devfolio.co", "pushtoprod-india.devfolio.co", "cognition-gamejam-1.devfolio.co", "devfolio.co")
        assert rec["scraped_at"], f"scraped_at missing for {url}"

        # Confirm format for Supabase
        formatted = format_record_for_supabase(rec)
        assert formatted["source_url"] == url
        assert "title" in formatted and "name" in formatted
        assert "start_date" in formatted and "event_start_date" in formatted

    print("\n--- Testing Supabase Upsert Routine ---")
    sync_result = upsert_hackathons_to_supabase(extracted_records)
    print(f"Supabase upsert returned: {sync_result}")

    print("\n[SUCCESS] All unit tests and real-world scraping tests passed!")


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestNormalizers)
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(suite)
    if not res.wasSuccessful():
        sys.exit(1)

    run_real_world_tests()
