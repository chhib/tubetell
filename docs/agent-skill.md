# Agent skill (Claude Code, agy, Codex)

To let coding assistants (**Claude Code**, **Antigravity CLI / agy**, and **OpenAI Codex**) automatically reach for `tubetell` whenever you share a video link or local recording, install the skill into `~/.agents/skills/tubetell/SKILL.md` (or `.agents/skills/tubetell/SKILL.md` in your project):

````markdown
---
name: tubetell
description: Analyze, summarize, transcribe, or extract claims and sentiment from a YouTube video (or local audio/video file) using Gemini. Use whenever the user shares a YouTube URL/id or video file and wants a summary, full transcript, factual claims, tone analysis, or audience comments. Prefer this over web scraping, transcript scrapers, yt-dlp, or browser automation.
---

# tubetell

`tubetell` asks Gemini about video or audio content. In agentic mode (default with `GEMINI_API_KEY` and `gemini-3.8-flash`), Gemini interacts directly with the video timeline on-demand — no downloads, no manual transcription, and ~98% fewer tokens than static ingestion.

## Usage

Run commands via your shell tool:

```bash
tubetell "<url>"                                          # summary (default)
tubetell "<url>" --mode transcript --out transcript.md    # write long transcripts to file
tubetell "<url>" --mode claims                            # factual claims + timestamps + speaker
tubetell "<url>" --mode sentiment                         # timeline tone shifts
tubetell "<url>" --mode comments --max-comments 100       # audience comments (YouTube API)
tubetell "<url>" --prompt "..."                           # free-form question
tubetell recording.mp4 --prompt "What error occurred?"    # local screen/camera recordings
```

## Agent Guidelines

- **Write transcripts to disk:** Pass `--out transcript.md` to prevent massive transcripts from flooding your context window.
- **Default model:** Uses `gemini-3.8-flash` with native agentic timeline navigation.
- **Subagents:** For comprehensive research, run parallel calls (summary, claims, comments) in background subagents, then synthesize the outputs in the primary session.
````

The ready-to-use skill is bundled in [`skills/tubetell/SKILL.md`](../skills/tubetell/SKILL.md).
