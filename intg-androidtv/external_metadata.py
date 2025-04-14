import base64
import json
import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Dict

import google_play_scraper
import requests
from PIL import Image

_LOG = logging.getLogger(__name__)

CACHE_FILENAME = "app_dict.json"
CACHE_SUBDIR = "external_cache"
ICON_SIZE = (90, 90)


# Cache
def get_cache_file_path() -> Path:
    config_home = Path(os.environ.get("UC_DATA_HOME", "./data"))
    cache_dir = config_home / CACHE_SUBDIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / CACHE_FILENAME


def load_cache() -> Dict[str, Dict[str, str]]:
    path = get_cache_file_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache: Dict[str, Dict[str, str]]) -> None:
    path = get_cache_file_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


# Metadata Fetch
def get_app_metadata(package_id: str) -> dict:
    """
    Returns metadata for a package ID, including app name and base64-encoded icon (if available).
    """
    cache = load_cache()
    if package_id in cache:
        return cache[package_id]

    try:
        app = google_play_scraper.app(package_id)
        name = app.get("title")
        icon_url = app.get("icon")
        icon_data_uri = None

        if icon_url:
            try:
                response = requests.get(icon_url, timeout=5)
                response.raise_for_status()
                image = Image.open(BytesIO(response.content))
                image = image.resize(ICON_SIZE)
                buffered = BytesIO()
                image.save(buffered, format="PNG")
                encoded_icon = base64.b64encode(buffered.getvalue()).decode("utf-8")
                icon_data_uri = f"data:image/png;base64,{encoded_icon}"
            except Exception as e:
                logging.warning(f"Failed to process icon for {package_id}: {e}")

        metadata = {
            "name": name,
            "media_image_url": icon_data_uri,
        }

        cache[package_id] = metadata
        save_cache(cache)
        return metadata

    except Exception as e:
        logging.warning(f"Failed to fetch metadata for {package_id}: {e}")
        return {"name": package_id}
