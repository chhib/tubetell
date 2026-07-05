"""tubetell — ask Gemini anything about a YouTube video.

Gemini on Vertex AI ingests the YouTube URL directly (no download, no
transcript step) and answers a prompt about the content.

Modes (--mode), each a tuned prompt preset:
    summary     3-sentence summary + key points + entities + tone   (default)
    transcript  full timestamped transcript, [mm:ss] per segment
    claims      every concrete factual claim with its [mm:ss] and speaker
    sentiment   how tone/sentiment shifts across the video, with markers
    comments    pulls top comments via the YouTube API, analyzes audience
                sentiment (this mode reads comments, not the video)

--prompt overrides the mode preset entirely.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import TubetellError, __version__
from .gemini import MODE_PROMPTS, comments_body, generate, make_client, video_contents
from .youtube import fetch_comments


def analyze(url: str, *, mode: str, prompt: str | None, model: str, max_comments: int) -> str:
    client = make_client()
    if mode == "comments":
        comments, n = fetch_comments(url, max_comments)
        return generate(client, model=model, contents=comments_body(comments, n, prompt))
    text = prompt or MODE_PROMPTS[mode]
    return generate(client, model=model, contents=video_contents(url, text))


def main() -> None:
    p = argparse.ArgumentParser(
        prog="tubetell",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("url", help="YouTube watch URL, youtu.be link, or bare video id")
    p.add_argument("--mode", default="summary", choices=list(MODE_PROMPTS) + ["comments"])
    p.add_argument("--prompt", help="override the mode preset with a custom prompt")
    p.add_argument("--model", default="gemini-2.5-flash", help="Gemini model id")
    p.add_argument("--max-comments", type=int, default=100, help="comments mode: how many to pull")
    p.add_argument("--out", help="write output here instead of stdout")
    p.add_argument("--version", action="version", version=f"tubetell {__version__}")
    args = p.parse_args()

    # Picks up ./.env when present; real environment variables win.
    load_dotenv(override=False)

    try:
        result = analyze(
            args.url,
            mode=args.mode,
            prompt=args.prompt,
            model=args.model,
            max_comments=args.max_comments,
        )
    except TubetellError as exc:
        sys.exit(str(exc))

    if args.out:
        Path(args.out).write_text(result + "\n", encoding="utf-8")
        print(f"Wrote {args.mode} output -> {args.out}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
