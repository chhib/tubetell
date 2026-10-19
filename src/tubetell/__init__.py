"""tubetell — ask Gemini anything about a video: YouTube, gs://, a local file, or any page yt-dlp can download."""

__version__ = "0.5.0"


class TubetellError(Exception):
    """User-facing error: configuration, API, or input problems."""
