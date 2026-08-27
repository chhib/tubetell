"""Fit a video into Gemini's context window.

Gemini tokenizes video at a fixed rate — about 258 tokens per sampled frame at
the default resolution (66 at low), plus 32 tokens per second of audio — and
samples one frame per second unless told otherwise. That puts a talking-head
hour at ~1.05M tokens: just over the 1,048,576-token window, and Vertex rejects
the request outright ("The input token count exceeds the maximum number of
tokens allowed"). Nothing about the video is too big — the sampling is just
denser than the content needs.

`plan()` decides, from the runtime, how to make a request fit: first drop the
frame resolution (fine for anything where the words matter more than the
pixels), then split the runtime into clips that each fit and let the caller
stitch the answers together.
"""

from __future__ import annotations

from dataclasses import dataclass

FRAME_TOKENS = 258
FRAME_TOKENS_LOW = 66
AUDIO_TOKENS_PER_SEC = 32
DEFAULT_FPS = 1.0

# Gemini 2.5's window is 1,048,576 tokens; keep headroom for the prompt and
# for the estimate being approximate.
INPUT_BUDGET = 950_000


def estimate_tokens(duration: float, *, fps: float | None, low_res: bool) -> int:
    """Input tokens a video of this runtime costs at these sampling settings."""
    per_frame = FRAME_TOKENS_LOW if low_res else FRAME_TOKENS
    rate = per_frame * (fps if fps is not None else DEFAULT_FPS) + AUDIO_TOKENS_PER_SEC
    return int(duration * rate)


@dataclass(frozen=True)
class Plan:
    low_res: bool
    clips: tuple[tuple[int, int], ...]  # empty: send the whole video in one request

    @property
    def chunked(self) -> bool:
        return len(self.clips) > 1


def plan(
    duration: float | None,
    *,
    fps: float | None,
    clip: tuple[int, int] | None,
    budget: int = INPUT_BUDGET,
) -> Plan:
    """How to fit this source into one or more requests.

    Unknown runtime means no plan: send as-is and let the API answer. A user
    --clip is honoured as the range to fit, and is itself split if too long.
    """
    if not duration or duration <= 0:
        return Plan(low_res=False, clips=())
    start, end = clip if clip else (0, int(round(duration)))
    span = end - start
    if estimate_tokens(span, fps=fps, low_res=False) <= budget:
        return Plan(low_res=False, clips=())
    if estimate_tokens(span, fps=fps, low_res=True) <= budget:
        return Plan(low_res=True, clips=())
    per_sec = estimate_tokens(1, fps=fps, low_res=True) or 1
    chunk = max(60, int(budget / per_sec))
    clips = []
    pos = start
    while pos < end:
        clips.append((pos, min(pos + chunk, end)))
        pos += chunk
    return Plan(low_res=True, clips=tuple(clips))
