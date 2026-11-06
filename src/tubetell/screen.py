"""The screen pass: what is on each sampled frame, written down once.

Given fifty frames and the question "write the tickets", a chat model does
not look at the frames: a dashboard tile full of zeros and a wrong "closed
through" date went unnoticed in a ticket step, while the same model listed
both in eleven seconds when shown that one frame and asked what was wrong.
So the frames get their own step, in batches, with one question: for each
frame, what is shown, which numbers and labels are legible, and what looks
wrong. The answers are kept as JSON lines next to the transcript cache, and
the answer step receives them as text instead of the frames, so figures,
labels and dates on screen are in its material rather than in pictures it
would have to read on the side.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from .transcript import cache_dir, content_hash

# Frames per request: about 800 tokens each at high detail, so 25 is ~20k
# tokens, which fits a deployment of 50 units (50,000 tokens per minute).
BATCH_FRAMES = 25
FENCE = re.compile(r"^\s*```(?:json|jsonl)?\s*|\s*```\s*$")


@dataclass(frozen=True)
class Observation:
    time: float
    shown: str
    legible: str
    wrong: str


def screen_cache_path(src: Path, span: tuple[float, float], deployment: str, *, fps: float | None) -> Path:
    """Where the observations of this file, span, chat deployment and frame
    sampling live: beside the transcript, under the same content hash."""
    sampling = f"{fps:g}" if fps else "auto"
    name = f"screen-{deployment}-{span[0]:g}-{span[1]:g}-{sampling}.jsonl"
    return cache_dir() / content_hash(src) / name


def save(path: Path, observations: list[Observation]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(asdict(o), ensure_ascii=False) + "\n" for o in observations),
        encoding="utf-8",
    )


def load(path: Path) -> list[Observation]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            out.append(Observation(float(d["time"]), d.get("shown", ""), d.get("legible", ""), d.get("wrong", "")))
    return out


def batches(frames: list, size: int) -> list[list]:
    return [frames[i : i + size] for i in range(0, len(frames), size)]


def _stamp(seconds: float) -> str:
    from .foundry import stamp

    return stamp(seconds)


def screen_request(frames: list) -> str:
    times = ", ".join(_stamp(f.time) for f in frames)
    return (
        f"You are given {len(frames)} still frames from a screen recording, each preceded "
        "by its timestamp. For each frame, in order, write exactly one line of JSON: "
        '{"frame": "<timestamp as given>", "shown": "<what is on screen: the application, '
        'page or slide, and what it is about>", "legible": "<the numbers, dates, labels, '
        "names and identifiers you can actually read, copied exactly>\", \"wrong\": "
        '"<what looks wrong or inconsistent in what is shown: empty tiles, zeros where a '
        "value is expected, dates that do not match, a title that contradicts a setting, "
        'an error message; or the word nothing>"}. Read only what is legible; do not '
        "guess at small text. One line per frame, no other text. The frames are at: "
        f"{times}."
    )


def parse_answer(answer: str, times: list[float]) -> list[Observation]:
    """One observation per JSON line, matched to the batch's frames in order;
    an answer that is not JSON lines is kept whole as one observation."""
    out = []
    for line in FENCE.sub("", answer.strip()).splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if len(out) < len(times):
            out.append(
                Observation(times[len(out)], str(d.get("shown", "")), str(d.get("legible", "")), str(d.get("wrong", "")))
            )
    if not out and times:
        return [Observation(times[0], answer.strip(), "", "")]
    return out


def format_observations(observations: list[Observation]) -> str:
    """The observations as text for the answer step, one frame per line."""
    lines = []
    for o in observations:
        parts = [f"shown: {o.shown}"]
        if o.legible:
            parts.append(f"legible: {o.legible}")
        if o.wrong:
            parts.append(f"wrong: {o.wrong}")
        lines.append(f"[{_stamp(o.time)}] " + " | ".join(parts))
    return "\n".join(lines)


def observe(client, cfg, src: Path, span: tuple[float, float], frames: list, *, fps: float | None) -> list[Observation]:
    """The screen pass over these frames, from the cache when it has run before."""
    from .foundry import ask, chat_content

    path = screen_cache_path(src, span, cfg.chat_deployment, fps=fps)
    if path.is_file():
        cached = load(path)
        print(f"screen: cached ({path}, {len(cached)} frames)", file=sys.stderr)
        return cached
    groups = batches(frames, BATCH_FRAMES)
    print(
        f"screen pass: {len(frames)} frames in {len(groups)} request{'s' if len(groups) != 1 else ''} "
        f"to {cfg.chat_deployment}...",
        file=sys.stderr,
    )
    observations: list[Observation] = []
    for group in groups:
        answer = ask(client, cfg, chat_content(screen_request(group), group))
        observations.extend(parse_answer(answer, [f.time for f in group]))
    save(path, observations)
    print(f"screen: saved to {path}", file=sys.stderr)
    return observations
