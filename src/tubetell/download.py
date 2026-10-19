"""Fetch a video from a page Vertex can't read — X, Vimeo, anything yt-dlp knows.

Vertex reads a YouTube URL by reference, but a tweet or a Vimeo page is just
HTML to it. For those, yt-dlp pulls the media to a cache directory and the rest
of tubetell treats it as a local file. The post's own text comes along in the
info JSON and stands in for a YouTube description as the speaker roster.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from . import TubetellError

YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")

# The proxy is 1280px at most and usually ends up at 640px, so a 1080p source
# only costs download time. X serves HLS in small fragments, which yt-dlp pulls
# one at a time unless told otherwise — an hour at 1080p took nine minutes.
FORMAT = "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b"
CONCURRENT_FRAGMENTS = "8"


def is_downloadable(source: str) -> bool:
    """True for an http(s) page that isn't YouTube — one yt-dlp must fetch first."""
    if not source.startswith(("http://", "https://")):
        return False
    host = (urlparse(source).hostname or "").lower()
    return not any(host == h or host.endswith("." + h) for h in YOUTUBE_HOSTS)


def _cache_dir(url: str) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:12]
    d = Path(tempfile.gettempdir()) / "tubetell-downloads" / digest
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cached(d: Path) -> Path | None:
    for p in sorted(d.glob("video.*")):
        if p.suffix not in (".json", ".part", ".ytdl") and ".part" not in p.name:
            return p
    return None


def _description(d: Path) -> str | None:
    info = d / "video.info.json"
    if not info.exists():
        return None
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("description") or data.get("title")


def fetch(url: str) -> tuple[Path, str | None]:
    """Download a page's video once (cached by URL); return its path and post text."""
    d = _cache_dir(url)
    path = _cached(d)
    if path is None:
        if shutil.which("yt-dlp") is None:
            raise TubetellError(
                "This URL isn't YouTube, so tubetell has to download the video first "
                "and that needs yt-dlp. Install it (brew install yt-dlp), or download "
                "the file yourself and pass its path."
            )
        print(f"downloading {url} with yt-dlp...", file=sys.stderr)
        cmd = [
            "yt-dlp", "--quiet", "--no-warnings", "--no-playlist",
            "-f", FORMAT, "--merge-output-format", "mp4",
            "--concurrent-fragments", CONCURRENT_FRAGMENTS,
            "--write-info-json", "-o", str(d / "video.%(ext)s"), url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        path = _cached(d)
        if result.returncode != 0 or path is None:
            raise TubetellError(
                f"yt-dlp could not download {url}:\n{result.stderr.strip()[:500]}"
            )
        print(f"downloaded: {path} ({path.stat().st_size / 1024 / 1024:.0f} MiB)", file=sys.stderr)
    return path, _description(d)
