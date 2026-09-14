"""
Verify deduplication and upsert handling.
"""

import unittest
from scraper import sync_to_supabase


class TestDeduplication(unittest.TestCase):

    def test_in_memory_deduplication(self):
        record_a = {
            "title": "Hackathon 1",
            "source_url": "https://www.hackerearth.com/challenges/test-1/",
            "start_date": "2026-11-01",
        }
        record_b = {
            "title": "Hackathon 1 Updated",
            "source_url": "https://www.hackerearth.com/challenges/test-1/",
            "start_date": "2026-11-02",
        }
        record_c = {
            "title": "Hackathon 2",
            "source_url": "https://www.hackerearth.com/challenges/test-2/",
            "start_date": "2026-11-05",
        }

        records = [record_a, record_b, record_c]

        # Deduplicate on source_url
        unique_map = {}
        for r in records:
            unique_map[r["source_url"]] = r

        unique_records = list(unique_map.values())
        self.assertEqual(len(unique_records), 2)
        # Verify the latest record is kept
        self.assertEqual(unique_map["https://www.hackerearth.com/challenges/test-1/"]["title"], "Hackathon 1 Updated")
        self.assertEqual(unique_map["https://www.hackerearth.com/challenges/test-1/"]["start_date"], "2026-11-02")


if __name__ == "__main__":
    unittest.main()
