"""A transcript as a file: the cache the Azure path reads before it transcribes,
and the `--transcript` file a user hands in instead.

Transcribing is the slow and expensive step of the Azure path (about a minute
and 0.03 USD per minute of audio), and a meeting is usually asked several
things: a transcript, a few `--clip` spans, a ticket prompt. The diarized
segments are therefore written once per (file content, span, deployment) under
`$XDG_CACHE_HOME/tubetell/transcripts/` (default `~/.cache/tubetell/`) and read
back by every later run on the same file. A run on a span inside a cached span
slices the cached segments.

`--transcript <file>` takes either such a JSON file or tubetell's own text
format (`[mm:ss] Speaker: text`, one line per segment), so a hand-corrected
transcript (names fixed, a product name spelt right) is what the later steps
see.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path

from . import TubetellError
from .media import parse_offset

LINE = re.compile(r"^\[(?P<time>[\d:]+)\]\s+(?P<speaker>[^:]+?):\s?(?P<text>.*)$")
PART_LABEL = re.compile(r"^Speaker \d+-")


@dataclass(frozen=True)
class Segment:
    """One diarized line: when it starts and ends on the source's clock, who
    speaks (a name, or a generic label), and the words."""

    start: float
    end: float
    speaker: str
    text: str


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "tubetell" / "transcripts"


def content_hash(path: Path) -> str:
    """The first twelve hex digits of the file's SHA-256: the same recording
    under another name or path is the same key."""
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:12]


def _span_key(span: tuple[float, float]) -> str:
    return f"{span[0]:g}-{span[1]:g}"


def cache_path(path: Path, span: tuple[float, float], deployment: str, *, tag: str = "") -> Path:
    """Where the transcript of this file, span and deployment lives.

    `tag` separates transcripts of the same audio made with different settings
    (known speakers, for instance): a different tag is a different file.
    """
    name = f"{deployment}-{_span_key(span)}" + (f"-{tag}" if tag else "") + ".json"
    return cache_dir() / content_hash(path) / name


@dataclass(frozen=True)
class Transcript:
    source: str
    span: tuple[float, float]
    deployment: str | None
    segments: list[Segment]
    # What else shaped the transcript (known speaker names); part of the key.
    tag: str = ""

    def covers(self, span: tuple[float, float]) -> bool:
        return self.span[0] <= span[0] and span[1] <= self.span[1]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "source": self.source,
            "span": list(self.span),
            "deployment": self.deployment,
            "tag": self.tag,
            "segments": [asdict(s) for s in self.segments],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Transcript":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                source=data["source"],
                span=(float(data["span"][0]), float(data["span"][1])),
                deployment=data.get("deployment"),
                tag=data.get("tag") or "",
                segments=[
                    Segment(float(s["start"]), float(s["end"]), s["speaker"], s["text"])
                    for s in data["segments"]
                ],
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise TubetellError(f"{path} is not a tubetell transcript file.") from exc


def slice_segments(segments: list[Segment], span: tuple[float, float]) -> list[Segment]:
    """The segments whose midpoint lies in the span — the rule merge_chunks uses."""
    a, b = span
    return [s for s in segments if a <= (s.start + s.end) / 2 <= b]


def parse_text(text: str) -> list[Segment]:
    """Segments from tubetell's transcript format, `[mm:ss] Speaker: text`.

    The text carries no end times, so each segment runs to the next one's
    start; the last is a point.
    """
    starts, speakers, texts = [], [], []
    for line in text.splitlines():
        m = LINE.match(line.strip())
        if not m:
            continue
        starts.append(float(parse_offset(m.group("time"))))
        speakers.append(m.group("speaker").strip())
        texts.append(m.group("text").strip())
    if not starts:
        raise TubetellError(
            "The transcript has no transcript lines; expected `[mm:ss] Speaker: text`, "
            "one per line."
        )
    ends = starts[1:] + [starts[-1]]
    return [Segment(a, b, s, t) for a, b, s, t in zip(starts, ends, speakers, texts)]


def load_file(path: Path) -> Transcript:
    """A `--transcript` file: tubetell's JSON cache format, or its text format."""
    if not path.is_file():
        raise TubetellError(f"No such transcript: {path}")
    if path.suffix.lower() == ".json":
        return Transcript.load(path)
    segments = parse_text(path.read_text(encoding="utf-8"))
    span = (segments[0].start, segments[-1].end)
    return Transcript(source=path.name, span=span, deployment=None, segments=segments)


def is_named(segments: list[Segment]) -> bool:
    """Whether any label is a name rather than a generic `Speaker ...` one."""
    return any(not s.speaker.startswith("Speaker ") for s in segments)


def is_multipart(segments: list[Segment]) -> bool:
    """Whether any label is a per-part one (`Speaker 2-A`), which the chat step
    is then told to reconcile."""
    return any(PART_LABEL.match(s.speaker) for s in segments)
