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
from typing import Dict, Literal
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import google_play_scraper
import httpx
from PIL import Image
from PIL.Image import Resampling
from pychromecast.controllers.media import MediaImage
from sanitize_filename import sanitize

_LOG = logging.getLogger(__name__)

CACHE_ROOT = "external_cache"
ICON_SUBDIR = "icons"
ICON_SIZE = (240, 240)
IMAGE_SIZE_MAX = 480


# Paths
def _get_config_root() -> Path:
    config_home = Path(os.environ.get("UC_CONFIG_HOME", "./config"))
    config_home.mkdir(parents=True, exist_ok=True)
    return config_home


def _get_cache_root() -> Path:
    data_home = Path(os.environ.get("UC_DATA_HOME", "./data"))
    cache_root = data_home / CACHE_ROOT
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


def _get_icon_name(package_id: str) -> str:
    return sanitize(f"{package_id}.png")


def _get_icon_path(icon_name: str) -> Path:
    if icon_name.startswith("config://"):
        return _get_config_root() / "icons" / sanitize(icon_name[9:])
    return _get_icon_dir() / sanitize(icon_name)


# Cache Management
def _load_cache() -> Dict[str, Dict[str, str]]:
    path = _get_metadata_file_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                cache = json.load(f)
                _LOG.debug("Loaded metadata cache with %d entries", len(cache))
                return cache
        except Exception as e:
            _LOG.warning("Failed to load metadata cache: %s", e)
            return {}
    _LOG.debug("Metadata cache file does not exist")
    return {}


def _save_cache(cache: Dict[str, Dict[str, str]]) -> None:
    path = _get_metadata_file_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
    _LOG.debug("Saved metadata cache with %d entries", len(cache))


# Metadata Fetch
async def _download_and_resize_icon(url: str, package_id: str) -> str | None:
    _LOG.debug("Downloading and resizing icon for %s from %s", package_id, url)
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10)
            response.raise_for_status()

        img_bytes = BytesIO(response.content)

        def resize_image() -> str:
            img = Image.open(img_bytes)
            img = img.resize(ICON_SIZE, Resampling.LANCZOS)
            icon_name = _get_icon_name(package_id)
            icon_path = _get_icon_path(icon_name)
            img.save(icon_path, format="PNG")
            _LOG.debug("Saved resized icon %s", icon_name)
            return icon_name

        filename = await asyncio.to_thread(resize_image)
        return filename

    except Exception as e:
        _LOG.warning("Failed to fetch icon for %s: %s", package_id, e)
        return None


async def encode_icon_to_data_uri(icon_name: str) -> str:
    """
    Encode an image from a local file path or remote URL.

    Returns a base64-encoded PNG data URI.
    """
    if isinstance(icon_name, MediaImage):
        icon_name = icon_name.url

    if isinstance(icon_name, str) and icon_name.startswith("data:image"):
        _LOG.debug("Icon is already a data URI")
        return icon_name

    _LOG.debug("Encoding icon to data URI: %s", icon_name)
    try:
        if _is_url(icon_name):
            async with httpx.AsyncClient() as client:
                response = await client.get(icon_name, timeout=10)
                response.raise_for_status()
                img_bytes = BytesIO(response.content)

            def encode_image() -> str:
                img = Image.open(img_bytes)
                img = img.convert("RGBA")
                buffer = BytesIO()
                img.save(buffer, format="PNG")
                encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
                return f"data:image/png;base64,{encoded}"

            return await asyncio.to_thread(encode_image)

        def load_and_encode() -> str:
            icon_path = _get_icon_path(icon_name)
            if not icon_path.exists():
                raise FileNotFoundError(f"Icon not found: {icon_name}")
            with open(icon_path, "rb") as f:
                img = Image.open(f)
                img.load()
                img = img.convert("RGBA")
                buffer = BytesIO()
                img.save(buffer, format="PNG")
                encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
                return f"data:image/png;base64,{encoded}"

        return await asyncio.to_thread(load_and_encode)

    except Exception as e:
        _LOG.warning("Failed to encode icon to base64 for %s: %s", icon_name, e)
        return ""


def _is_url(path: str) -> bool:
    """Check if the provided string is a valid http/https URL."""
    try:
        parsed = urlparse(path)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except (ValueError, AttributeError):
        return False


async def _fetch_google_play_metadata(package_id: str) -> Dict[str, str] | None:
    if not package_id:
        return None
    _LOG.debug("Fetching metadata for %s from Google Play", package_id)
    try:
        app = await asyncio.to_thread(google_play_scraper.app, package_id)

        name = app["title"]
        icon_url = app["icon"]
        icon_name = await _download_and_resize_icon(icon_url, package_id)

        _LOG.debug("Fetched metadata for %s: name='%s', icon='%s'", package_id, name, icon_name)
        return {"name": name, "icon": icon_name or ""}

    except Exception as e:
        _LOG.warning("Google Play metadata fetch failed for %s: %s", package_id, e)
        return None


async def get_app_metadata(package_id: str) -> Dict[str, str]:
    """
    Fetch metadata for a mobile application specified by the package ID.

    The metadata includes the application name and its icon encoded as a data URI.
    If metadata is found in a locally cached source, it is fetched from the cache.
    Otherwise, metadata is retrieved from external sources such as Google Play.

    :param package_id: The unique package identifier for the application.
    :type package_id: str
    :return: A dictionary containing the application's metadata. The dictionary
             includes the 'name' of the application and the 'icon', which is the
             application's icon encoded as a data URI. If no metadata is found,
             it returns the package ID as the name and an empty string as the icon.
    :rtype: Dict[str, str]
    """
    _LOG.debug("Getting app metadata for %s", package_id)
    cache = _load_cache()
    if package_id in cache:
        _LOG.debug("Cache hit for %s", package_id)
        icon_name = cache[package_id].get("icon")
        icon_data_uri = await encode_icon_to_data_uri(icon_name) if icon_name else ""
        return {"name": cache[package_id]["name"], "icon": icon_data_uri}

    _LOG.debug("Cache miss for %s", package_id)
    metadata = await _fetch_google_play_metadata(package_id)

    if metadata:
        cache[package_id] = metadata
        _save_cache(cache)
        icon_data_uri = await encode_icon_to_data_uri(metadata["icon"]) if metadata["icon"] else ""
        return {"name": metadata["name"], "icon": icon_data_uri}

    _LOG.debug("Falling back to default metadata for %s", package_id)
    return {"name": package_id, "icon": ""}


def get_resized_image_url(url: str, max_size: int = IMAGE_SIZE_MAX) -> str | Literal[b""]:
    """
    Adjust width and height query parameters while maintaining aspect ratio.

    Only performs the rewrite if the URL path contains '/photo/:/transcode' (Plex transcode URL).
    - If parameters are missing: returns original URL.
    - If parameters are invalid (non-numeric/<=0): returns original URL.
    - If one parameter exists: sets it to max_size.
    - If both exist: resizes proportionally to fit within max_size.
    """
    if not url or not _is_url(url):
        _LOG.warning("Invalid URL provided for resizing: %s", url)
        return url

    parsed_url = urlparse(url)

    # Only rewrite Plex transcode URLs
    # Other services can be added later
    if "/photo/:/transcode" not in parsed_url.path:
        return url

    query_dict = parse_qs(parsed_url.query, keep_blank_values=True)

    width_str = query_dict.get("width", [None])[0]
    height_str = query_dict.get("height", [None])[0]

    # Helper to validate and convert numeric strings
    def _safe_int(val):
        try:
            res = int(val)
            return res if res > 0 else None
        except (TypeError, ValueError):
            return None

    w = _safe_int(width_str)
    h = _safe_int(height_str)

    # both parameters present
    if w is not None and h is not None:
        if w > max_size or h > max_size:
            if w > h:
                new_w = max_size
                new_h = max(1, int(h * (max_size / w)))
            else:
                new_h = max_size
                new_w = max(1, int(w * (max_size / h)))

            query_dict["width"] = [str(new_w)]
            query_dict["height"] = [str(new_h)]
        else:
            return url  # No resizing needed

    # only width present
    elif w is not None:
        query_dict["width"] = [str(max_size)]

    # only height present
    elif h is not None:
        query_dict["height"] = [str(max_size)]

    # If neither are valid/present, return original
    else:
        return url

    # Reconstruct the URL safely
    new_query = urlencode(query_dict, doseq=True)
    return urlunparse(parsed_url._replace(query=new_query))
