import base64
import json
import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

import google_play_scraper
import requests
from PIL import Image

_LOG = logging.getLogger(__name__)

CACHE_ROOT = "external_cache"
ICON_SUBDIR = "icons"
ICON_SIZE = (120, 120)


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


def encode_icon_to_data_uri(icon_path: str) -> str:
    """
    Accepts a local file path or remote URL.
    Returns a base64-encoded PNG data URI.
    """
    try:
        if is_url(icon_path):
            response = requests.get(icon_path, timeout=10)
            response.raise_for_status()
            img = Image.open(BytesIO(response.content))
        else:
            with open(icon_path, "rb") as f:
                img = Image.open(f)
                img.load()  # Ensure the image is fully loaded before the file is closed

        img = img.convert("RGBA")

        buffer = BytesIO()
        img.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{encoded}"

    except Exception as e:
        _LOG.warning(f"Failed to encode icon to base64 for {icon_path}: {e}")
        return ""


def is_url(path: str) -> bool:
    parsed = urlparse(path)
    return parsed.scheme in ("http", "https")


def fetch_google_play_metadata(package_id: str) -> Dict[str, str] | None:
    try:
        app = google_play_scraper.app(package_id)

        name = app["title"]
        icon_url = app["icon"]
        icon_path = download_and_resize_icon(icon_url, package_id)

        return {"name": name, "icon": icon_path or ""}

    except Exception as e:
        _LOG.warning(f"Google Play metadata fetch failed for {package_id}: {e}")
        return None


def get_app_metadata(package_id: str) -> Dict[str, str]:
    cache = load_cache()
    if package_id in cache:
        icon_path = cache[package_id].get("icon")
        icon_data_uri = encode_icon_to_data_uri(icon_path) if icon_path else ""
        return {"name": cache[package_id]["name"], "icon": icon_data_uri}

    # Try Google Play
    metadata = fetch_google_play_metadata(package_id)
    # if not metadata:
    # Additional Fallback option for the future maybe APKPure or another source
    # metadata = fetch_fallback_metadata(package_id)

    if metadata:
        cache[package_id] = metadata
        save_cache(cache)
        icon_data_uri = (
            encode_icon_to_data_uri(metadata["icon"]) if metadata["icon"] else ""
        )
        return {"name": metadata["name"], "icon": icon_data_uri}

    return {"name": package_id, "icon": ""}
