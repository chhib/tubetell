"""The Azure path: a two-step pipeline on Azure AI Foundry (Azure OpenAI).

No Azure model reads video natively, so where the Gemini paths hand the model
the video itself, this one takes it apart first:

1. the audio track goes to a transcription deployment
   (`gpt-4o-transcribe-diarize`, `diarized_json`), cut into chunks that fit one
   request and stitched back onto the source's clock;
2. stills are sampled with ffmpeg — one every 10 s plus scene changes, capped —
   and each is tagged with its timestamp;
3. the timestamped transcript, the frames and the mode prompt go to a chat
   deployment that takes image input (`gpt-6.1-terra`).

`--mode transcript` without `--prompt` stops after step 1 and formats the
transcript itself. Everything stays in the Azure tenant: the media is cut up
locally, in a temporary directory deleted afterwards.

Auth is Entra ID by default (DefaultAzureCredential, so `az login` is enough
with the "Cognitive Services OpenAI User" role on the resource);
AZURE_OPENAI_API_KEY is a fallback when it is set.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import TubetellError
from .config import user_config_path
from .gemini import MODE_PROMPTS, video_body
from .transcript import (
    Segment,
    Transcript,
    cache_path,
    is_multipart,
    is_named,
    load_file,
    slice_segments,
)

TRANSCRIBE_DEPLOYMENT = "gpt-4o-transcribe-diarize"
CHAT_DEPLOYMENT = "gpt-6.1-terra"
SCOPE = "https://cognitiveservices.azure.com/.default"

# One transcription request takes at most 25 MB, and the diarize model has
# been reported to refuse audio past ~1400 s. 20-minute chunks of 32 kbps mono
# MP3 are ~4.8 MB, well under both; the overlap keeps a sentence cut at a
# boundary whole in one of the two chunks.
CHUNK_SECONDS = 1200
CHUNK_OVERLAP = 10
# With known speakers the labels need no reconciling across chunks, so the
# chunks can be short and run at the same time: 5 minutes takes ~100 s.
SHORT_CHUNK_SECONDS = 300
# How many transcription requests to run at once, from the deployment's
# requests-per-minute header; 2 when it says nothing.
PARALLEL_PER_RPM = 2000
MAX_PARALLEL = 8
DEFAULT_PARALLEL = 2
AUDIO_ARGS = ("-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k")

# Frames: one every FRAME_INTERVAL seconds, plus a scene change when the picture
# moves on (a new slide), but never two closer than SCENE_MIN_GAP.
FRAME_INTERVAL = 10.0
SCENE_THRESHOLD = 0.3
SCENE_MIN_GAP = 2.0
FRAME_WIDTH = 1024
# The most images one chat request carries. Each 1024px frame costs roughly a
# thousand input tokens at high detail, so 50 is ~50k tokens of pictures.
MAX_FRAMES = 50

REQUEST_TIMEOUT = 10 * 60


# --- configuration ---------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    endpoint: str
    transcribe_deployment: str
    chat_deployment: str
    api_key: str | None


def settings(chat_override: str | None = None) -> Settings:
    """Azure settings from the environment; fails clearly without an endpoint."""
    endpoint = (os.environ.get("AZURE_OPENAI_ENDPOINT") or "").strip()
    if not endpoint:
        raise TubetellError(
            "AZURE_OPENAI_ENDPOINT is not set. --provider azure needs your Azure AI "
            "Foundry resource, e.g. https://<resource>.cognitiveservices.azure.com — "
            "export it, or put it in a .env file in the directory you run tubetell "
            f"from, or in {user_config_path()}."
        )
    return Settings(
        endpoint=endpoint,
        transcribe_deployment=os.environ.get("AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT")
        or TRANSCRIBE_DEPLOYMENT,
        chat_deployment=chat_override
        or os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT")
        or CHAT_DEPLOYMENT,
        api_key=os.environ.get("AZURE_OPENAI_API_KEY") or None,
    )


def base_url(endpoint: str) -> str:
    """The resource's v1 API root, which takes no api-version parameter."""
    root = endpoint.rstrip("/")
    if root.endswith("/openai/v1"):
        return root + "/"
    return root + "/openai/v1/"


def make_client(cfg: Settings):
    """An OpenAI client on the resource's v1 API, Entra ID unless a key is set.

    The openai SDK calls a callable api_key before every request, so the
    bearer token provider refreshes the Entra token on its own. Neither the
    token nor the key is ever printed.
    """
    from openai import OpenAI

    if cfg.api_key:
        credential = cfg.api_key
    else:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        credential = get_bearer_token_provider(DefaultAzureCredential(), SCOPE)
    return OpenAI(
        base_url=base_url(cfg.endpoint),
        api_key=credential,
        timeout=REQUEST_TIMEOUT,
        max_retries=4,
    )


# The diarize model answers only on the classic deployment route
# (/openai/deployments/<name>/audio/transcriptions); /openai/v1/ gives 404
# DeploymentNotFound for it (seen live 2026-11-06). Chat works on v1.
TRANSCRIBE_API_VERSION = "2025-03-01-preview"


def resource_root(endpoint: str) -> str:
    """The resource URL without a trailing /openai/v1."""
    root = endpoint.rstrip("/")
    return root.removesuffix("/openai/v1").rstrip("/")


def make_transcribe_client(cfg: Settings):
    """A client on the classic deployment route, for step 1 only."""
    from openai import AzureOpenAI

    auth: dict = {"api_key": cfg.api_key}
    if not cfg.api_key:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        auth = {"azure_ad_token_provider": get_bearer_token_provider(DefaultAzureCredential(), SCOPE)}
    return AzureOpenAI(
        azure_endpoint=resource_root(cfg.endpoint),
        api_version=TRANSCRIBE_API_VERSION,
        timeout=REQUEST_TIMEOUT,
        max_retries=4,
        **auth,
    )


def _api_error(exc: Exception) -> TubetellError:
    """A clean message for an Azure failure; never includes the credential."""
    from openai import APIConnectionError, APIStatusError

    try:
        from azure.core.exceptions import ClientAuthenticationError
    except ImportError:  # pragma: no cover - azure-identity pulls azure-core in
        ClientAuthenticationError = ()  # type: ignore[assignment]

    if isinstance(exc, ClientAuthenticationError):
        return TubetellError(
            "Could not get an Entra ID token for Azure. Run `az login` (with the "
            "tenant that owns the resource), or set AZURE_OPENAI_API_KEY."
        )
    if isinstance(exc, APIStatusError):
        message = exc.message
        hint = ""
        if exc.status_code in (401, 403):
            hint = (
                "\nYour identity needs the \"Cognitive Services OpenAI User\" role on "
                "the Azure resource (a role assignment can take a few minutes to apply)."
            )
        elif exc.status_code == 404:
            hint = (
                "\nCheck AZURE_OPENAI_ENDPOINT and that the deployment name exists on "
                "that resource (AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT / "
                "AZURE_OPENAI_CHAT_DEPLOYMENT, or --model)."
            )
        return TubetellError(f"Azure OpenAI error {exc.status_code}: {message}{hint}")
    if isinstance(exc, APIConnectionError):
        return TubetellError(f"Could not reach Azure OpenAI: {exc}")
    return TubetellError(f"Azure OpenAI error: {exc}")


# --- timestamps --------------------------------------------------------------


def stamp(seconds: float) -> str:
    """`mm:ss` in the first hour, `h:mm:ss` from one hour on — tubetell's rule."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


# --- step 1: audio -> diarized transcript -------------------------------------


def plan_chunks(
    start: float, end: float, *, length: int = CHUNK_SECONDS, overlap: int = CHUNK_OVERLAP
) -> list[tuple[float, float]]:
    """Consecutive (start, end) audio chunks covering the span, each `overlap` s
    into the next. A span that fits one request is one chunk."""
    if end <= start:
        return []
    chunks = []
    a = start
    while True:
        b = min(a + length, end)
        chunks.append((a, b))
        if b >= end:
            return chunks
        a = b - overlap


def chunk_length(*, named: bool) -> int:
    """Short chunks when the speakers are named, long ones when their labels
    would have to be reconciled across chunks."""
    return SHORT_CHUNK_SECONDS if named else CHUNK_SECONDS


def parallelism(headers) -> int:
    """How many transcription requests to run at once for this deployment."""
    try:
        rpm = int(headers.get("x-ratelimit-limit-requests"))
    except (TypeError, ValueError):
        return DEFAULT_PARALLEL
    return max(1, min(MAX_PARALLEL, rpm // PARALLEL_PER_RPM))


def speaker_label(raw: str | None, part: int | None, known: tuple[str, ...] = ()) -> str:
    """An honest label for a diarizer label.

    A known speaker (one sent with a reference clip) comes back under its own
    name, which is stable across chunks, so it stays as it is. For the rest the
    diarizer names voices `A`, `B`, ... per request, so `A` in one chunk is
    not necessarily `A` in the next. With several chunks the label carries the
    chunk number (`Speaker 2-A`: voice A of part 2) instead of pretending they
    line up; reconciling voices across parts is left to the answer step.
    """
    raw = (raw or "").strip() or "?"
    if raw in known:
        return raw
    if part is None:
        return f"Speaker {raw}"
    return f"Speaker {part}-{raw}"


def _field(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def segments_from(
    response, *, offset: float, part: int | None, known: tuple[str, ...] = ()
) -> list[Segment]:
    """Segments of one diarized_json response, moved onto the source's clock."""
    out = []
    for seg in _field(response, "segments", None) or []:
        text = (_field(seg, "text", "") or "").strip()
        if not text:
            continue
        out.append(
            Segment(
                start=float(_field(seg, "start", 0.0) or 0.0) + offset,
                end=float(_field(seg, "end", 0.0) or 0.0) + offset,
                speaker=speaker_label(_field(seg, "speaker"), part, known),
                text=text,
            )
        )
    return out


def merge_chunks(
    chunks: list[tuple[float, float]], per_chunk: list[list[Segment]]
) -> list[Segment]:
    """One timeline from overlapping chunks, each segment kept exactly once.

    Each chunk owns its span up to the middle of each overlap; a segment is
    kept by the chunk whose owned range holds its midpoint, so a sentence that
    straddles a boundary is neither lost nor doubled.
    """
    merged: list[Segment] = []
    for i, ((a, b), segments) in enumerate(zip(chunks, per_chunk)):
        lo = (a + chunks[i - 1][1]) / 2 if i > 0 else float("-inf")
        hi = (chunks[i + 1][0] + b) / 2 if i + 1 < len(chunks) else float("inf")
        merged.extend(s for s in segments if lo <= (s.start + s.end) / 2 < hi)
    return sorted(merged, key=lambda s: s.start)


def format_transcript(segments: list[Segment]) -> str:
    """tubetell's transcript format: `[timestamp] Speaker: text`, one per line.

    Consecutive segments from the same voice stay separate lines; each keeps
    its own timestamp, which is what makes the transcript citable.
    """
    return "\n".join(f"[{stamp(s.start)}] {s.speaker}: {s.text}" for s in segments)


def _require(tool: str) -> None:
    if shutil.which(tool) is None:
        raise TubetellError(
            f"{tool} is not installed; --provider azure cuts the audio and frames "
            "locally with it. Install it (brew install ffmpeg)."
        )


def streams(path: Path) -> dict:
    """Which streams a file has, and its duration: {'audio', 'video', 'duration'}."""
    _require("ffprobe")
    cmd = [
        "ffprobe", "-v", "error", "-of", "json",
        "-show_entries", "stream=codec_type:stream_disposition=attached_pic",
        "-show_entries", "format=duration", str(path),
    ]
    try:
        out = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise TubetellError(f"ffprobe could not read {path.name}.") from exc
    kinds = [
        s.get("codec_type")
        for s in out.get("streams") or []
        if not (s.get("disposition") or {}).get("attached_pic")  # cover art
    ]
    return {
        "audio": "audio" in kinds,
        "video": "video" in kinds,
        "duration": float(out.get("format", {}).get("duration") or 0) or None,
    }


def audio_command(src: Path, dest: Path, chunk: tuple[float, float]) -> list[str]:
    a, b = chunk
    return [
        "ffmpeg", "-v", "error", "-y", "-ss", f"{a:g}", "-to", f"{b:g}",
        "-i", str(src), *AUDIO_ARGS, str(dest),
    ]


def _run(cmd: list[str], what: str) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise TubetellError(f"ffmpeg failed {what}:\n{result.stderr.strip()[:500]}")
    return result


@dataclass
class TranscriptionUsage:
    requests: int = 0
    seconds: float = 0.0
    tokens: int = 0

    def add(self, usage) -> None:
        self.requests += 1
        if usage is None:
            return
        if _field(usage, "type") == "tokens":
            self.tokens += int(_field(usage, "total_tokens", 0) or 0)
        else:
            self.seconds += float(_field(usage, "seconds", 0) or 0)

    def line(self) -> str:
        parts = []
        if self.seconds:
            parts.append(f"{self.seconds:,.0f} s audio")
        if self.tokens:
            parts.append(f"{self.tokens:,} tokens")
        what = " + ".join(parts) or "audio"
        n = self.requests
        return f"transcribed: {what} in {n:,} request{'s' if n != 1 else ''}"


def transcribe(
    client,
    cfg: Settings,
    src: Path,
    span: tuple[float, float],
    workdir: Path,
    usage: TranscriptionUsage,
    speakers: list = (),
) -> list[Segment]:
    """Step 1: the span's audio, chunked, transcribed with speakers, merged.

    `speakers` are known voices (tubetell.speakers.Speaker): their names and
    reference clips go with every request, and come back as the labels.
    """
    from concurrent.futures import ThreadPoolExecutor

    from .speakers import references

    known = tuple(s.name for s in speakers)
    chunks = plan_chunks(*span, length=chunk_length(named=bool(known)))
    several = len(chunks) > 1
    extra: dict = {}
    if speakers:
        extra = dict(known_speaker_names=list(known), known_speaker_references=references(speakers))
    files = []
    for i, chunk in enumerate(chunks, start=1):
        dest = workdir / f"audio-{i:03d}.mp3"
        _run(audio_command(src, dest, chunk), f"extracting audio from {src.name}")
        files.append(dest)

    def request(i: int):
        chunk, dest = chunks[i - 1], files[i - 1]
        label = f" {i}/{len(chunks)}" if several else ""
        print(
            f"transcribing{label} {stamp(chunk[0])}-{stamp(chunk[1])} "
            f"({dest.stat().st_size / 1024 / 1024:.1f} MB)...",
            file=sys.stderr,
        )
        try:
            with dest.open("rb") as handle:
                raw = client.audio.transcriptions.with_raw_response.create(
                    model=cfg.transcribe_deployment,
                    file=handle,
                    response_format="diarized_json",
                    chunking_strategy="auto",
                    **extra,
                )
        except TubetellError:
            raise
        except Exception as exc:
            raise _api_error(exc) from exc
        return raw

    # The first request alone: its headers say how many may run at once.
    responses = [request(1)]
    if several:
        at_once = parallelism(responses[0].headers)
        if at_once > 1 and len(chunks) > 2:
            print(f"  {len(chunks) - 1} more chunks, {at_once} at a time", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=at_once) as pool:
            responses.extend(pool.map(request, range(2, len(chunks) + 1)))
    per_chunk = []
    for i, (chunk, raw) in enumerate(zip(chunks, responses), start=1):
        resp = raw.parse()
        usage.add(_field(resp, "usage"))
        per_chunk.append(
            segments_from(resp, offset=chunk[0], part=i if several else None, known=known)
        )
    return merge_chunks(chunks, per_chunk)


def cached_transcript(
    cfg: Settings, src: Path, span: tuple[float, float], tag: str = ""
) -> list[Segment] | None:
    """The span's segments from the cache: the file for this exact span, or a
    slice of any cached span of the same deployment and tag that contains it."""
    exact = cache_path(src, span, cfg.transcribe_deployment, tag=tag)
    candidates = [exact] + sorted(exact.parent.glob("*.json"))
    for path in candidates:
        if not path.is_file():
            continue
        cached = Transcript.load(path)
        if (cached.deployment, cached.tag) != (cfg.transcribe_deployment, tag) or not cached.covers(span):
            continue
        segments = slice_segments(cached.segments, span)
        print(f"transcript: cached ({path}, {len(segments)} lines)", file=sys.stderr)
        return segments
    return None


def transcript_tag(speakers: list = (), glossary_terms: list[str] = ()) -> str:
    """The cache tag of a transcript made with these known speakers and this
    glossary: each part that is set, joined by "-"."""
    from .glossary import glossary_tag
    from .speakers import cache_tag

    return "-".join(t for t in (cache_tag(list(speakers)), glossary_tag(list(glossary_terms))) if t)


def transcribe_and_cache(
    cfg: Settings,
    src: Path,
    span: tuple[float, float],
    workdir: Path,
    usage: TranscriptionUsage,
    speakers: list = (),
    *,
    client=None,
    glossary_terms: list[str] = (),
) -> list[Segment]:
    from . import glossary

    segments = transcribe(make_transcribe_client(cfg), cfg, src, span, workdir, usage, speakers)
    print(usage.line(), file=sys.stderr)
    if glossary_terms:
        segments = glossary.correct(client, cfg, segments, list(glossary_terms))
    tag = transcript_tag(speakers, glossary_terms)
    path = cache_path(src, span, cfg.transcribe_deployment, tag=tag)
    Transcript(src.name, span, cfg.transcribe_deployment, segments, tag=tag).save(path)
    print(f"transcript: saved to {path}", file=sys.stderr)
    return segments


# --- step 2: frames -----------------------------------------------------------


def frame_select(fps: float | None) -> str:
    """The ffmpeg `select` expression: a fixed interval, plus scene changes.

    With --fps the caller asked for a fixed rate, so that is all it gets.
    """
    if fps:
        return f"isnan(prev_selected_t)+gte(t-prev_selected_t\\,{1 / fps:g})"
    return (
        f"isnan(prev_selected_t)+gte(t-prev_selected_t\\,{FRAME_INTERVAL:g})"
        f"+gt(scene\\,{SCENE_THRESHOLD:g})*gte(t-prev_selected_t\\,{SCENE_MIN_GAP:g})"
    )


def frames_command(
    src: Path, outdir: Path, span: tuple[float, float], *, fps: float | None, width: int = FRAME_WIDTH
) -> list[str]:
    a, b = span
    # Decoding at 2 fps before selecting keeps scene detection cheap on long
    # recordings; a higher --fps needs the frames it asks for.
    decode = max(2.0, fps or 0)
    vf = (
        f"fps={decode:g},select='{frame_select(fps)}',"
        f"scale='min({width},iw)':-2,showinfo"
    )
    return [
        "ffmpeg", "-v", "info", "-nostats", "-y", "-ss", f"{a:g}", "-to", f"{b:g}",
        "-i", str(src), "-vf", vf, "-fps_mode", "vfr", "-q:v", "4",
        str(outdir / "frame-%05d.jpg"),
    ]


PTS_TIME = re.compile(r"Parsed_showinfo.*?\bpts_time:\s*([\d.]+)")


def frame_times(showinfo_log: str, offset: float) -> list[float]:
    """Each written frame's position on the source's clock, from showinfo's log.

    Input seeking resets the clock to 0 at the span's start, hence the offset.
    """
    return [float(m.group(1)) + offset for m in PTS_TIME.finditer(showinfo_log)]


def thin(items: list, cap: int) -> list:
    """At most `cap` items, picked evenly across the list, first and last kept."""
    n = len(items)
    if n <= cap:
        return list(items)
    if cap <= 0:
        return []
    if cap == 1:
        return [items[0]]
    picks = sorted({round(i * (n - 1) / (cap - 1)) for i in range(cap)})
    return [items[i] for i in picks]


@dataclass(frozen=True)
class Frame:
    time: float
    path: Path


def sample_frames(
    src: Path, span: tuple[float, float], workdir: Path, *, fps: float | None, cap: int = MAX_FRAMES
) -> list[Frame]:
    """Step 2: JPEG stills across the span, each with its timestamp, at most `cap`."""
    outdir = workdir / "frames"
    outdir.mkdir()
    print(f"sampling frames {stamp(span[0])}-{stamp(span[1])}...", file=sys.stderr)
    result = _run(frames_command(src, outdir, span, fps=fps), f"sampling frames from {src.name}")
    files = sorted(outdir.glob("frame-*.jpg"))
    times = frame_times(result.stderr, span[0])
    frames = [Frame(t, p) for t, p in zip(times, files)]
    kept = thin(frames, cap)
    if len(kept) < len(frames):
        print(f"  {len(frames)} frames; keeping {len(kept)} spread evenly", file=sys.stderr)
    return kept


# --- step 3: the answer ---------------------------------------------------------


PIPELINE_NOTE = (
    "You cannot watch or hear this media directly. You are given what was taken "
    "from it: a timestamped transcript from a speech-to-text model, and still "
    "frames sampled from the video, each preceded by its timestamp. Answer from "
    "these alone. Timestamps in the transcript and on the frames are positions in "
    "the media; cite them as given. A name counts as heard in the audio only if it "
    "is spoken in the transcript, and as seen on screen only if it is legible in a "
    "frame."
)

VOICE_NOTE = (
    "The transcript's speaker labels come from an automatic diarizer that worked "
    "on the audio in separate parts, labelling the voices of part N as "
    "`Speaker N-A`, `Speaker N-B`, ... The same person usually has a different "
    "label in each part. Where the content makes it clear that two labels are the "
    "same voice (who answers whom, who introduced themselves, a continued "
    "sentence), give them one stable generic label; where it does not, keep them "
    "apart rather than guess."
)

NAMED_NOTE = (
    "A label that is a name (not `Speaker ...`) was matched to that person's "
    "voice by the diarizer from a reference recording; keep those names as they "
    "are, and reconcile only the generic labels."
)


def answer_text(
    request: str,
    transcript: str | None,
    *,
    duration: float | None,
    description: str | None,
    multipart: bool,
    has_frames: bool,
    named: bool = False,
) -> str:
    """The text half of the chat request: prompt, standing rules, the transcript."""
    parts = [video_body(request, duration, description), PIPELINE_NOTE]
    if multipart:
        parts.append(VOICE_NOTE)
    if named:
        parts.append(NAMED_NOTE)
    if transcript:
        parts.append(f"--- TRANSCRIPT ---\n{transcript}\n--- END TRANSCRIPT ---")
    else:
        parts.append("--- TRANSCRIPT ---\n(no speech was transcribed)\n--- END TRANSCRIPT ---")
    if has_frames:
        parts.append("The frames follow, in order, each after its timestamp.")
    return "\n\n".join(parts)


def _data_url(path: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()


def chat_content(text: str, frames: list[Frame], image_url=_data_url) -> list[dict]:
    """The user message: the text, then each frame preceded by its timestamp."""
    content: list[dict] = [{"type": "text", "text": text}]
    for f in frames:
        content.append({"type": "text", "text": f"[frame {stamp(f.time)}]"})
        content.append(
            {"type": "image_url", "image_url": {"url": image_url(f.path), "detail": "high"}}
        )
    return content


def format_chat_usage(usage) -> str | None:
    """The tokens line, in the Gemini paths' shape: in + thinking + out = total."""
    if usage is None:
        return None
    prompt = _field(usage, "prompt_tokens", 0) or 0
    completion = _field(usage, "completion_tokens", 0) or 0
    total = _field(usage, "total_tokens", 0) or (prompt + completion)
    details = _field(usage, "completion_tokens_details")
    reasoning = (_field(details, "reasoning_tokens", 0) or 0) if details is not None else 0
    parts = [f"{prompt:,} in"]
    if reasoning:
        parts.append(f"{reasoning:,} thinking")
    parts.append(f"{completion - reasoning:,} out")
    return f"tokens: {' + '.join(parts)} = {total:,} total"


def ask(client, cfg: Settings, content: list[dict]) -> str:
    try:
        resp = client.chat.completions.create(
            model=cfg.chat_deployment,
            messages=[{"role": "user", "content": content}],
        )
    except TubetellError:
        raise
    except Exception as exc:
        raise _api_error(exc) from exc
    line = format_chat_usage(_field(resp, "usage"))
    if line:
        print(line, file=sys.stderr)
    choice = (_field(resp, "choices") or [None])[0]
    if choice is not None and _field(choice, "finish_reason") == "length":
        print(
            "  warning: the answer hit the output cap and was cut off — narrow the "
            "question with --prompt, or analyze a span with --clip START-END.",
            file=sys.stderr,
        )
    message = _field(choice, "message") if choice is not None else None
    text = _field(message, "content") if message is not None else None
    if not text:
        raise TubetellError("the model returned no answer")
    return text


# --- the whole path -------------------------------------------------------------


def answer(
    path: Path,
    *,
    mode: str,
    prompt: str | None,
    model: str | None,
    clip: tuple[int, int] | None,
    fps: float | None,
    description: str | None,
    transcript: Path | None = None,
    speakers: list = (),
    glossary_terms: list[str] = (),
) -> str:
    """Run the Azure pipeline on one local file and return the answer.

    `speakers` are known voices (tubetell.speakers.Speaker) for step 1.
    `glossary_terms` are restored in a fresh transcript by one chat request
    before it is cached (tubetell.glossary).
    """

    cfg = settings(model)
    if not path.exists():
        raise TubetellError(f"No such file: {path}")
    if path.is_dir():
        raise TubetellError(f"{path} is a directory, not a media file.")
    _require("ffmpeg")
    info = streams(path)
    duration = info["duration"]
    if clip:
        span = (float(clip[0]), float(min(clip[1], duration or clip[1])))
    elif duration:
        span = (0.0, duration)
    else:
        raise TubetellError(f"Could not establish how long {path.name} runs (ffprobe).")
    if not (info["audio"] or info["video"]):
        raise TubetellError(f"{path.name} has neither an audio nor a video stream.")

    transcript_only = mode == "transcript" and not prompt
    client = make_client(cfg)
    usage = TranscriptionUsage()
    with tempfile.TemporaryDirectory(prefix="tubetell-azure-") as tmp:
        workdir = Path(tmp)
        segments: list[Segment] = []
        if transcript is not None:
            segments = slice_segments(load_file(transcript).segments, span)
            print(f"transcript: {transcript.name} ({len(segments)} lines)", file=sys.stderr)
        elif info["audio"]:
            segments = cached_transcript(cfg, path, span, transcript_tag(speakers, glossary_terms)) or transcribe_and_cache(
                cfg, path, span, workdir, usage, speakers, client=client, glossary_terms=list(glossary_terms)
            )
        elif transcript_only:
            raise TubetellError(f"{path.name} has no audio track to transcribe.")
        text_transcript = format_transcript(segments)
        if transcript_only:
            if not text_transcript:
                raise TubetellError("the transcription returned no speech")
            return text_transcript
        frames = sample_frames(path, span, workdir, fps=fps) if info["video"] else []
        text = answer_text(
            prompt or MODE_PROMPTS[mode],
            text_transcript,
            # Timestamps are on the source's clock, so the bound is where the
            # analyzed span ends, clip or not.
            duration=span[1],
            description=description,
            multipart=is_multipart(segments),
            has_frames=bool(frames),
            named=is_named(segments),
        )
        print(
            f"asking {cfg.chat_deployment} ({len(segments)} transcript lines, "
            f"{len(frames)} frames)...",
            file=sys.stderr,
        )
        return ask(client, cfg, chat_content(text, frames))
