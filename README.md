# tubetell

Ask Gemini anything about a YouTube video — straight from the URL.

Gemini on Vertex AI ingests the video itself (no download, no transcription
step) and answers a prompt about the content: what's said, what's shown, what's
claimed, how the tone shifts. A separate mode reads the comment section instead
and analyzes audience sentiment.

```bash
tubetell https://www.youtube.com/watch?v=vOVKnYoH1p4
tubetell <url> --mode transcript --out transcript.md
tubetell <url> --mode claims --model gemini-2.5-pro
tubetell <url> --mode comments --max-comments 100
tubetell <url> --prompt "What products are recommended, and by whom?"
```

## Modes

| Mode | What you get |
|------|--------------|
| `summary` (default) | 3-sentence summary, key points, notable entities, overall tone |
| `transcript` | full timestamped transcript, `[mm:ss]` per segment, speakers attributed |
| `claims` | every checkable factual claim with timestamp and speaker |
| `sentiment` | how tone/sentiment shifts across the runtime, with `[mm:ss]` markers |
| `comments` | audience sentiment from top comments (reads comments, not the video) |

`--prompt "..."` overrides the mode preset entirely — ask the video anything.

The `comments` prompt is hardened against hallucination: quotes must be
verbatim from the fetched comments, and small samples are summarized without
invented percentage splits.

## Install

```bash
pipx install git+https://github.com/chhib/tubetell
# or run without installing:
uvx --from git+https://github.com/chhib/tubetell tubetell --help
```

Requires Python 3.10+.

## Setup

You need a Google Cloud project with the **Vertex AI API** enabled. tubetell
reads configuration from environment variables, or from a `.env` file in the
directory you run it from (real environment variables win — see
[`.env.example`](.env.example)):

```bash
export GOOGLE_CLOUD_PROJECT=my-project-id
export GOOGLE_CLOUD_LOCATION=global        # optional, this is the default
```

### Auth, two ways

**Local machine (ADC)** — easiest. Log in once and tubetell picks up your
Application Default Credentials:

```bash
gcloud auth application-default login
```

**Headless / CI (service account)** — create a service account with the
`roles/aiplatform.user` role, download a JSON key, and point at it:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/key.json
```

If `GOOGLE_APPLICATION_CREDENTIALS` is set it wins; otherwise ADC is used.

### Comments mode only

`--mode comments` reads the comment section via the YouTube Data API v3, which
needs an API key (the other modes don't):

```bash
export YOUTUBE_API_KEY=...
```

Enable "YouTube Data API v3" in your Google Cloud project and create an API key
restricted to it. Each run costs ~1 quota unit per 100 comments — negligible
against the 10k/day default quota.

## Troubleshooting

**`500 INTERNAL` from Vertex, repeatedly.** Vertex re-fetches the YouTube URL
on every call, and analyzing the same video many times in a short window gets
the fetch rate-limited downstream. tubetell backs off and retries (4s → 8s →
16s) automatically; if it still fails, wait a minute and retry, or try a
different video. Switching regions does not help.

**Long videos.** Cost and latency scale with video length (roughly ~300 tokens
per second of video). If you want several answers about the same video, one
combined `--prompt` is cheaper than running multiple modes.

**Private/unlisted videos** can't be analyzed — Vertex fetches the video
server-side and only public videos are supported.

## Development

```bash
git clone https://github.com/chhib/tubetell && cd tubetell
python3 -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/pytest
```

## License

MIT
