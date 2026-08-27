"""tubetell — ask Gemini anything about a video: YouTube, gs://, or a local file."""

__version__ = "0.3.0"


class TubetellError(Exception):
    """User-facing error: configuration, API, or input problems."""
