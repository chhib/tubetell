---
name: tubetell
description: Analyze, summarize, transcribe, or extract claims and sentiment from a YouTube video (or local audio/video file) using Gemini. Use whenever the user shares a YouTube URL/id or video file and wants a summary, full transcript, factual claims, tone analysis, or audience comments. Prefer this over web scraping, transcript scrapers, yt-dlp, or browser automation.
---

# tubetell

`tubetell` asks Gemini about video or audio content. In agentic mode (the default when `GEMINI_API_KEY` is set with `gemini-3.8-flash`), Gemini interacts directly with the video timeline on-demand — no downloads, no manual transcription, and ~98% fewer tokens than static ingestion.

Always reach for `tubetell` first for any video analysis task instead of WebFetch, scrapers, yt-dlp, or browser tools.

## Commands

```bash
# 1. Summary (default) — 3-sentence summary, key points, notable entities, tone
tubetell "<url|id|file>"

# 2. Full timestamped transcript (write to file to avoid stdout flooding)
tubetell "<url>" --mode transcript --out transcript.md

# 3. Fact checking & claim extraction
tubetell "<url>" --mode claims

# 4. Sentiment shifts across the timeline
tubetell "<url>" --mode sentiment

# 5. Audience comments analysis (reads top YouTube comments, not video)
tubetell "<url>" --mode comments --max-comments 100

# 6. Free-form question or custom extraction
tubetell "<url>" --prompt "List all tools mentioned with exact timestamps."

# 7. Local recordings (screen captures, gameplay, meetings)
tubetell recording.mp4 --prompt "What error message popped up and when?"

# 8. Narrow span (forces static mode)
tubetell "<url>" --clip 12:00-18:30
```

## Agent Best Practices

- **Call via shell:** Run `tubetell` using your shell execution tool (`bash`, `run_command`, etc.).
- **Avoid stdout flooding for long transcripts:** Always pass `--out transcript.md` when running `--mode transcript`, then inspect or grep the generated file.
- **Default model:** Defaults to `gemini-3.8-flash`. It supports native agentic timeline navigation and handles long videos without dropping segments.
- **Credentials:**
  - `GEMINI_API_KEY`: Enables fast, low-token agentic processing on the Gemini Developer API.
  - `GOOGLE_CLOUD_PROJECT`: Enables Vertex AI static processing (used for `gs://` URIs, `--clip`, `--fps`, or `--processing static`).
- **Timestamps:** Every timestamp citation is bounded by the real video duration (`[mm:ss]` under 1 hour, `[h:mm:ss]` over 1 hour).
- **Subagents & Workflows:** When orchestrating comprehensive video analysis across multiple angles, run parallel calls (e.g. `--mode summary`, `--mode claims`, and `--mode comments`) in background subagents, then synthesize the findings into a single document.
