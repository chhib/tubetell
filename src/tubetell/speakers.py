"""Known speakers for the Azure transcription step.

The diarize model labels voices `A`, `B`, ... per request. Given a short
reference clip of a voice (2 to 10 s) and a name, it labels that voice with the
name instead, in every request it is sent with, so names reach the transcript
directly and a voice keeps its name across chunks.

`--speaker Name=mm:ss-mm:ss` cuts the reference from the recording being
analyzed and keeps it as `~/.config/tubetell/speakers/Name.mp3`
(`$XDG_CONFIG_HOME` replaces `~/.config`); `--speaker Name` reuses the kept
clip. The API takes at most four speakers per request.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

from . import TubetellError
from .foundry import AUDIO_ARGS, _run
from .media import parse_clip

MAX_SPEAKERS = 4
MIN_SECONDS = 2
MAX_SECONDS = 10


@dataclass(frozen=True)
class Speaker:
    name: str
    reference: Path


def speakers_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "tubetell" / "speakers"


def parse_flag(flag: str) -> tuple[str, tuple[int, int] | None]:
    """`Name=START-END` or `Name`; a span is 2 to 10 seconds of the recording."""
    name, _, span_text = flag.partition("=")
    name = name.strip()
    if not name:
        raise TubetellError(f"--speaker needs a name: `Name` or `Name=mm:ss-mm:ss` (got {flag!r}).")
    if not span_text:
        return name, None
    try:
        span = parse_clip(span_text)
    except TubetellError as exc:
        raise TubetellError(f"--speaker {name}: {exc}") from exc
    length = span[1] - span[0]
    if not MIN_SECONDS <= length <= MAX_SECONDS:
        raise TubetellError(
            f"--speaker {name}: the reference must be {MIN_SECONDS} to {MAX_SECONDS} s "
            f"of one voice (got {length} s)."
        )
    return name, span


def cut_reference(src: Path, dest: Path, span: tuple[int, int]) -> None:
    a, b = span
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{a:g}", "-to", f"{b:g}", "-i", str(src), *AUDIO_ARGS, str(dest)]
    _run(cmd, f"cutting the reference for {dest.stem} from {src.name}")


def resolve(flags: list[str], src: Path) -> list[Speaker]:
    """The speakers named on the command line, each with a reference file:
    cut now from `src` when a span is given, otherwise the kept one."""
    found = []
    for flag in flags:
        name, span = parse_flag(flag)
        reference = speakers_dir() / f"{name}.mp3"
        if span:
            cut_reference(src, reference, span)
        elif not reference.is_file():
            raise TubetellError(
                f"No reference clip for {name}: expected {reference}. Cut one with "
                f"--speaker '{name}=mm:ss-mm:ss' (2 to 10 s of that voice alone)."
            )
        found.append(Speaker(name, reference))
    if len(found) > MAX_SPEAKERS:
        raise TubetellError(f"--speaker: the transcription model takes at most {MAX_SPEAKERS} known speakers.")
    return found


def references(found: list[Speaker]) -> list[str]:
    """The reference clips as data URLs, in the same order as the names."""
    return [
        "data:audio/mp3;base64," + base64.b64encode(s.reference.read_bytes()).decode()
        for s in found
    ]


def cache_tag(found: list[Speaker]) -> str:
    """What separates this transcript's cache file from one made without the
    names: the sorted names."""
    return "+".join(sorted(s.name for s in found))
