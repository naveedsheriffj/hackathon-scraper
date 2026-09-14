import json
import csv
from collections import Counter

def verify():
    with open("unstop_hackathons.json", "r", encoding="utf-8") as f:
        records = json.load(f)

    print(f"=" * 70)
    print(f"HACKATHON DATABASE VERIFICATION REPORT")
    print(f"=" * 70)
    print(f"Total Hackathons: {len(records)}")

    # Lifecycle Status Breakdown
    reg_counts = Counter(r.get("registration_status") for r in records)
    event_counts = Counter(r.get("event_status") for r in records)
    combos = Counter((r.get("event_status"), r.get("registration_status")) for r in records)

    print("\n--- Lifecycle Status Distributions ---")
    print(f"Registration Status:")
    for status, count in sorted(reg_counts.items()):
        print(f"  * {status:10}: {count:5} ({count / len(records) * 100:.1f}%)")

    print(f"\nEvent Status:")
    for status, count in sorted(event_counts.items()):
        print(f"  * {status:10}: {count:5} ({count / len(records) * 100:.1f}%)")

    print(f"\nStatus Combinations (Event Status & Registration Status):")
    for (ev, reg), count in sorted(combos.items()):
        print(f"  * {ev:10} & {reg:8}: {count:5}")

    # Validation Checks
    print("\n--- Integrity & Consistency Checks ---")
    mismatched_open_flags = [
        r["id"] for r in records
        if r.get("is_registration_open") != (r.get("registration_status") == "OPEN")
    ]
    print(f"  [OK] is_registration_open synchronized with registration_status: {len(mismatched_open_flags) == 0} (mismatches: {len(mismatched_open_flags)})")

    closed_positive_days = [
        r["id"] for r in records
        if r.get("registration_status") == "CLOSED" and r.get("days_left", 0) > 0
    ]
    print(f"  [OK] Closed hackathons have days_left = 0: {len(closed_positive_days) == 0} (violations: {len(closed_positive_days)})")

    # Field statistics
    stats = {}
    for r in records:
        for k, v in r.items():
            if k not in stats:
                stats[k] = {"not_null": 0, "null": 0}
            if v is not None:
                stats[k]["not_null"] += 1
            else:
                stats[k]["null"] += 1

    print("\n--- Field Presence Statistics ---")
    for k, counts in stats.items():
        nn = counts["not_null"]
        nl = counts["null"]
        print(f"  {k:25}: {nn:5} populated, {nl:5} null")

    print("\n--- Sample 5 Hackathons (Detailed View) ---")
    for r in records[:5]:
        org = r["organization"]["name"] if r["organization"] else "None"
        prize_cash = r["prizes"]["total_cash"] if (r["prizes"] and r["prizes"].get("total_cash")) else "None"
        prize_curr = r["prizes"]["currency"] if (r["prizes"] and r["prizes"].get("currency")) else ""
        print(f"  * [{r['id']}] {r['title']}")
        print(f"    - URL: {r['url']}")
        print(f"    - Org: {org}")
        print(f"    - Event Status: {r.get('event_status')} | Registration Status: {r.get('registration_status')} (is_open: {r.get('is_registration_open')})")
        print(f"    - Remaining Time: {r.get('days_left')} days ({r.get('time_left')}) | Raw Unstop Status: {r.get('raw_status')}")
        print(f"    - Prize: {prize_cash} {prize_curr}")
        print(f"    - Deadline: {r.get('registration_deadline')} | End Date: {r.get('end_date')}")

    # Verify CSV
    with open("unstop_hackathons.csv", "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        csv_rows = list(reader)
    print(f"\nCSV Validation: {len(csv_rows)} rows matching JSON count ({len(records)}).")
    print("=" * 70)

if __name__ == "__main__":
    verify()
