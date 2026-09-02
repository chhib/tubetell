"""The agentic path: Gemini's Interactions API on the Developer API.

Where the static path (gemini.py) ships frames to Vertex and pays for every
one of them, an interaction with `processing: "agentic"` lets the model fetch
and scrub through the video itself, so a long video costs a fraction of the
tokens. YouTube URLs go in as-is; a local file is first uploaded through the
Files API and deleted again once the answer is back.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from pathlib import Path

from google import genai

from . import TubetellError
from .config import load_gemini_api_key, user_config_path
from .media import mime_type, source_kind, youtube_url

MAX_OUTPUT_TOKENS = 65535

# Files API: how often to ask whether the upload is done, and when to give up.
POLL_INTERVAL = 2.0
PROCESSING_TIMEOUT = 5 * 60


def _message(exc: Exception) -> str:
    return getattr(exc, "message", None) or str(exc)


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def make_developer_client() -> genai.Client:
    """Developer-API client keyed by GEMINI_API_KEY.

    Explicit `vertexai=False` and an explicit key: a bare `genai.Client()`
    would honor GOOGLE_GENAI_USE_VERTEXAI and prefer GOOGLE_API_KEY.
    """
    key = load_gemini_api_key()
    if not key:
        raise TubetellError(
            "GEMINI_API_KEY is not set. Export it, or put it in a .env file in the "
            f"directory you run tubetell from, or in {user_config_path()}."
        )
    return genai.Client(vertexai=False, api_key=key)


def video_input(source: str, uploaded: tuple[str, str] | None = None) -> dict:
    """The video half of the request: a YouTube URL, or an uploaded file's URI."""
    if uploaded is not None:
        uri, mime = uploaded
        return {"type": "video", "uri": uri, "mime_type": mime, "processing": "agentic"}
    if source_kind(source) == "gs":
        raise TubetellError("agentic mode does not support Cloud Storage (gs://) sources.")
    return {"type": "video", "uri": youtube_url(source), "processing": "agentic"}


def format_usage_interaction(usage) -> str | None:
    """One-line token summary from an interaction's usage, or None."""
    if usage is None:
        return None
    counts = [
        getattr(usage, field, 0) or 0
        for field in (
            "total_input_tokens",
            "total_thought_tokens",
            "total_tool_use_tokens",
            "total_output_tokens",
            "total_tokens",
        )
    ]
    prompt, thoughts, tool, out, total = counts
    return (
        f"tokens: {prompt:,} in + {thoughts:,} thinking + {tool:,} tool + "
        f"{out:,} out = {total:,} total"
    )


def _interaction_error(resp) -> str | None:
    for err in getattr(resp, "errors", None) or []:
        if getattr(err, "message", None):
            return err.message
    for s in getattr(resp, "steps", None) or []:
        err = getattr(s, "error", None)
        if err is not None and getattr(err, "message", None):
            return err.message
    return None


def _answer_text(resp) -> str:
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text:
        return text
    pieces = []
    for s in getattr(resp, "steps", None) or []:
        if getattr(s, "type", None) != "model_output":
            continue
        for part in getattr(s, "content", None) or []:
            t = getattr(part, "text", None)
            if t:
                pieces.append(t)
    return "".join(pieces)


def run(client: genai.Client, *, model: str, video: dict, text: str) -> str:
    """One agentic interaction: the video input plus the prompt, answer text back.

    The SDK retries transient failures itself, so there is no backoff loop
    here; whatever it still raises is wrapped, since its error classes do not
    all subclass genai.errors.APIError.
    """
    try:
        resp = client.interactions.create(
            model=model,
            input=[video, {"type": "text", "text": text}],
            generation_config={"max_output_tokens": MAX_OUTPUT_TOKENS},
        )
    except TubetellError:
        raise
    except Exception as exc:
        raise TubetellError(f"Gemini API error: {_message(exc)}") from exc

    status = getattr(resp, "status", None)
    if status in ("failed", "cancelled"):
        raise TubetellError(
            f"Gemini interaction {status}: {_interaction_error(resp) or status}"
        )
    usage = format_usage_interaction(getattr(resp, "usage", None))
    if usage:
        print(usage, file=sys.stderr)
    if status in ("incomplete", "budget_exceeded"):
        print(
            "  warning: the answer hit the output cap and was cut off — "
            "analyze a shorter span with --clip START-END for the rest.",
            file=sys.stderr,
        )
    answer_text = _answer_text(resp)
    if not answer_text:
        raise TubetellError("the model returned no answer")
    return answer_text


def _state(file) -> str:
    state = getattr(file, "state", None)
    return str(getattr(state, "name", None) or state or "")


def _wait_active(client, file, label: str):
    deadline = time.monotonic() + PROCESSING_TIMEOUT
    announced = False
    while _state(file) == "PROCESSING":
        if not announced:
            _log("processing...")
            announced = True
        if time.monotonic() >= deadline:
            raise TubetellError(
                f"Gemini was still processing {label} after "
                f"{PROCESSING_TIMEOUT // 60} minutes; giving up."
            )
        time.sleep(POLL_INTERVAL)
        try:
            file = client.files.get(name=file.name)
        except Exception as exc:
            raise TubetellError(f"Could not check on {label}: {_message(exc)}") from exc
    if _state(file) != "ACTIVE":
        detail = getattr(getattr(file, "error", None), "message", None)
        raise TubetellError(
            f"Gemini could not process {label} (state {_state(file) or 'unknown'}"
            f"{': ' + detail if detail else ''})."
        )
    return file


@contextmanager
def uploaded_file(client: genai.Client, source: str):
    """Upload a local file to the Files API, yield `(uri, mime_type)`, delete it.

    The original goes up as-is — the Files API takes it, so nothing is
    proxied through ffmpeg. Deletion is best effort: files expire server-side
    after 48 h anyway, so a failed delete is a warning, not an error.
    """
    path = Path(source).expanduser()
    if not path.exists():
        raise TubetellError(f"No such file: {source}")
    if path.is_dir():
        raise TubetellError(f"{source} is a directory, not a media file.")
    mime = mime_type(path)
    _log(f"uploading {path.name} ({path.stat().st_size / 1024 / 1024:.0f} MB)...")
    try:
        file = client.files.upload(file=path, config={"mime_type": mime})
    except Exception as exc:
        raise TubetellError(f"Upload of {path.name} failed: {_message(exc)}") from exc
    try:
        file = _wait_active(client, file, path.name)
        yield file.uri, file.mime_type or mime
    finally:
        try:
            client.files.delete(name=file.name)
        except Exception as exc:
            _log(f"  warning: could not delete uploaded file {file.name}: {_message(exc)}")


def answer(source: str, *, model: str, text: str) -> str:
    """The whole agentic path for one source: client, upload if local, interact."""
    kind = source_kind(source)
    if kind == "gs":
        raise TubetellError("agentic mode does not support Cloud Storage (gs://) sources.")
    client = make_developer_client()
    if kind == "local":
        with uploaded_file(client, source) as uploaded:
            return run(client, model=model, video=video_input(source, uploaded), text=text)
    return run(client, model=model, video=video_input(source), text=text)
