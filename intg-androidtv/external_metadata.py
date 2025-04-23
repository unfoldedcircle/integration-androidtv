"""
External metadata retrieval from Google Play.

:copyright: (c) 2025 by thomasm789, www.tmason.uk
:license: MPL-2.0, see LICENSE for more details.
"""

import asyncio
import base64
import json
import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

import google_play_scraper
import httpx
from PIL import Image
from PIL.Image import Resampling
from pychromecast.controllers.media import MediaImage

_LOG = logging.getLogger(__name__)

CACHE_ROOT = "external_cache"
ICON_SUBDIR = "icons"
ICON_SIZE = (120, 120)


# Paths
def _get_cache_root() -> Path:
    config_home = Path(os.environ.get("UC_DATA_HOME", "./data"))
    cache_root = config_home / CACHE_ROOT
    cache_root.mkdir(parents=True, exist_ok=True)
    return cache_root


def _get_metadata_dir() -> Path:
    metadata_dir = _get_cache_root()
    metadata_dir.mkdir(parents=True, exist_ok=True)
    return metadata_dir


def _get_icon_dir() -> Path:
    icon_dir = _get_cache_root() / ICON_SUBDIR
    icon_dir.mkdir(parents=True, exist_ok=True)
    return icon_dir


def _get_metadata_file_path() -> Path:
    return _get_metadata_dir() / "app_metadata.json"


def _get_icon_path(package_id: str) -> Path:
    return _get_icon_dir() / f"{package_id}.png"


# Cache Management
def _load_cache() -> Dict[str, Dict[str, str]]:
    path = _get_metadata_file_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_cache(cache: Dict[str, Dict[str, str]]) -> None:
    path = _get_metadata_file_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


# Metadata Fetch
async def _download_and_resize_icon_async(url: str, package_id: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            response.raise_for_status()
            img = Image.open(BytesIO(response.content))
            img = img.resize(ICON_SIZE, Resampling.LANCZOS)

            icon_path = _get_icon_path(package_id)
            img.save(icon_path, format="PNG")
            return str(icon_path)
    except Exception as e:
        _LOG.warning("Failed to fetch icon for %s: %s", package_id, e)
        return None


def encode_icon_to_data_uri(icon_path: str) -> str:
    if isinstance(icon_path, MediaImage):
        icon_path = icon_path.url

    if isinstance(icon_path, str) and icon_path.startswith("data:image"):
        return icon_path

    try:
        if _is_url(icon_path):
            response = requests.get(icon_path, timeout=10)
            response.raise_for_status()
            img = Image.open(BytesIO(response.content))
        else:
            with open(icon_path, "rb") as f:
                img = Image.open(f)
                img.load()

        img = img.convert("RGBA")
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{encoded}"

    except Exception as e:
        _LOG.warning("Failed to encode icon to base64 for %s: %s", icon_path, e)
        return ""


def _is_url(path: str) -> bool:
    parsed = urlparse(path)
    return parsed.scheme in ("http", "https")


async def _fetch_google_play_metadata_async(package_id: str) -> Dict[str, str] | None:
    try:
        app = await asyncio.to_thread(google_play_scraper.app, package_id)
        name = app["title"]
        icon_url = app["icon"]
        icon_path = await _download_and_resize_icon_async(icon_url, package_id)
        return {"name": name, "icon": icon_path or ""}
    except Exception as e:
        _LOG.warning("Google Play metadata fetch failed for %s: %s", package_id, e)
        return None


async def get_app_metadata_async(package_id: str) -> Dict[str, str]:
    cache = _load_cache()
    if package_id in cache:
        icon_path = cache[package_id].get("icon")
        icon_data_uri = encode_icon_to_data_uri(icon_path) if icon_path else ""
        return {"name": cache[package_id]["name"], "icon": icon_data_uri}

    metadata = await _fetch_google_play_metadata_async(package_id)
    if metadata:
        cache[package_id] = metadata
        _save_cache(cache)
        icon_data_uri = (
            encode_icon_to_data_uri(metadata["icon"]) if metadata["icon"] else ""
        )
        return {"name": metadata["name"], "icon": icon_data_uri}

    return {"name": package_id, "icon": ""}
