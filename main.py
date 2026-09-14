"""Central Hackathon Scraper Orchestrator.

Sequentially executes the 5 independent hackathon scrapers:
1. Devfolio
2. Devpost
3. HackerEarth
4. Knowafest
5. Unstop

Fault Tolerance:
Each scraper executes within an isolated error boundary. A failure in one scraper
does not terminate the run; the remaining platforms continue processing.

Centralized Logging & Reporting:
Prints structured execution progress and summary metrics without exposing credentials.
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

from common.database import deduplicate_records, save_to_csv, save_to_json, upsert_hackathons
from common.models import Hackathon
from common.supabase_client import get_supabase_credentials, load_env
from common.utils import print_banner, print_summary_table, setup_logging

# Initialize central logging
logger = setup_logging()

# Supported 5 platforms in canonical order
SUPPORTED_PLATFORMS = [
    "devfolio",
    "devpost",
    "hackerearth",
    "knowafest",
    "unstop",
]


def run_devfolio(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Run the Devfolio scraper."""
    from scrapers.devfolio.scraper import DevfolioScraper
    scraper = DevfolioScraper()
    records = scraper.scrape(deep=True, deep_limit=limit)
    return records


def run_devpost(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Run the Devpost scraper."""
    from scrapers.devpost.scraper import scrape_devpost
    records = scrape_devpost(start_page=1, all_pages=False, fetch_details=True)
    if limit and records:
        records = records[:limit]
    return records


def run_hackerearth(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Run the HackerEarth scraper."""
    from scrapers.HackerEarth.scraper import scrape_challenges
    records = scrape_challenges()
    if limit and records:
        records = records[:limit]
    return records


def run_knowafest(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Run the Knowafest scraper."""
    from scrapers.Knowafest.scraper import scrape_knowafest_hackathons
    records = scrape_knowafest_hackathons(limit=limit)
    return records


def run_unstop(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Run the Unstop scraper."""
    from scrapers.unstop.scraper import scrape_hackathons
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        # In case we're inside an active event loop
        import nest_asyncio
        nest_asyncio.apply()

    records = asyncio.run(scrape_hackathons(limit=limit))
    return records


RUNNERS = {
    "devfolio": ("Devfolio", run_devfolio),
    "devpost": ("Devpost", run_devpost),
    "hackerearth": ("HackerEarth", run_hackerearth),
    "knowafest": ("Knowafest", run_knowafest),
    "unstop": ("Unstop", run_unstop),
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Central Hackathon Scraper Orchestrator (Devfolio, Devpost, HackerEarth, Knowafest, Unstop)"
    )
    parser.add_argument(
        "--platform",
        choices=SUPPORTED_PLATFORMS + ["all"],
        default="all",
        help="Target platform to scrape (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of hackathons scraped per platform (useful for testing)",
    )
    parser.add_argument(
        "--supabase",
        dest="sync_supabase",
        action="store_true",
        default=True,
        help="Synchronize scraped records to Supabase database (default: True)",
    )
    parser.add_argument(
        "--no-supabase",
        dest="sync_supabase",
        action="store_false",
        help="Disable Supabase sync and only export locally",
    )
    parser.add_argument(
        "--table",
        default=None,
        help="Target Supabase table name (default from SUPABASE_TABLE or 'hackathons')",
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv", "all"],
        default="all",
        help="Local export format: json, csv, or all (default: all)",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory to save consolidated output files (default: 'data')",
    )

    args = parser.parse_args()

    # Load environment variables
    load_env()

    print_banner()

    target_platforms = (
        SUPPORTED_PLATFORMS
        if args.platform == "all"
        else [args.platform]
    )

    execution_results: List[Dict[str, Any]] = []
    all_scraped_records: List[Dict[str, Any]] = []

    for index, p_key in enumerate(target_platforms, 1):
        name, runner_fn = RUNNERS[p_key]
        print(f"\n[{index}/{len(target_platforms)}] Scraping platform: {name}...")
        start_time = time.time()

        try:
            records = runner_fn(limit=args.limit) or []
            elapsed = time.time() - start_time
            logger.info("[%s] Successfully scraped %d records in %.2fs", name, len(records), elapsed)

            # Validate records using common model
            validated_records = []
            for r in records:
                try:
                    h = Hackathon.from_dict(r)
                    if h.validate():
                        validated_records.append(h.to_dict())
                except Exception as val_err:
                    logger.warning("[%s] Error validating record: %s", name, val_err)

            all_scraped_records.extend(validated_records)

            # Sync to Supabase if requested
            sync_stats = {"scraped": len(validated_records), "inserted_or_updated": 0, "failed": 0}
            if args.sync_supabase and validated_records:
                sync_stats = upsert_hackathons(validated_records, table_name=args.table)

            execution_results.append({
                "platform": name,
                "status": "SUCCESS",
                "scraped": len(validated_records),
                "inserted_or_updated": sync_stats.get("inserted_or_updated", 0),
                "failed": sync_stats.get("failed", 0),
                "error": None,
            })

        except Exception as exc:
            elapsed = time.time() - start_time
            logger.error("[%s] FAILED after %.2fs: %s", name, elapsed, exc, exc_info=False)
            execution_results.append({
                "platform": name,
                "status": "FAILED",
                "scraped": 0,
                "inserted_or_updated": 0,
                "failed": 0,
                "error": str(exc),
            })
            # Continue to next platform! Failure of one scraper never terminates others.
            continue

    # Central local export of all consolidated records
    if all_scraped_records:
        deduped_all = deduplicate_records(all_scraped_records)
        os.makedirs(args.output_dir, exist_ok=True)
        if args.format in ("json", "all"):
            save_to_json(deduped_all, os.path.join(args.output_dir, "all_hackathons.json"))
        if args.format in ("csv", "all"):
            save_to_csv(deduped_all, os.path.join(args.output_dir, "all_hackathons.csv"))

    # Print formatted summary table
    print_summary_table(execution_results)


if __name__ == "__main__":
    main()
