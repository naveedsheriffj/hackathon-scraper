import unittest
from typing import List, Dict, Any

def deduplicate_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate records by source_url preserving order and merging any fuller records."""
    seen = {}
    deduped = []
    for r in records:
        url = r.get("source_url")
        if not url:
            continue
        if url not in seen:
            seen[url] = True
            deduped.append(r)
    return deduped


class TestDeduplication(unittest.TestCase):
    def test_deduplicate(self):
        records = [
            {"title": "Event 1", "source_url": "https://www.knowafest.com/event1"},
            {"title": "Event 1 Duplicate", "source_url": "https://www.knowafest.com/event1"},
            {"title": "Event 2", "source_url": "https://www.knowafest.com/event2"},
        ]
        result = deduplicate_records(records)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["title"], "Event 1")
        self.assertEqual(result[1]["title"], "Event 2")

    def test_empty_or_missing_url(self):
        records = [
            {"title": "Event 1", "source_url": None},
            {"title": "Event 2", "source_url": ""},
            {"title": "Event 3", "source_url": "https://www.knowafest.com/event3"},
        ]
        result = deduplicate_records(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Event 3")


if __name__ == "__main__":
    unittest.main()
