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

See [docs/example.md](docs/example.md) for a worked example: all five modes
plus a custom prompt on one video, with real outputs and token counts.

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

## Cost

**Rule of thumb: one question to one video costs about $0.006 per minute of
video runtime.** A 17-minute video is ~$0.11 per run, an hour-long video
~$0.37 — whichever mode you use, whatever you ask. The exception is
`comments` mode, which never touches the video and costs about a cent
regardless of video length.

These numbers come from a real experiment, not the price list: every mode
plus one custom prompt was run against the same 16:57 cooking video, and each
run's token usage was recorded — [docs/example.md](docs/example.md) has all
the commands and outputs. tubetell prints this line to stderr on every run,
so you can watch your own spend (stdout stays clean for piping):

```
tokens: 287,866 in + 1,534 thinking + 1,019 out = 290,419 total
```

### Where the money goes

When Gemini "watches" a YouTube video, Vertex converts it into input tokens
at a fixed rate — measured on this video:

| | tokens per second of video |
|---|---|
| video frames (1 frame/s at default resolution) | 258 |
| audio track | 25 |
| **total** | **≈283** (≈1M tokens per hour of video) |

Two consequences:

- **You pay for the whole video on every run.** Vertex re-fetches and
  re-tokenizes it each call — there is no caching. This 17-minute video is
  ~288k input tokens every single time, whether you ask for a full transcript
  or a yes/no answer.
- **The answer is nearly free by comparison.** Even the 7,486-token full
  transcript added less than 2 cents of output.

Those tokens are billed at Vertex's `gemini-2.5-flash` standard-tier rates
(as of July 2026 — check
[current pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing)),
and note that audio costs more than video:

| | per 1M tokens |
|---|---|
| Input: text, image, **video** | $0.30 |
| Input: **audio** | $1.00 |
| Output (response + thinking) | $2.50 |

### What the experiment cost, per run

For the test video, input = 262,386 video tokens + 25,425 audio tokens
≈ **$0.104 per run** — which is why every video mode below lands at nearly
the same price no matter how long its answer was:

| Run | Input tokens | Output tokens | Cost |
|---|---|---|---|
| `summary` | 287,866 | 2,553 | $0.111 |
| `transcript` | 287,867 | 7,486 | $0.123 |
| `claims` | 287,859 | 746 | $0.106 |
| `sentiment` | 287,858 | 499 | $0.105 |
| `--prompt` (shopping list) | 287,833 | 2,431 | $0.110 |
| `comments` (50 comments) | 2,013 | 5,007 | $0.013 |
| **whole experiment** | | | **≈ $0.57** |

Practical upshots:

- Cost scales with **video length**, not with what you ask.
- Want several answers about one video? Bundle them into **one `--prompt`** —
  five separate runs pay for the video five times.
- `comments` mode reads the YouTube Data API instead of the video, so its
  cost is a flat ~$0.01 (almost all of it output tokens).

## Troubleshooting

**`500 INTERNAL` from Vertex, repeatedly.** Vertex re-fetches the YouTube URL
on every call, and analyzing the same video many times in a short window gets
the fetch rate-limited downstream. tubetell backs off and retries (4s → 8s →
16s) automatically; if it still fails, wait a minute and retry, or try a
different video. Switching regions does not help.

**Long videos.** Cost and latency scale with video length, not with the
question — see [Cost](#cost).

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
