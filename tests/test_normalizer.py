"""Unit tests for central normalization functions.

Verifies date parsing, team size extraction, prize calculation, mode detection,
and validation logic against zero-hallucination rules.
"""

import unittest
from common.normalizer import (
    clean_tags,
    clean_text,
    extract_domain,
    parse_date_range,
    parse_location_and_mode,
    parse_prize,
    parse_single_date,
    parse_team_size,
    validate_record,
)


class TestNormalizer(unittest.TestCase):

    def test_date_parsing_ranges(self):
        # Range with en-dash
        s, e = parse_date_range("2 Nov 2026 – 3 Nov 2026")
        self.assertEqual(s, "2026-11-02")
        self.assertEqual(e, "2026-11-03")

        # Range with hyphen
        s, e = parse_date_range("2 Nov 2026 - 3 Nov 2026")
        self.assertEqual(s, "2026-11-02")
        self.assertEqual(e, "2026-11-03")

        # Range within same month shorthand
        s, e = parse_date_range("Sep 25 - 26, 2026")
        self.assertEqual(s, "2026-09-25")
        self.assertEqual(e, "2026-09-26")

        # Single date with year
        s, e = parse_date_range("2 Nov 2026")
        self.assertEqual(s, "2026-11-02")
        self.assertIsNone(e)

        # Single date with conversational prefix
        d = parse_single_date("Registration closes: 28 Oct 2026")
        self.assertEqual(d, "2026-10-28")

        # Unparseable date returns None (no guessing)
        self.assertIsNone(parse_single_date("Hackathon has ended"))
        self.assertIsNone(parse_single_date("TBA"))
        self.assertIsNone(parse_single_date(""))
        self.assertIsNone(parse_single_date(None))

    def test_team_size_parsing(self):
        # Ranges
        self.assertEqual(parse_team_size("1-3"), (1, 3))
        self.assertEqual(parse_team_size("2–5"), (2, 5))
        self.assertEqual(parse_team_size("1 to 4 members"), (1, 4))
        self.assertEqual(parse_team_size("up to 4 members"), (1, 4))

        # Single value
        self.assertEqual(parse_team_size("1"), (1, 1))
        self.assertEqual(parse_team_size(1), (1, 1))

        # Dict input
        self.assertEqual(parse_team_size({"min": 2, "max": 4}), (2, 4))

        # Inverted team size: NEVER silently swap, return (None, None)
        self.assertEqual(parse_team_size("5-2"), (None, None))
        self.assertEqual(parse_team_size({"min": 4, "max": 2}), (None, None))

        # Empty / None
        self.assertEqual(parse_team_size(""), (None, None))
        self.assertEqual(parse_team_size(None), (None, None))

    def test_prize_parsing(self):
        # INR standard format
        p, amt, curr = parse_prize("₹1,00,000")
        self.assertEqual(p, "₹1,00,000")
        self.assertEqual(amt, 100000.0)
        self.assertEqual(curr, "INR")

        # INR word denomination: Lakh
        p, amt, curr = parse_prize("Prize Pool: ₹1 Lakh")
        self.assertEqual(amt, 100000.0)
        self.assertEqual(curr, "INR")

        # INR word denomination: Crore
        p, amt, curr = parse_prize("Prize: 2 Crore")
        self.assertEqual(amt, 20000000.0)
        self.assertEqual(curr, "INR")

        # USD
        p, amt, curr = parse_prize("$10,000")
        self.assertEqual(amt, 10000.0)
        self.assertEqual(curr, "USD")

        # EUR
        p, amt, curr = parse_prize("€5,000")
        self.assertEqual(amt, 5000.0)
        self.assertEqual(curr, "EUR")

        # Non-numeric prize text: amount & curr None, raw string preserved
        p, amt, curr = parse_prize("Exclusive Mentorship and Swags")
        self.assertEqual(p, "Exclusive Mentorship and Swags")
        self.assertIsNone(amt)
        self.assertIsNone(curr)

    def test_location_and_mode_disambiguation(self):
        loc, mode = parse_location_and_mode("Online (Online)")
        self.assertEqual(loc, "Online")
        self.assertEqual(mode, "Online")

        loc, mode = parse_location_and_mode("Bengaluru, India")
        self.assertEqual(loc, "Bengaluru, India")
        self.assertEqual(mode, "Offline")

        loc, mode = parse_location_and_mode("Bengaluru, India", mode_hint="hybrid")
        self.assertEqual(loc, "Bengaluru, India")
        self.assertEqual(mode, "Hybrid")

        loc, mode = parse_location_and_mode(None, address_dict={"city": "Pune", "state": "MH"})
        self.assertEqual(loc, "Pune, MH")
        self.assertEqual(mode, "Offline")

    def test_clean_tags(self):
        raw = ["• Cloud", "AWS", "cloud", "  AWS  ", "", None]
        self.assertEqual(clean_tags(raw), ["Cloud", "AWS"])

    def test_extract_domain(self):
        self.assertEqual(extract_domain("https://devfolio.co/hackathons/test"), "devfolio.co")
        self.assertEqual(extract_domain("https://www.hackerearth.com/challenges"), "hackerearth.com")
        self.assertIsNone(extract_domain(""))


if __name__ == "__main__":
    unittest.main()
