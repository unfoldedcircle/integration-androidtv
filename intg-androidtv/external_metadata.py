import json
import os
import logging
from pathlib import Path
from typing import Dict

import google_play_scraper

# ------------------------
# Cache
# ------------------------

CACHE_FILENAME = "app_dict.json"
CACHE_SUBDIR = "external_metadata_cache"


def get_cache_file_path() -> Path:
    config_home = Path(os.environ.get("UC_DATA_HOME", "./data"))
    cache_dir = config_home / CACHE_SUBDIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / CACHE_FILENAME


def load_cache() -> Dict[str, str]:
    path = get_cache_file_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache: Dict[str, str]) -> None:
    path = get_cache_file_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


# ------------------------
# App Name Logic
# ------------------------


def get_app_name(package_id: str) -> str:
    """
    Returns the friendly app name for a given package ID.
    Uses cache first, then online lookup if enabled, then falls back to package ID.
    """
    cache = load_cache()

    if package_id in cache:
        return cache[package_id]

    try:
        app = google_play_scraper.app(package_id)
        name = app["title"]
        cache[package_id] = name
        save_cache(cache)
        return name
    except Exception as e:
        logging.warning(f"Failed to fetch app name for {package_id}: {e}")
        return package_id