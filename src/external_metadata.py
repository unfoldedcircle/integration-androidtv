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
from urllib.parse import urlparse, quote
import re

import google_play_scraper
import httpx
from PIL import Image
from PIL.Image import Resampling
from pychromecast.controllers.media import MediaImage
from sanitize_filename import sanitize
from config import _get_config_root, _get_data_root
from simplejustwatchapi import justwatch

_LOG = logging.getLogger(__name__)

CACHE_ROOT = "external_cache"
ICON_SUBDIR = "icons"
ICON_SIZE = (240, 240)


# Paths
def _get_metadata_dir() -> Path:
    metadata_dir = _get_data_root() / CACHE_ROOT
    metadata_dir.mkdir(parents=True, exist_ok=True)
    return metadata_dir


def _get_icon_dir() -> Path:
    icon_dir = _get_data_root() / CACHE_ROOT / ICON_SUBDIR
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
    parsed = urlparse(path)
    return parsed.scheme in ("http", "https")


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
    return {"name": "", "icon": ""}


async def youtube_search(query: str, limit: int = 1):
    url = f"https://www.youtube.com/results?search_query={quote(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    with httpx.Client(headers=headers, timeout=10) as client:
        response = client.get(url)
        html = response.text

    # Extract the ytInitialData JSON
    match = re.search(r"var ytInitialData = ({.*?});</script>", html)
    if not match:
        raise RuntimeError("Could not find ytInitialData in the page")

    data = json.loads(match.group(1))

    try:
        items = (
            data["contents"]["twoColumnSearchResultsRenderer"]
            ["primaryContents"]["sectionListRenderer"]
            ["contents"][0]["itemSectionRenderer"]["contents"]
        )
    except (KeyError, IndexError):
        raise RuntimeError("Could not parse YouTube data structure")

    for item in items:
        if "videoRenderer" in item:
            video = item["videoRenderer"]
            video_id = video.get("videoId")

            return f"https://img.youtube.com/vi/{video_id}/0.jpg"

    return None

async def search_poster_justwatch(query: str, country: str = "GB", limit: int = 1) -> list[dict]:
    """Search for poster images using JustWatch API."""

    response = justwatch.search(query, country, 'en', count=1, best_only=True)

    if not response[0].poster:
        return None
    else:
        poster_url = response[0].poster

        if poster_url:
            return poster_url

    return None

async def get_best_artwork(title: str, artist: str = None, current_package: str = None) -> Dict[str, str] | bool:
    _LOG.debug("Resolving best artwork for title='%s', artist='%s', current_package='%s'", title, artist, current_package)

    search_query = f"{title} - {artist}" if artist else title

    if current_package in ["com.google.android.youtube.tv", "com.liskovsoft.videomanager", "com.teamsmart.videomanager.tv"]:

        _LOG.debug("YouTube detected. Searching for artwork.")

        youtube = await youtube_search(search_query)

        if youtube:
            _LOG.debug("Artwork result:\n%s", json.dumps(youtube, indent=2))
            return youtube
        else:
            _LOG.debug("No artwork found from YouTube search.")

    else:

        _LOG.debug("Non-YouTube package detected. Searching for artwork.")
        justwatch = await search_poster_justwatch(search_query)

        if justwatch:
            _LOG.debug("Artwork result:\n%s", json.dumps(justwatch, indent=2))
            return justwatch
        else:
            _LOG.debug("No artwork found from JustWatch search.")

    _LOG.debug("No artwork source applicable. Returning False.")
    return None


# async def test():
#     posters = await get_best_artwork("Episode 1", "Breaking Bad", "com.plexapp.android")
#     print(posters)
#
# asyncio.run(test())