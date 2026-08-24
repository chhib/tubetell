"""Minimal YouTube Data API v3 client — just enough for the comments mode."""

from __future__ import annotations

import os
import re

import requests

from . import TubetellError

API_ROOT = "https://www.googleapis.com/youtube/v3"


def load_api_key() -> str:
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise TubetellError(
            "YOUTUBE_API_KEY is not set — the comments mode reads comments via the "
            "YouTube Data API v3 and needs an API key. Export it, or put it in a "
            ".env file in the directory you run tubetell from."
        )
    return key


def _get(endpoint: str, params: dict, key: str) -> dict:
    resp = requests.get(f"{API_ROOT}/{endpoint}", params={**params, "key": key}, timeout=30)
    if resp.status_code != 200:
        raise TubetellError(f"YouTube API {endpoint} -> {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def video_id(url: str) -> str:
    """Pull the 11-char video id from a watch URL, youtu.be link, or bare id."""
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})", url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    raise TubetellError(f"Could not extract a video id from: {url}")


ISO_DURATION = re.compile(
    r"^P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$"
)


def parse_iso8601_duration(text: str) -> float | None:
    """`PT1H35M12S` as seconds — None for anything that isn't that shape.

    A live or upcoming broadcast reports `P0D`, which parses to 0 and is
    reported as None: unknown runtime, not a zero-length video.
    """
    m = ISO_DURATION.fullmatch(text.strip()) if text else None
    if not m:
        return None
    days, hours, minutes, seconds = (float(g or 0) for g in m.groups())
    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    return total or None


def fetch_duration(url: str) -> float | None:
    """A YouTube video's runtime in seconds, or None when it can't be had.

    Deliberately best effort: the video modes need no API key of their own, so a
    missing key, an unparseable URL, or a failed call just means the prompt goes
    out without a runtime to bound its timestamps against.
    """
    try:
        key = load_api_key()
        vid = video_id(url)
        data = _get("videos", {"part": "contentDetails", "id": vid}, key)
    except (TubetellError, requests.RequestException):
        return None
    items = data.get("items") or []
    if not items:
        return None
    return parse_iso8601_duration(items[0].get("contentDetails", {}).get("duration", ""))


def fetch_comments(url: str, limit: int) -> tuple[str, int]:
    """Top comments as a numbered text block + count, ordered by relevance."""
    key = load_api_key()
    vid = video_id(url)
    lines: list[str] = []
    page_token: str | None = None
    while len(lines) < limit:
        params = {
            "part": "snippet",
            "videoId": vid,
            "maxResults": min(100, limit - len(lines)),
            "order": "relevance",
        }
        if page_token:
            params["pageToken"] = page_token
        data = _get("commentThreads", params, key)
        for it in data.get("items", []):
            top = it["snippet"]["topLevelComment"]["snippet"]
            # textOriginal is plain text; textDisplay falls back but carries HTML (<br>).
            text = (top.get("textOriginal") or top.get("textDisplay") or "").replace("\n", " ").strip()
            likes = top.get("likeCount", 0)
            lines.append(f"{len(lines) + 1}. ({likes} likes) {text}")
            if len(lines) >= limit:
                break
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    if not lines:
        raise TubetellError("No comments returned (comments may be disabled on this video).")
    return "\n".join(lines), len(lines)
