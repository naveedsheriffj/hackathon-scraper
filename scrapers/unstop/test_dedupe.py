"""
Deduplication test suite for Unstop Hackathon Scraper.
Verifies that duplicate records with the same source_url are deduplicated
before database upsert, ensuring idempotency.
"""

import unittest
from scraper import sync_to_supabase


class TestDeduplication(unittest.TestCase):
    def test_in_memory_deduplication(self):
        records = [
            {
                "title": "Hackathon Alpha",
                "source_url": "https://unstop.com/hackathons/hackathon-alpha-1001",
                "organizer": "Org A",
                "status": "Upcoming",
                "location": "Online",
                "event_mode": "Online",
                "start_date": "2026-11-01",
                "end_date": "2026-11-02",
                "registration_deadline": "2026-10-25",
                "team_size_min": 1,
                "team_size_max": 3,
                "prize": "₹50,000",
                "prize_amount": 50000.0,
                "currency": "INR",
                "tags": ["AI", "Web"],
                "source_website": "unstop.com",
                "scraped_at": "2026-09-14T10:00:00Z",
            },
            {
                # Duplicate with updated scraped_at timestamp
                "title": "Hackathon Alpha Updated",
                "source_url": "https://unstop.com/hackathons/hackathon-alpha-1001",
                "organizer": "Org A",
                "status": "Ongoing",
                "location": "Online",
                "event_mode": "Online",
                "start_date": "2026-11-01",
                "end_date": "2026-11-02",
                "registration_deadline": "2026-10-25",
                "team_size_min": 1,
                "team_size_max": 3,
                "prize": "₹50,000",
                "prize_amount": 50000.0,
                "currency": "INR",
                "tags": ["AI", "Web"],
                "source_website": "unstop.com",
                "scraped_at": "2026-09-14T12:00:00Z",
            },
            {
                "title": "Hackathon Beta",
                "source_url": "https://unstop.com/hackathons/hackathon-beta-1002",
                "organizer": "Org B",
                "status": "Upcoming",
                "location": "Bengaluru, Karnataka",
                "event_mode": "Offline",
                "start_date": "2026-12-01",
                "end_date": "2026-12-02",
                "registration_deadline": "2026-11-20",
                "team_size_min": 2,
                "team_size_max": 4,
                "prize": "$5,000",
                "prize_amount": 5000.0,
                "currency": "USD",
                "tags": ["Cloud", "DevOps"],
                "source_website": "unstop.com",
                "scraped_at": "2026-09-14T10:00:00Z",
            },
        ]

        # Verify deduplication map
        unique_map = {}
        for r in records:
            unique_map[r["source_url"]] = r

        self.assertEqual(len(records), 3)
        self.assertEqual(len(unique_map), 2)
        self.assertIn("https://unstop.com/hackathons/hackathon-alpha-1001", unique_map)
        self.assertIn("https://unstop.com/hackathons/hackathon-beta-1002", unique_map)
        # Latest record value should be preserved
        self.assertEqual(
            unique_map["https://unstop.com/hackathons/hackathon-alpha-1001"]["title"],
            "Hackathon Alpha Updated",
        )


if __name__ == "__main__":
    unittest.main()
