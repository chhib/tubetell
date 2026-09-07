"""Gemini-on-Vertex client, mode prompt presets, and the retry wrapper."""

from __future__ import annotations

import os
import sys
import time

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from . import TubetellError
from .media import format_offset

MODE_PROMPTS = {
    "summary": (
        "Analyze this video. Give: (1) a 3-sentence summary, (2) the key points or "
        "arguments as a bullet list, (3) any notable claims, people, or entities "
        "mentioned, and (4) the overall tone. Be specific and concise."
    ),
    "transcript": (
        "Produce a transcript of this entire video with timestamps. Format every "
        "segment on its own line as `[timestamp] text`, attributing the speaker "
        "when identifiable (`[timestamp] Name: text`). Transcribe — do not "
        "summarize — and cover the video end to end."
    ),
    "claims": (
        "List every concrete, checkable factual claim made in this video. For each, "
        "give its timestamp, the claim (verbatim or close paraphrase), and who said "
        "it. Be exhaustive and keep them in chronological order."
    ),
    "sentiment": (
        "Analyze the tone and sentiment of this video. Cover: the overall sentiment, "
        "how it shifts across the runtime (timestamp each notable shift), each "
        "speaker's attitude, and any emotionally charged or tense moments."
    ),
}

# Left to itself Gemini writes `mm:ss` and keeps writing it past the hour mark,
# so a moment at 1:47:00 comes back as `47:00` or `107:00` — and it will place a
# citation past the end of the video rather than admit it isn't sure. Every
# video-mode request carries this rule, custom --prompt runs included.
TIMESTAMP_RULE = (
    "Timestamp rules — these override any format used above: write a position in "
    "the first hour as `[mm:ss]` and a position from one hour on as `[h:mm:ss]`. "
    "Never write `[mm:ss]` for a position past 59:59, never let the minutes field "
    "exceed 59, and never mix the two formats in one answer. Every timestamp is an "
    "offset from the start of the media you were given{runtime}. Do not guess, "
    "round to a tidy-looking number, or extrapolate a position you did not "
    "observe: where you are unsure of the position, write the point without a "
    "timestamp instead of inventing one."
)

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


# Modes whose output is a chronological list: chunk answers just concatenate.
LIST_MODES = {"transcript", "claims"}

MERGE_PROMPT = (
    "Below are {n} answers to the same request, each covering one consecutive "
    "span of a single video (spans marked). Merge them into one answer to the "
    "original request as if you had watched the whole video: no repetition, no "
    "mention of the spans or of this merge step, all timestamps kept as they are.\n\n"
    "--- ORIGINAL REQUEST ---\n{request}\n\n--- ANSWERS ---\n{answers}"
)


def merge_body(request: str, answers: list[tuple[tuple[int, int], str]]) -> str:
    blocks = [
        f"[span {format_offset(a)}-{format_offset(b)}]\n{text}" for (a, b), text in answers
    ]
    return MERGE_PROMPT.format(n=len(answers), request=request, answers="\n\n".join(blocks))


def timestamp_rule(duration: float | None = None) -> str:
    """The timestamp rule, bounded by the real runtime when we know it."""
    if duration and duration > 0:
        runtime = (
            f", which runs {format_offset(duration)} — no timestamp may be later "
            "than that"
        )
    else:
        runtime = ", and no timestamp may be later than its end"
    return TIMESTAMP_RULE.format(runtime=runtime)


def video_body(text: str, duration: float | None = None) -> str:
    """A video-mode prompt — preset or custom — with the timestamp rule appended."""
    return f"{text}\n\n{timestamp_rule(duration)}"


def make_client(model: str | None = None) -> genai.Client:
    """Vertex-mode client from env config.

    Auth is handled by google.auth: a service-account key if
    GOOGLE_APPLICATION_CREDENTIALS is set, otherwise local ADC
    (`gcloud auth application-default login`).

    On Vertex the Gemini 3.x models are only served from `global`, so a regional
    GOOGLE_CLOUD_LOCATION left over from another project would 404 every call.
    Rather than fail, coerce those models to `global` and say so once.
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    if model and model.startswith("gemini-3") and location != "global":
        print(
            f"location: {location} -> global ({model} is only served from global)",
            file=sys.stderr,
        )
        location = "global"
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


# The most a single response may run to; Gemini 2.5 caps output at 65,536.
MAX_OUTPUT_TOKENS = 65535

TOKEN_LIMIT_HINT = (
    "The video is too long for one request at this sampling density. tubetell "
    "sizes requests from the runtime when it can establish it — set "
    "YOUTUBE_API_KEY so it can look the runtime up, or pass --fps 0.5 (or lower) "
    "or analyze a span with --clip START-END."
)


def request_config(*, low_res: bool = False) -> types.GenerateContentConfig:
    # No tools are ever declared, so AFC has nothing to do; disabling it also
    # keeps google-genai 2.21+ from logging an AFC warning on every call.
    cfg = types.GenerateContentConfig(
        max_output_tokens=MAX_OUTPUT_TOKENS,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    if low_res:
        cfg.media_resolution = types.MediaResolution.MEDIA_RESOLUTION_LOW
    return cfg


def _truncated(resp) -> bool:
    cands = getattr(resp, "candidates", None) or []
    reason = getattr(cands[0], "finish_reason", None) if cands else None
    return reason is not None and "MAX_TOKENS" in str(reason)


def generate(
    client: genai.Client, *, model: str, contents, low_res: bool = False
) -> str:
    """generate_content with backoff on transient server and transport errors.

    Two things fail intermittently and both clear on a retry: Vertex re-fetches
    a YouTube URL on every call, and hammering the same video in a short window
    gets it rate-limited downstream as a 500 INTERNAL; and a request carrying
    inline media sometimes has its connection dropped mid-upload.
    """
    delay = 4.0
    last: Exception | None = None
    for attempt in range(4):
        try:
            resp = client.models.generate_content(
                model=model, contents=contents, config=request_config(low_res=low_res)
            )
            usage = format_usage(getattr(resp, "usage_metadata", None))
            if usage:
                print(usage, file=sys.stderr)
            if _truncated(resp):
                print(
                    "  warning: the answer hit the output cap and was cut off — "
                    "analyze a shorter span with --clip START-END for the rest.",
                    file=sys.stderr,
                )
            return resp.text or ""
        except genai_errors.ClientError as exc:
            if exc.code == 400 and "token count exceeds" in str(exc.message or exc):
                raise TubetellError(f"Vertex AI error 400: {exc.message}\n{TOKEN_LIMIT_HINT}")
            raise
        except (genai_errors.ServerError, httpx.TransportError) as exc:  # transient
            last = exc
            if attempt < 3:
                label = getattr(exc, "code", None) or type(exc).__name__
                print(
                    f"  transient {label}; retrying in {delay:.0f}s "
                    f"(attempt {attempt + 1}/3)...",
                    file=sys.stderr,
                )
                time.sleep(delay)
                delay *= 2
    raise TubetellError(
        f"Vertex kept failing: {last}\n"
        "For a YouTube source this is usually the URL being rate-limited from "
        "repeated fetches; for a local file it is usually the upload being cut "
        "off. Wait a minute and retry."
    )


def media_contents(part: types.Part, text: str) -> types.Content:
    """The video-mode request: the media part (see media.py) + the prompt."""
    return types.Content(role="user", parts=[part, types.Part(text=text)])


def comments_body(comments: str, n: int, prompt: str | None) -> str:
    """The comments-mode request body: prompt (custom or preset) + comment block."""
    if prompt:
        return f"{prompt}\n\n--- COMMENTS ({n} total) ---\n{comments}"
    return COMMENTS_PROMPT.format(n=n) + comments
