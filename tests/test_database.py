"""Unit tests for central database layer and deduplication."""

import unittest
from common.database import deduplicate_records


class TestDatabaseDeduplication(unittest.TestCase):

    def test_deduplicate_by_source_url(self):
        records = [
            {"title": "Hackathon 1", "source_url": "https://example.com/h1", "status": "Upcoming"},
            {"title": "Hackathon 1 Updated", "source_url": "https://example.com/h1", "status": "Ongoing"},
            {"title": "Hackathon 2", "source_url": "https://example.com/h2", "status": "Upcoming"},
        ]

        deduped = deduplicate_records(records)
        self.assertEqual(len(deduped), 2)

        # Check latest record is preserved
        h1 = next(r for r in deduped if r["source_url"] == "https://example.com/h1")
        self.assertEqual(h1["title"], "Hackathon 1 Updated")
        self.assertEqual(h1["status"], "Ongoing")

    def test_empty_or_missing_source_url_ignored(self):
        records = [
            {"title": "Valid Event", "source_url": "https://example.com/valid"},
            {"title": "Invalid Event 1", "source_url": ""},
            {"title": "Invalid Event 2", "source_url": None},
            {"title": "Invalid Event 3"},
        ]

        deduped = deduplicate_records(records)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source_url"], "https://example.com/valid")


if __name__ == "__main__":
    unittest.main()
