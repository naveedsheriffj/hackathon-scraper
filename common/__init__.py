"""Common shared library for Hackathon Scrapers.

Contains unified data models, normalizers, Supabase client manager,
database sync layer, and logging utilities.
"""

from common.models import Hackathon
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
from common.supabase_client import get_supabase_client, get_supabase_credentials, load_env
from common.database import deduplicate_records, upsert_hackathons, save_to_csv, save_to_json
from common.utils import print_banner, print_summary_table, setup_logging

__all__ = [
    "Hackathon",
    "clean_tags",
    "clean_text",
    "extract_domain",
    "parse_date_range",
    "parse_location_and_mode",
    "parse_prize",
    "parse_single_date",
    "parse_team_size",
    "validate_record",
    "get_supabase_client",
    "get_supabase_credentials",
    "load_env",
    "deduplicate_records",
    "upsert_hackathons",
    "save_to_csv",
    "save_to_json",
    "print_banner",
    "print_summary_table",
    "setup_logging",
]
