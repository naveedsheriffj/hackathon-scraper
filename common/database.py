"""Central Database Layer for Hackathon Scrapers.

Handles:
- In-memory deduplication on `source_url` (preserving latest record for conflict resolution).
- Idempotent upserting to Supabase PostgreSQL table 'hackathons' (conflict target: source_url).
- Graceful handling of database connectivity failures without terminating execution.
- Local JSON and CSV fallback preservation.
- Reporting structured sync metrics (scraped, inserted_or_updated, failed).
"""

import csv
import json
import logging
import os
from typing import Any, Dict, List, Optional

from common.models import Hackathon
from common.supabase_client import get_supabase_client, get_supabase_credentials

logger = logging.getLogger("hackathon_scraper.database")


def deduplicate_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate records by unique source_url, preserving the latest record."""
    unique_map: Dict[str, Dict[str, Any]] = {}
    for r in records:
        if not r or not isinstance(r, dict):
            continue
        url = r.get("source_url") or r.get("url")
        if not url or not str(url).strip():
            continue
        unique_map[url] = r

    return list(unique_map.values())


def upsert_hackathons(
    records: List[Dict[str, Any]],
    table_name: Optional[str] = None,
    batch_size: int = 50,
) -> Dict[str, int]:
    """Upsert validated records into Supabase using source_url for deduplication.

    Returns:
        Dict with keys 'scraped', 'inserted_or_updated', and 'failed'.
    """
    total_scraped = len(records)
    metrics = {
        "scraped": total_scraped,
        "inserted_or_updated": 0,
        "failed": 0,
    }

    if not records:
        logger.info("[DATABASE] No records provided for upsert.")
        return metrics

    # 1. In-memory deduplication
    unique_records = deduplicate_records(records)
    if not unique_records:
        logger.warning("[DATABASE] No records with valid source_url to sync.")
        metrics["failed"] = total_scraped
        return metrics

    url, _, default_table = get_supabase_credentials()
    target_table = table_name or default_table or "hackathons"

    client = get_supabase_client()
    if not client:
        logger.warning(
            "[DATABASE] Connectivity Notice: Supabase client unavailable. "
            "All %d records preserved locally.",
            len(unique_records),
        )
        metrics["failed"] = len(unique_records)
        return metrics

    # Format records using common model for complete column support & backward compatibility
    formatted_payload = []
    for r in unique_records:
        try:
            h = Hackathon.from_dict(r)
            if h.validate():
                formatted_payload.append(h.to_supabase_dict())
            else:
                formatted_payload.append(r)
        except Exception:
            formatted_payload.append(r)

    logger.info(
        "[DATABASE] Upserting %d unique records to table '%s'...",
        len(formatted_payload),
        target_table,
    )

    # 2. Batch upsert
    successful_count = 0
    for i in range(0, len(formatted_payload), batch_size):
        batch = formatted_payload[i : i + batch_size]
        try:
            response = (
                client.table(target_table)
                .upsert(batch, on_conflict="source_url")
                .execute()
            )
            count = len(response.data) if response.data else len(batch)
            successful_count += count
        except Exception as exc:
            err_str = str(exc)
            logger.warning(
                "[DATABASE] Connectivity Error: Unable to sync batch with Supabase at %s: %s",
                url,
                err_str,
            )

            # Check for missing column error (PostgREST PGRST204) to attempt legacy fallback
            if "PGRST204" in err_str or "column" in err_str.lower():
                logger.warning(
                    "[DATABASE] Schema mismatch detected in Supabase table. Attempting legacy column fallback..."
                )
                legacy_cols = [
                    "name", "organizer", "location_mode", "event_start_date",
                    "event_end_date", "registration_deadline", "team_size",
                    "prize", "source_url", "registration_link"
                ]
                fallback_batch = [
                    {k: v for k, v in item.items() if k in legacy_cols}
                    for item in batch
                ]
                try:
                    fb_res = (
                        client.table(target_table)
                        .upsert(fallback_batch, on_conflict="source_url")
                        .execute()
                    )
                    fb_count = len(fb_res.data) if fb_res.data else len(fallback_batch)
                    successful_count += fb_count
                    continue
                except Exception as fb_err:
                    logger.warning("[DATABASE] Fallback sync also failed: %s", fb_err)

            metrics["failed"] += len(batch)

    metrics["inserted_or_updated"] = successful_count
    logger.info(
        "[DATABASE] Complete: %d inserted/updated, %d failed out of %d scraped.",
        metrics["inserted_or_updated"],
        metrics["failed"],
        metrics["scraped"],
    )
    return metrics


def save_to_json(records: List[Dict[str, Any]], filepath: str) -> None:
    """Save records to a formatted JSON file."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False, default=str)
    logger.info("[EXPORT] Saved %d records to JSON: %s", len(records), filepath)


def save_to_csv(records: List[Dict[str, Any]], filepath: str) -> None:
    """Save records to a formatted CSV file."""
    if not records:
        logger.warning("[EXPORT] No records to save to CSV.")
        return

    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    fieldnames = [
        "title", "organizer", "status", "location", "event_mode",
        "start_date", "end_date", "registration_deadline",
        "team_size_min", "team_size_max", "prize", "prize_amount",
        "currency", "tags", "source_url", "source_website", "scraped_at"
    ]

    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            row = dict(r)
            if isinstance(row.get("tags"), list):
                row["tags"] = ", ".join(str(t) for t in row["tags"])
            writer.writerow(row)
    logger.info("[EXPORT] Saved %d records to CSV: %s", len(records), filepath)
