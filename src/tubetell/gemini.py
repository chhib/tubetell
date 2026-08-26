"""Gemini-on-Vertex client, mode prompt presets, and the retry wrapper."""

from __future__ import annotations

import os
import sys
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from . import TubetellError

MODE_PROMPTS = {
    "summary": (
        "Analyze this video. Give: (1) a 3-sentence summary, (2) the key points or "
        "arguments as a bullet list, (3) any notable claims, people, or entities "
        "mentioned, and (4) the overall tone. Be specific and concise."
    ),
    "transcript": (
        "Produce a transcript of this entire video with timestamps. Format every "
        "segment on its own line as `[mm:ss] text`, attributing the speaker when "
        "identifiable (`[mm:ss] Name: text`). Transcribe — do not summarize — and "
        "cover the video end to end."
    ),
    "claims": (
        "List every concrete, checkable factual claim made in this video. For each, "
        "give `[mm:ss]` timestamp, the claim (verbatim or close paraphrase), and who "
        "said it. Be exhaustive and keep them in chronological order."
    ),
    "sentiment": (
        "Analyze the tone and sentiment of this video. Cover: the overall sentiment, "
        "how it shifts across the runtime (mark notable shifts with `[mm:ss]`), each "
        "speaker's attitude, and any emotionally charged or tense moments."
    ),
}

COMMENTS_PROMPT = (
    "You are given the COMPLETE set of comments fetched for a YouTube video — {n} "
    "comment(s), listed below. Analyze audience sentiment using ONLY these comments. "
    "Hard rules: do not invent, assume, or paraphrase in any comment that is not in "
    "the list; every quote must be verbatim from the list; if the sample is small "
    "(under ~5 comments) say so explicitly and DO NOT report percentage splits — just "
    "summarize the comments present. Otherwise give: the rough split (positive / "
    "negative / neutral as approximate %), recurring themes, the most common praise "
    "and criticism, and any notably controversial reactions, each backed by a verbatim "
    "quote. Quote comments as plain list items — no label or prefix like "
    "'Verbatim Quote:' before them. For example:\n"
    '- "This has quickly become my favourite cooking channel."\n'
    'not:\n- Verbatim Quote: "This has quickly become my favourite cooking channel."'
    "\n\n--- COMMENTS ({n} total) ---\n"
)


def make_client() -> genai.Client:
    """Vertex-mode client from env config.

    Auth is handled by google.auth: a service-account key if
    GOOGLE_APPLICATION_CREDENTIALS is set, otherwise local ADC
    (`gcloud auth application-default login`).
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    if not project:
        raise TubetellError(
            "GOOGLE_CLOUD_PROJECT is not set. Export it, or put it in a .env file "
            "in the directory you run tubetell from, or in ~/.config/tubetell/.env."
        )
    return genai.Client(vertexai=True, project=project, location=location)


def format_usage(usage) -> str | None:
    """One-line token summary from a response's usage_metadata, or None."""
    if usage is None:
        return None
    prompt = usage.prompt_token_count or 0
    thoughts = getattr(usage, "thoughts_token_count", 0) or 0
    out = usage.candidates_token_count or 0
    total = usage.total_token_count or 0
    parts = [f"{prompt:,} in"]
    if thoughts:
        parts.append(f"{thoughts:,} thinking")
    parts.append(f"{out:,} out")
    return f"tokens: {' + '.join(parts)} = {total:,} total"


def generate(client: genai.Client, *, model: str, contents) -> str:
    """generate_content with backoff on transient 500/503s.

    Vertex re-fetches a YouTube URL on every call; hammering the same video in a
    short window gets it rate-limited downstream, surfaced as a 500 INTERNAL.
    Backing off and retrying clears it.
    """
    delay = 4.0
    last: Exception | None = None
    for attempt in range(4):
        try:
            resp = client.models.generate_content(model=model, contents=contents)
            usage = format_usage(getattr(resp, "usage_metadata", None))
            if usage:
                print(usage, file=sys.stderr)
            return resp.text or ""
        except genai_errors.ServerError as exc:  # 500/503 — transient
            last = exc
            if attempt < 3:
                print(
                    f"  transient {exc.code}; retrying in {delay:.0f}s "
                    f"(attempt {attempt + 1}/3)...",
                    file=sys.stderr,
                )
                time.sleep(delay)
                delay *= 2
    raise TubetellError(
        f"Vertex kept returning a server error: {last}\n"
        "Likely the video URL is being rate-limited from repeated fetches — "
        "wait a minute and retry, or try a different video."
    )


def video_contents(url: str, text: str) -> types.Content:
    """The video-mode request: the YouTube URL as a FileData part + the prompt.

    Vertex fetches and reads the video itself — no download, no transcript step.
    """
    return types.Content(
        role="user",
        parts=[
            types.Part(file_data=types.FileData(file_uri=url, mime_type="video/*")),
            types.Part(text=text),
        ],
    )


def comments_body(comments: str, n: int, prompt: str | None) -> str:
    """The comments-mode request body: prompt (custom or preset) + comment block."""
    if prompt:
        return f"{prompt}\n\n--- COMMENTS ({n} total) ---\n{comments}"
    return COMMENTS_PROMPT.format(n=n) + comments
