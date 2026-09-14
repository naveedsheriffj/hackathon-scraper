"""Utility helpers for logging, formatting, and console output.

Ensures credentials are never printed to terminal or log outputs.
"""

import logging
import os
import sys
from typing import Dict, List, Optional


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure and return the root logger for the hackathon scraper suite."""
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger("hackathon_scraper")
    root_logger.setLevel(level)

    # Avoid duplicate handlers
    if not root_logger.handlers:
        root_logger.addHandler(handler)

    return root_logger


def print_banner() -> None:
    """Print the startup banner."""
    print("\n" + "=" * 50)
    print("       CENTRAL HACKATHON SCRAPER SUITE")
    print("=" * 50)


def print_summary_table(results: List[Dict[str, Any]]) -> None:
    """Print formatted execution summary table across all scraped platforms."""
    print("\n" + "=" * 60)
    print("PLATFORM EXECUTION SUMMARY")
    print("=" * 60)

    total_scraped = 0
    total_synced = 0
    total_failed = 0

    for idx, r in enumerate(results, 1):
        platform = r.get("platform", "Unknown")
        status = r.get("status", "UNKNOWN")
        scraped = r.get("scraped", 0)
        synced = r.get("inserted_or_updated", 0)
        failed = r.get("failed", 0)
        error = r.get("error")

        total_scraped += scraped
        total_synced += synced
        total_failed += failed

        status_str = f"[{status}]"
        print(f"[{idx}/5] {platform:<14} {status_str:>10}")
        print(f"      Scraped: {scraped:<4} | Inserted/Updated: {synced:<4} | Failed: {failed:<4}")
        if error:
            print(f"      Error: {error}")

    print("=" * 60)
    print("TOTAL METRICS")
    print("=" * 60)
    print(f"Total Scraped:          {total_scraped}")
    print(f"Total Inserted/Updated: {total_synced}")
    print(f"Total Failed:           {total_failed}")
    print("=" * 60 + "\n")
