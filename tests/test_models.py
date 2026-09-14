"""Unit tests for common Hackathon data model and validation integrity.

Verifies strict non-fabrication and no silent alteration of questionable data.
"""

import unittest
from common.models import Hackathon


class TestHackathonModel(unittest.TestCase):

    def test_valid_hackathon_creation(self):
        h = Hackathon(
            title="Global AI Hackathon 2026",
            source_url="https://example.com/global-ai-2026",
            organizer="AI Foundation",
            status="Upcoming",
            location="Online",
            event_mode="Online",
            start_date="2026-11-01",
            end_date="2026-11-03",
            registration_deadline="2026-10-25",
            team_size_min=1,
            team_size_max=4,
            prize="$50,000",
            prize_amount=50000.0,
            currency="USD",
            tags=["AI", "Machine Learning", "Cloud"],
        )

        self.assertTrue(h.validate())
        d = h.to_dict()
        self.assertEqual(d["title"], "Global AI Hackathon 2026")
        self.assertEqual(d["source_website"], "example.com")
        self.assertEqual(d["prize_amount"], 50000.0)
        self.assertEqual(len(d["tags"]), 3)

        sb_dict = h.to_supabase_dict()
        self.assertEqual(sb_dict["name"], "Global AI Hackathon 2026")
        self.assertEqual(sb_dict["team_size"], "1-4")

    def test_missing_mandatory_title_fails_validation(self):
        h1 = Hackathon(title="", source_url="https://example.com/test")
        self.assertFalse(h1.validate())

        h2 = Hackathon(title="   ", source_url="https://example.com/test")
        self.assertFalse(h2.validate())

    def test_missing_or_invalid_url_fails_validation(self):
        h1 = Hackathon(title="Test Event", source_url="")
        self.assertFalse(h1.validate())

        h2 = Hackathon(title="Test Event", source_url="not-a-valid-url")
        self.assertFalse(h2.validate())

    def test_inverted_team_size_nullified_not_swapped(self):
        """CRITICAL: User directive - DO NOT silently swap inverted team sizes. Set to NULL."""
        h = Hackathon(
            title="Team Size Test",
            source_url="https://example.com/team-test",
            team_size_min=4,
            team_size_max=2,  # inverted
        )
        self.assertTrue(h.validate())
        # Both must be set to None, NEVER swapped to 2, 4
        self.assertIsNone(h.team_size_min)
        self.assertIsNone(h.team_size_max)

    def test_inverted_dates_nullifies_end_date(self):
        h = Hackathon(
            title="Date Test",
            source_url="https://example.com/date-test",
            start_date="2026-11-10",
            end_date="2026-11-05",  # earlier than start
        )
        self.assertTrue(h.validate())
        self.assertEqual(h.start_date, "2026-11-10")
        self.assertIsNone(h.end_date)

    def test_negative_prize_amount_nullified(self):
        h = Hackathon(
            title="Prize Test",
            source_url="https://example.com/prize-test",
            prize="-$500",
            prize_amount=-500.0,
        )
        self.assertTrue(h.validate())
        self.assertIsNone(h.prize_amount)
        self.assertEqual(h.prize, "-$500")

    def test_tag_cleaning_and_deduplication(self):
        h = Hackathon(
            title="Tags Test",
            source_url="https://example.com/tags-test",
            tags=["AI", "Cloud", "ai", "CLOUD", "DevOps", "", None],
        )
        self.assertEqual(h.tags, ["AI", "Cloud", "DevOps"])


if __name__ == "__main__":
    unittest.main()
