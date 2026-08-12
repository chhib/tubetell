"""Turn a source — YouTube URL, GCS URI, or local file — into a Gemini part.

Remote sources are references: Vertex fetches them itself, so the part is just
a URI. A local file has to travel in the request body, which Vertex caps at
20 MB — so local media goes through an ffmpeg proxy first (see `proxy`), and
the shrunken result is sent as inline bytes.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from google.genai import types

from . import TubetellError
from .youtube import video_id

# Vertex caps a generateContent request at 20 MB and inline bytes are
# base64-encoded on the wire (+33%), so 12 MiB of media is the most that
# reliably fits alongside the prompt.
INLINE_LIMIT = 12 * 1024 * 1024

# Gemini's supported media formats, by extension.
MIME_BY_SUFFIX = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".flv": "video/x-flv",
    ".wmv": "video/x-ms-wmv",
    ".3gp": "video/3gpp",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}

# Fallbacks tried in order when the requested proxy settings still overflow
# the inline cap: coarser frames, then a smaller frame.
PROXY_LADDER = ((960, 1.0), (640, 1.0), (480, 0.5))

def is_remote(source: str) -> bool:
    """True for sources Vertex fetches itself (YouTube page, GCS object)."""
    return source.startswith(("http://", "https://", "gs://"))


def looks_like_path(source: str) -> bool:
    """True when the source is meant as a local file, existing or not.

    A typo'd path should fail as a missing file, not get sent to Vertex as a
    video id — so anything with a directory separator or a known media
    extension counts, whether or not it is on disk.
    """
    if is_remote(source):
        return False
    p = Path(source).expanduser()
    return p.exists() or "/" in source or p.suffix.lower() in MIME_BY_SUFFIX


def mime_type(path: Path) -> str:
    mime = MIME_BY_SUFFIX.get(path.suffix.lower())
    if mime is None:
        raise TubetellError(
            f"Unsupported file type: {path.suffix or path.name}. Gemini reads "
            f"{', '.join(sorted(MIME_BY_SUFFIX))}."
        )
    return mime


def parse_offset(text: str) -> int:
    """Seconds from `ss`, `mm:ss`, or `hh:mm:ss`."""
    parts = text.strip().split(":")
    if not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts) or len(parts) > 3:
        raise TubetellError(f"Not a timestamp: {text!r}. Use ss, mm:ss, or hh:mm:ss.")
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + float(p)
    return int(seconds)


def parse_clip(text: str) -> tuple[int, int]:
    """`START-END` (each ss / mm:ss / hh:mm:ss) as a pair of second offsets."""
    if "-" not in text:
        raise TubetellError(f"--clip needs START-END, e.g. 1:30-2:45 (got {text!r}).")
    start_text, _, end_text = text.partition("-")
    start, end = parse_offset(start_text), parse_offset(end_text)
    if end <= start:
        raise TubetellError(f"--clip end must come after its start (got {text!r}).")
    return start, end


def probe(path: Path) -> dict:
    """Video width and duration via ffprobe; empty dict when it can't tell."""
    if shutil.which("ffprobe") is None:
        return {}
    cmd = [
        "ffprobe", "-v", "error", "-of", "json",
        "-select_streams", "v:0", "-show_entries", "stream=width",
        "-show_entries", "format=duration", str(path),
    ]
    try:
        out = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}
    streams = out.get("streams") or [{}]
    return {
        "width": streams[0].get("width"),
        "duration": float(out.get("format", {}).get("duration") or 0) or None,
    }


def _cache_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "tubetell-proxies"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _proxy_path(src: Path, width: int, fps: float, clip: tuple[int, int] | None) -> Path:
    """A cache path keyed by the source's identity and the proxy settings.

    Iterating on prompts against one video is the normal case, so a proxy is
    transcoded once and reused until the file itself changes. Identity is the
    inode rather than the path, so a moved file — or the same file reached
    through a differently-cased path on macOS — still hits its proxy.
    """
    stat = src.stat()
    key = "|".join(
        str(x)
        for x in (stat.st_dev, stat.st_ino, stat.st_size, int(stat.st_mtime), width, fps, clip)
    )
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return _cache_dir() / f"{src.stem[:40]}-{digest}.mp4"


def transcode(src: Path, dest: Path, *, width: int, fps: float, clip: tuple[int, int] | None) -> None:
    """Re-encode to a small H.264/AAC proxy: fewer frames, narrower, no more.

    Gemini samples video at ~1 fps and downscales anyway, so a 120 fps retina
    screen recording spends its bytes on detail the model never sees.
    """
    if shutil.which("ffmpeg") is None:
        raise TubetellError(
            "ffmpeg is not installed, so the file can't be shrunk to fit the request. "
            "Install it (brew install ffmpeg), or pass --no-transcode with a file "
            f"under {INLINE_LIMIT // 1024 // 1024} MiB."
        )
    scale = f"scale={width}:-2:flags=lanczos" if (probe(src).get("width") or 1 << 20) > width else "scale=trunc(iw/2)*2:-2"
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if clip:
        cmd += ["-ss", str(clip[0]), "-to", str(clip[1])]
    cmd += [
        "-i", str(src),
        "-vf", f"fps={fps:g},{scale}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k", "-ac", "1",
        "-movflags", "+faststart",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        dest.unlink(missing_ok=True)
        raise TubetellError(f"ffmpeg failed on {src.name}:\n{result.stderr.strip()[:500]}")


def _log(quiet: bool, message: str) -> None:
    if not quiet:
        print(message, file=sys.stderr)


def prepare_local(
    path: Path,
    *,
    transcode_enabled: bool = True,
    width: int = 1280,
    fps: float = 2.0,
    clip: tuple[int, int] | None = None,
    quiet: bool = False,
) -> tuple[bytes, str, tuple[int, int] | None]:
    """Read a local file as request-ready bytes, proxying it when oversized.

    Returns the bytes, their mime type, and the clip that still needs to be
    applied by Gemini — a proxy cuts the clip during transcode, so it comes
    back as None to keep the model from cutting it twice.
    """
    size = path.stat().st_size
    mime = mime_type(path)
    if not transcode_enabled:
        if size > INLINE_LIMIT:
            raise TubetellError(
                f"{path.name} is {size / 1024 / 1024:.0f} MiB — over the "
                f"{INLINE_LIMIT // 1024 // 1024} MiB a request can carry. Drop "
                "--no-transcode to let ffmpeg shrink it."
            )
        return path.read_bytes(), mime, clip

    if size <= INLINE_LIMIT:
        # Small enough to send whole; Gemini applies fps and clip on its side.
        return path.read_bytes(), mime, clip

    attempts = [(width, fps)] + [rung for rung in PROXY_LADDER if rung[0] < width]
    for attempt_width, attempt_fps in dict.fromkeys(attempts):
        proxy = _proxy_path(path, attempt_width, attempt_fps, clip)
        if not proxy.exists():
            _log(quiet, f"proxying {path.name} -> {attempt_width}px {attempt_fps:g}fps...")
            transcode(path, proxy, width=attempt_width, fps=attempt_fps, clip=clip)
        proxy_size = proxy.stat().st_size
        if proxy_size <= INLINE_LIMIT:
            _log(
                quiet,
                f"proxy: {proxy_size / 1024 / 1024:.1f} MiB "
                f"({attempt_width}px {attempt_fps:g}fps, from {size / 1024 / 1024:.0f} MiB)",
            )
            return proxy.read_bytes(), "video/mp4", None
        _log(quiet, f"  {proxy_size / 1024 / 1024:.1f} MiB is still too big; going coarser")

    duration = probe(path).get("duration")
    span = f" It runs {duration / 60:.0f} min." if duration else ""
    raise TubetellError(
        f"{path.name} won't fit in a request even at the lowest proxy settings.{span} "
        "Analyze it in pieces with --clip START-END."
    )


def source_part(
    source: str,
    *,
    transcode_enabled: bool = True,
    width: int = 1280,
    fps: float | None = None,
    clip: tuple[int, int] | None = None,
    quiet: bool = False,
) -> types.Part:
    """The media half of the request: a URI reference or inline bytes."""
    if looks_like_path(source):
        path = Path(source).expanduser()
        if not path.exists():
            raise TubetellError(f"No such file: {source}")
        if path.is_dir():
            raise TubetellError(f"{source} is a directory, not a media file.")
        data, mime, remaining_clip = prepare_local(
            path,
            transcode_enabled=transcode_enabled,
            width=width,
            fps=fps if fps is not None else 2.0,
            clip=clip,
            quiet=quiet,
        )
        part = types.Part.from_bytes(data=data, mime_type=mime)
        clip = remaining_clip
    elif source.startswith("gs://"):
        mime = MIME_BY_SUFFIX.get(Path(source).suffix.lower(), "video/*")
        part = types.Part(file_data=types.FileData(file_uri=source, mime_type=mime))
    else:
        # A YouTube URL, or a bare video id — Vertex wants the full watch URL.
        url = source if is_remote(source) else f"https://www.youtube.com/watch?v={video_id(source)}"
        part = types.Part(file_data=types.FileData(file_uri=url, mime_type="video/*"))

    metadata = video_metadata(fps=fps, clip=clip)
    if metadata is not None:
        part.video_metadata = metadata
    return part


def video_metadata(*, fps: float | None, clip: tuple[int, int] | None) -> types.VideoMetadata | None:
    """How much of the video to look at, and how densely — None when default."""
    if fps is None and clip is None:
        return None
    metadata = types.VideoMetadata(fps=fps)
    if clip:
        metadata.start_offset = f"{clip[0]}s"
        metadata.end_offset = f"{clip[1]}s"
    return metadata
