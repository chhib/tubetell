# tubetell

Ask Gemini anything about a video — a YouTube URL, a `gs://` object, or a file
on your disk.

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

tubetell ~/screen-recording.mov --prompt "What did I click, and when?"
tubetell gameplay.mov --fps 2 --clip 1:30-2:45
```

## Modes

| Mode | What you get |
|------|--------------|
| `summary` (default) | 3-sentence summary, key points, notable entities, overall tone |
| `transcript` | full timestamped transcript, one line per segment, speakers attributed |
| `claims` | every checkable factual claim with timestamp and speaker |
| `sentiment` | how tone/sentiment shifts across the runtime, each shift timestamped |
| `comments` | audience sentiment from top comments (reads comments, not the video) |

`--prompt "..."` overrides the mode preset entirely — ask the video anything.

See [docs/example.md](docs/example.md) for a worked example: all five modes
plus a custom prompt on one video, with real outputs and token counts.

Every video-mode request — preset or `--prompt` — carries a timestamp rule,
because left alone Gemini writes `mm:ss` past the hour mark (1:47:00 comes back
as `47:00` or `107:00`) and will cite a position past the end of the video. The
rule pins one format per answer (`mm:ss` in the first hour, `h:mm:ss` after it)
and tells the model to drop the timestamp rather than guess. When the runtime
can be established — ffprobe for a local file, `videos.list` for a YouTube URL
if `YOUTUBE_API_KEY` happens to be set — it goes in the prompt as a hard upper
bound. Both lookups are best effort: no ffprobe or no key just means no bound.

The `comments` prompt is hardened against hallucination: quotes must be
verbatim from the fetched comments, and small samples are summarized without
invented percentage splits.

## Sources

| Source | Example | How it travels |
|---|---|---|
| YouTube | `https://youtu.be/vOVKnYoH1p4`, `vOVKnYoH1p4` | a URL Vertex fetches itself (public videos only) |
| Cloud Storage | `gs://my-bucket/clip.mp4` | a URI Vertex reads from the bucket |
| Local file | `~/recording.mov`, `clip.mp4` | proxied by ffmpeg, then sent inline |

Local video and audio both work — `.mov`, `.mp4`, `.webm`, `.mkv`, `.avi`,
`.mpeg`, `.flv`, `.wmv`, `.3gp`, `.mp3`, `.wav`, `.m4a`, `.aac`, `.ogg`,
`.flac`. Only `--mode comments` is YouTube-only; it reads a comment section,
which a local file doesn't have.

### Local files: the proxy

A request can only carry about 12 MiB of media, and a phone or screen recording
is usually far bigger — so anything over the cap is re-encoded first, to 1280px
wide at 2 fps (H.264/AAC). That is not much of a compromise: Gemini samples
video at roughly 1 frame per second and downscales it anyway, so a 120 fps
retina capture spends its bytes on detail the model never sees. A 3-minute,
322 MB screen recording becomes a 2.3 MB proxy.

Proxies are cached in your temp directory, keyed by the file's size and mtime,
so iterating on prompts against one video only transcodes once. This needs
`ffmpeg` on PATH (`brew install ffmpeg`).

```bash
tubetell clip.mov --width 1920        # sharper proxy, bigger request
tubetell clip.mov --no-transcode      # send as-is; fails if over ~12 MiB
```

If even the coarsest proxy won't fit — an hour of footage, say — tubetell says
so and tells you to work in pieces with `--clip`.

### Framing what Gemini looks at

Both work with every source, and both move the bill:

```bash
tubetell <source> --clip 1:30-2:45   # only this span (ss, mm:ss, hh:mm:ss)
tubetell <source> --fps 2            # frames per second to look at
```

`--fps` defaults to Gemini's own ~1/s. Raise it for fast-moving footage, lower
it (`--fps 0.5`) to halve the video tokens on something slow.

There is no resolution knob, because for video there is nothing to turn:
`gemini-2.5-flash` accepts `MEDIA_RESOLUTION_HIGH` only for single images and
rejects the request outright for video. Small on-screen text still reads
surprisingly well at the default — but when it doesn't, the fix is to crop the
region you care about before handing the file over, not to send more pixels of
the whole frame.

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
- **Local files are re-tokenized every run too.** The proxy saves upload time
  and request size, not tokens — a 1280px frame and a 4K frame cost the same.
  The levers that cut the bill are `--clip` (pay for less runtime) and
  `--fps 0.5` (pay for fewer frames). A silent video also skips the audio
  tokens, which are the expensive kind: a 3:01 screen recording with no audio
  track came to 46,871 input tokens at `--fps 1` — 259 per second of video,
  matching the rate measured above — or about $0.014 of input per run.

## Troubleshooting

**`500 INTERNAL` from Vertex, repeatedly.** Vertex re-fetches the YouTube URL
on every call, and analyzing the same video many times in a short window gets
the fetch rate-limited downstream. tubetell backs off and retries (4s → 8s →
16s) automatically; if it still fails, wait a minute and retry, or try a
different video. Switching regions does not help.

**`Server disconnected without sending a response.`** A transient drop while a
request carrying inline media uploads. tubetell retries it on the same backoff
as the 500s; if it keeps happening, wait a minute.

**`the model supports HIGH media resolution only for single images`.** Nothing
in tubetell sets per-frame resolution anymore — see
[Framing what Gemini looks at](#framing-what-gemini-looks-at). If you see this,
something else in your environment is setting `media_resolution`.

**Long videos.** Cost and latency scale with video length, not with the
question — see [Cost](#cost). A local file that no proxy can squeeze under the
request cap has to be worked in `--clip` slices.

**Private/unlisted videos** can't be analyzed — Vertex fetches the video
server-side and only public videos are supported. Download it and pass the
file instead.

**`ffmpeg is not installed`.** Only local files over ~12 MiB need it:
`brew install ffmpeg`.

## Development

```bash
git clone https://github.com/chhib/tubetell && cd tubetell
python3 -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/pytest
```

## License

MIT
