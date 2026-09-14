"""Shared Supabase Client Manager.

Handles environment configuration loading, credentials verification,
and cached client initialization.
Adheres strictly to:
- Never printing secrets or keys in logs.
- Treating network or DNS errors as connectivity failures.
- Supporting native fallback .env parsing when python-dotenv is not installed.
"""

import logging
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger("hackathon_scraper.supabase")

_cached_client = None


def load_env(env_path: Optional[str] = None) -> Dict[str, str]:
    """Load environment variables from a .env file into os.environ.

    Supports python-dotenv if available, with robust native fallback.
    Searches current directory, parent directory, and workspace root.
    """
    # 1. Try python-dotenv
    try:
        from dotenv import load_dotenv
        if env_path and os.path.exists(env_path):
            load_dotenv(env_path, override=False)
        else:
            load_dotenv(override=False)
    except ImportError:
        pass

    loaded = {}
    search_paths = []
    if env_path:
        search_paths.append(Path(env_path))

    # Add standard root and scraper locations
    cwd = Path.cwd()
    search_paths.extend([
        cwd / ".env",
        cwd.parent / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ])

    for p in search_paths:
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k not in os.environ:
                            os.environ[k] = v
                        loaded[k] = v
                break  # Stop at first found valid .env
            except Exception as e:
                logger.debug("Failed reading %s: %s", p, e)

    return loaded


def get_supabase_credentials() -> Tuple[Optional[str], Optional[str], str]:
    """Retrieve validated Supabase URL, Key, and Table name from environment.

    Never logs the actual key.
    """
    load_env()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    table = os.getenv("SUPABASE_TABLE", "hackathons")

    if url:
        url = url.strip().rstrip("/")
    if key:
        key = key.strip()

    return url, key, table


def get_supabase_client(force_new: bool = False):
    """Obtain an authenticated Supabase client instance.

    Returns the client, or None if credentials are missing or supabase library is unavailable.
    """
    global _cached_client
    if _cached_client is not None and not force_new:
        return _cached_client

    url, key, _ = get_supabase_credentials()
    if not url or not key or "your-project" in url:
        logger.warning(
            "[SUPABASE] Missing or placeholder credentials in .env. "
            "Set SUPABASE_URL and SUPABASE_KEY to enable database synchronization."
        )
        return None

    try:
        from supabase import create_client, ClientOptions
    except ImportError:
        logger.warning("[SUPABASE] The 'supabase' library is not installed. Database sync disabled.")
        return None

    try:
        # Create client with standard options
        client = create_client(url, key)
        _cached_client = client
        return client
    except Exception as exc:
        logger.warning(
            "[SUPABASE] Connectivity error during client initialization to %s: %s",
            url,
            exc,
        )
        return None
