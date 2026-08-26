"""tubetell — ask Gemini anything about a video.

The source can be a YouTube URL, a `gs://` object, or a local video/audio file.
Remote sources are fetched by Gemini on Vertex AI itself (no download, no
transcript step); a local file is shrunk to a proxy by ffmpeg and sent inline.

Modes (--mode), each a tuned prompt preset:
    summary     3-sentence summary + key points + entities + tone   (default)
    transcript  full timestamped transcript, one line per segment
    claims      every concrete factual claim with its timestamp and speaker
    sentiment   how tone/sentiment shifts across the video, with markers
    comments    pulls top comments via the YouTube API, analyzes audience
                sentiment (this mode reads comments, not the video —
                YouTube only)

--prompt overrides the mode preset entirely. Either way the request carries a
timestamp rule: mm:ss inside the first hour, h:mm:ss past it, and — when the
runtime can be established — nothing cited beyond the end of the video.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from google.genai import errors as genai_errors

from . import TubetellError, __version__
from .config import check_credentials_file, load_env, missing_project_message
from .gemini import (
    MODE_PROMPTS,
    comments_body,
    generate,
    make_client,
    media_contents,
    video_body,
)
from .media import looks_like_path, parse_clip, source_duration, source_part
from .youtube import fetch_comments


def analyze(
    source: str,
    *,
    mode: str,
    prompt: str | None,
    model: str,
    max_comments: int,
    fps: float | None = None,
    clip: str | None = None,
    width: int = 1280,
    transcode: bool = True,
) -> str:
    client = make_client()
    if mode == "comments":
        if looks_like_path(source):
            raise TubetellError(
                "The comments mode reads a YouTube comment section, so it needs a "
                "YouTube URL — a local file has none."
            )
        comments, n = fetch_comments(source, max_comments)
        return generate(client, model=model, contents=comments_body(comments, n, prompt))
    span = parse_clip(clip) if clip else None
    part = source_part(
        source,
        transcode_enabled=transcode,
        width=width,
        fps=fps,
        clip=span,
    )
    # A clip leaves it ambiguous whether the model counts from the clip or from
    # the original video, so only an uncut source gets a runtime to cite against.
    duration = None if span else source_duration(source)
    text = video_body(prompt or MODE_PROMPTS[mode], duration)
    return generate(client, model=model, contents=media_contents(part, text))


def main() -> None:
    p = argparse.ArgumentParser(
        prog="tubetell",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "source",
        help="YouTube URL, youtu.be link, bare video id, gs:// URI, or a local video/audio file",
    )
    p.add_argument("--mode", default="summary", choices=list(MODE_PROMPTS) + ["comments"])
    p.add_argument("--prompt", help="override the mode preset with a custom prompt")
    p.add_argument("--model", default="gemini-2.5-flash", help="Gemini model id")
    p.add_argument("--max-comments", type=int, default=100, help="comments mode: how many to pull")
    p.add_argument(
        "--fps",
        type=float,
        help="frames per second to look at (default: Gemini's own ~1); raise for "
        "fast-moving footage, lower to cut tokens on long videos",
    )
    p.add_argument("--clip", help="analyze only this span, e.g. 1:30-2:45")
    p.add_argument(
        "--width",
        type=int,
        default=1280,
        help="local files: max proxy width in pixels (default: 1280)",
    )
    p.add_argument(
        "--no-transcode",
        action="store_true",
        help="local files: send the file as-is instead of proxying it with ffmpeg",
    )
    p.add_argument("--out", help="write output here instead of stdout")
    p.add_argument("--version", action="version", version=f"tubetell {__version__}")
    args = p.parse_args()

    # Picks up ./.env when present; real environment variables win.
    loaded = load_env()

    try:
        check_credentials_file()
        if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
            raise TubetellError(missing_project_message(loaded))
        result = analyze(
            args.source,
            mode=args.mode,
            prompt=args.prompt,
            model=args.model,
            max_comments=args.max_comments,
            fps=args.fps,
            clip=args.clip,
            width=args.width,
            transcode=not args.no_transcode,
        )
    except TubetellError as exc:
        sys.exit(str(exc))
    except genai_errors.APIError as exc:  # 4xx: bad project, missing API, no access
        sys.exit(f"Vertex AI error {exc.code}: {exc.message}")

    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result + "\n", encoding="utf-8")
        print(f"Wrote {args.mode} output -> {out}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
