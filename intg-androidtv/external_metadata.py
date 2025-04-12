import json
import os
import logging
from pathlib import Path
from typing import Dict

import requests
from PIL import Image
from io import BytesIO
import google_play_scraper

_LOG = logging.getLogger(__name__)

CACHE_ROOT = "external_cache"
ICON_SUBDIR = "icons"
ICON_SIZE = (90, 90)

# Paths
def get_cache_root() -> Path:
    config_home = Path(os.environ.get("UC_DATA_HOME", "./data"))
    cache_root = config_home / CACHE_ROOT
    cache_root.mkdir(parents=True, exist_ok=True)
    return cache_root

def get_metadata_dir() -> Path:
    metadata_dir = get_cache_root()
    metadata_dir.mkdir(parents=True, exist_ok=True)
    return metadata_dir

def get_icon_dir() -> Path:
    icon_dir = get_cache_root() / ICON_SUBDIR
    icon_dir.mkdir(parents=True, exist_ok=True)
    return icon_dir

def get_metadata_file_path() -> Path:
    return get_metadata_dir() / "app_metadata.json"

def get_icon_path(package_id: str) -> Path:
    return get_icon_dir() / f"{package_id}.png"

# Cache Management
def load_cache() -> Dict[str, Dict[str, str]]:
    path = get_metadata_file_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cache(cache: Dict[str, Dict[str, str]]) -> None:
    path = get_metadata_file_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


# Metadata Fetch
def download_and_resize_icon(url: str, package_id: str) -> str | None:
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        img = Image.open(BytesIO(response.content))
        img = img.resize(ICON_SIZE, Image.LANCZOS)

        icon_path = get_icon_path(package_id)
        img.save(icon_path, format="PNG")
        return str(icon_path)
    except Exception as e:
        _LOG.warning(f"Failed to fetch icon for {package_id}: {e}")
        return None

def get_app_metadata(package_id: str) -> Dict[str, str]:
    """
    Returns a dictionary with app name and icon path:
    {
        "name": "YouTube",
        "icon": "/absolute/path/to/youtube.png"
    }
    """
    cache = load_cache()
    if package_id in cache:
        return cache[package_id]

    try:
        app = google_play_scraper.app(package_id)
        name = app["title"]
        icon_url = app["icon"]

        icon_path = download_and_resize_icon(icon_url, package_id)
        metadata = {"name": name, "icon": icon_path or ""}

        cache[package_id] = metadata
        save_cache(cache)
        return metadata

    except Exception as e:
        _LOG.warning(f"Failed to fetch metadata for {package_id}: {e}")
        return {"name": package_id, "icon": ""}


# Shorthand Accessors
def get_app_name(package_id: str) -> str:
    """Shorthand to get just the app name."""
    return get_app_metadata(package_id).get("name", package_id)

def get_app_icon_path(package_id: str) -> str:
    """Shorthand to get just the cached icon path."""
    return get_app_metadata(package_id).get("icon", "")