# tubetell

Ask Gemini anything about a video — a YouTube URL, a `gs://` object, or a file
on your disk.

Gemini reads the video itself (no download, no transcription step) and answers
a prompt about the content: what's said, what's shown, what's claimed, how the
tone shifts. It gets at the video one of two ways. **Agentic**: through the
Gemini Interactions API, where the model navigates the timeline and loads
transcript and frames on demand, so an hour-long video costs a few thousand
tokens. **Static**: through `generate_content` on Vertex AI, where every sampled
frame and second of audio is tokenized up front. tubetell picks agentic when it
can and static otherwise — see [Processing modes](#processing-modes). A
separate mode reads the comment section instead and analyzes audience
sentiment.

```bash
tubetell https://www.youtube.com/watch?v=vOVKnYoH1p4
tubetell <url> --mode transcript --out transcript.md
tubetell <url> --mode claims
tubetell <url> --mode comments --max-comments 100
tubetell <url> --prompt "What products are recommended, and by whom?"

tubetell ~/screen-recording.mov --prompt "What did I click, and when?"
tubetell gameplay.mov --fps 2 --clip 1:30-2:45      # static: exact frames, one span
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
plus a custom prompt on one video, in both processing modes, with real outputs
and token counts.

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

## Processing modes

`--processing {auto,agentic,static}`, default `auto`. Auto picks **agentic**
when all of these hold, and **static** otherwise:

| Condition | Why |
|---|---|
| `GEMINI_API_KEY` is set | agentic runs on the Gemini Developer API; Vertex's Interactions endpoint does not accept any model yet |
| the model is `gemini-3.7-flash`, `gemini-3.6-flash` or `gemini-3.5-flash-lite` | the only models that support `processing: "agentic"` |
| the source is a YouTube URL/id or a local file | `gs://` objects can only be read by Vertex |
| neither `--clip` nor `--fps` is given | those steer the static frame sampler; the agentic model samples on its own |

When auto falls back to static even though a key is present, stderr says why:

```
processing: static — --clip is static-only
processing: static — gemini-2.5-pro is not an agentic model (supported: gemini-3.7-flash, gemini-3.6-flash, gemini-3.5-flash-lite)
processing: static — agentic mode does not support Cloud Storage (gs://) sources
```

`--processing agentic` with one of those conditions unmet is an error with the
same reason. `--processing static` forces the Vertex path and never prints the
line. Force static when you want exactly what it gives you: a `gs://` source,
one `--clip` span, a fixed `--fps` sampling rate, or a bill that scales
predictably with runtime rather than with what the model chose to look at.

The stderr usage line tells you which path ran. Agentic adds a `tool` bucket
for the timeline navigation:

```
tokens: 241 in + 1,602 thinking + 287 tool + 1,320 out = 3,450 total      # agentic
tokens: 287,866 in + 1,534 thinking + 1,019 out = 290,419 total           # static
```

## Long videos

In agentic mode there is no long-video problem: the model pulls in the parts
of the timeline it needs, so a 57-minute podcast is a few thousand tokens
whether you ask for claims or a full transcript (measured numbers under
[Cost](#cost)). Everything below applies to the static path.

Gemini tokenizes video at a fixed rate — on `gemini-2.5-flash` roughly 258
tokens per sampled frame (one frame a second by default) plus 32 tokens a
second of audio; the 3.x models tokenize video about 3× cheaper — so an hour of
talking heads is ~1.05M tokens on 2.5-flash, just over the 1,048,576-token
window, and Vertex rejects the request with "The input token count exceeds the
maximum number of tokens allowed". When tubetell knows the runtime (ffprobe for
a file, `videos.list` for YouTube when `YOUTUBE_API_KEY` is set) it sizes the
request itself: first it samples frames at low resolution (66 tokens a frame —
fine for anything where the words matter more than the pixels), and if that
still won't fit it analyzes the video in consecutive clips. `transcript` and
`claims` output is concatenated; every other mode gets one merge pass so you
still receive a single answer. Without a runtime it can't plan, so the 400
comes back with a hint: set the key, lower `--fps`, or pass `--clip`.

A response is capped at 65,535 output tokens in both modes; if an answer hits
that cap tubetell says so on stderr rather than handing you a silently
truncated transcript.

## Sources

| Source | Example | How it travels |
|---|---|---|
| YouTube | `https://youtu.be/vOVKnYoH1p4`, `vOVKnYoH1p4` | agentic: the URL goes to the Interactions API; static: Vertex fetches it (public videos only) |
| Cloud Storage | `gs://my-bucket/clip.mp4` | Vertex only — a URI it reads from the bucket (static) |
| Local file | `~/recording.mov`, `clip.mp4` | agentic: uploaded as-is via the Files API; static: proxied by ffmpeg, then sent inline |

Local video and audio both work — `.mov`, `.mp4`, `.webm`, `.mkv`, `.avi`,
`.mpeg`, `.flv`, `.wmv`, `.3gp`, `.mp3`, `.wav`, `.m4a`, `.aac`, `.ogg`,
`.flac`. Only `--mode comments` is YouTube-only; it reads a comment section,
which a local file doesn't have.

### Local files, agentic: the Files API

The file is uploaded unchanged to the Gemini Files API (`--width` and
`--no-transcode` don't apply — there is no proxy), polled until it is ACTIVE
(tubetell gives up after 5 minutes), used for the one request, then deleted.
Files expire server-side after 48 hours anyway, so a failed delete only warns.
stderr shows the progress:

```
uploading recording.mp4 (11 MB)...
processing...
tokens: 200 in + 457 thinking + 706 tool + 188 out = 1,551 total
```

### Local files, static: the proxy

A Vertex request can only carry about 12 MiB of media, and a phone or screen
recording is usually far bigger — so anything over the cap is re-encoded
first, to 1280px wide at 2 fps (H.264/AAC). That is not much of a compromise:
Gemini samples video at roughly 1 frame per second and downscales it anyway,
so a 120 fps retina capture spends its bytes on detail the model never sees. A
3-minute, 322 MB screen recording becomes a 2.3 MB proxy.

Proxies are cached in your temp directory, keyed by the file's size and mtime,
so iterating on prompts against one video only transcodes once. This needs
`ffmpeg` on PATH (`brew install ffmpeg`).

```bash
tubetell clip.mov --processing static --width 1920    # sharper proxy, bigger request
tubetell clip.mov --processing static --no-transcode  # send as-is; fails if over ~12 MiB
```

If even the coarsest proxy won't fit — an hour of footage, say — tubetell says
so and tells you to work in pieces with `--clip`.

### Framing what Gemini looks at (static)

Both work with every source, both move the bill, and either one switches the
run to static:

```bash
tubetell <source> --clip 1:30-2:45   # only this span (ss, mm:ss, hh:mm:ss)
tubetell <source> --fps 2            # frames per second to look at
```

`--fps` defaults to Gemini's own ~1/s. Raise it for fast-moving footage, lower
it (`--fps 0.5`) to halve the video tokens on something slow.

There is no resolution knob, because for video there is nothing to turn: the
Gemini models accept `MEDIA_RESOLUTION_HIGH` only for single images and reject
the request outright for video. Small on-screen text still reads surprisingly
well at the default — but when it doesn't, the fix is to crop the region you
care about before handing the file over, not to send more pixels of the whole
frame.

## Install

```bash
pipx install git+https://github.com/chhib/tubetell
# or run without installing:
uvx --from git+https://github.com/chhib/tubetell tubetell --help
```

Requires Python 3.10+. The dependency pin `google-genai>=2.21.0` matters: that
is the first release that sends the `processing` field the agentic path needs.

## Setup

You need at least one of two credentials — either alone is enough to run, and
each unlocks a different set of features:

| Credential | Enables |
|---|---|
| `GEMINI_API_KEY` — a [Google AI Studio](https://aistudio.google.com/apikey) key | agentic processing (YouTube and local files) |
| `GOOGLE_CLOUD_PROJECT` with the Vertex AI API enabled, plus ADC or a service-account key | static processing (all sources, `--clip`, `--fps`, `gs://`) and `--mode comments` |

With only `GEMINI_API_KEY` set, anything that needs Vertex fails with a
message saying so; with only a project set, everything runs static. tubetell
reads configuration from, in order of precedence (see
[`.env.example`](.env.example)):

1. real environment variables
2. the nearest `.env` walking up from the directory you run it from
3. the file named by `$TUBETELL_ENV`
4. `~/.config/tubetell/.env` (or `$XDG_CONFIG_HOME/tubetell/.env`)

Put your keys in `~/.config/tubetell/.env` once and tubetell works from any
directory. A relative `GOOGLE_APPLICATION_CREDENTIALS` is resolved against the
`.env` file that sets it, not the cwd.

```bash
export GEMINI_API_KEY=...                  # agentic
export GOOGLE_CLOUD_PROJECT=my-project-id  # static + comments
export GOOGLE_CLOUD_LOCATION=global        # optional, this is the default
```

Leave `GOOGLE_CLOUD_LOCATION` at `global`: on Vertex the Gemini 3.x models are
only served from there. A regional value such as `europe-west1` gets a 404,
which tubetell annotates:

```
hint: gemini-3.7-flash is not served from europe-west1; set GOOGLE_CLOUD_LOCATION=global (or use --model gemini-2.5-flash).
```

### Vertex auth, two ways

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
Neither is needed for the agentic path — the AI Studio key is the whole
credential there.

### Comments mode only

`--mode comments` reads the comment section via the YouTube Data API v3, which
needs an API key (the other modes don't), and analyzes it on Vertex:

```bash
export YOUTUBE_API_KEY=...
```

Enable "YouTube Data API v3" in your Google Cloud project and create an API key
restricted to it. Each run costs ~1 quota unit per 100 comments — negligible
against the 10k/day default quota.

## Cost

tubetell prints a usage line to stderr on every run, so you can watch your own
spend (stdout stays clean for piping):

```
tokens: 241 in + 1,602 thinking + 287 tool + 1,320 out = 3,450 total      # agentic
tokens: 287,866 in + 1,534 thinking + 1,019 out = 290,419 total           # static
```

### Agentic

The model reads only what it needs, so tokens no longer scale with runtime.
Measured on a 57:32 podcast (`vOVKnYoH1p4`) with `gemini-3.7-flash`:

| Run | Tokens | Wall time |
|---|---|---|
| `--mode claims` (33 claims, 01:29–54:17) | `241 in + 1,602 thinking + 287 tool + 1,320 out = 3,450 total` | 14 s |
| `--mode transcript` (381 lines, 00:00→57:30, complete) | `289 in + 2,174 thinking + 335 tool + 16,141 out = 18,939 total` | 57 s |
| the same video static, low-res frames, `summary` | ~314,000 input tokens (transcript would need the clip planner) | 56 s |

That is roughly 90 % fewer tokens on an hour-long video. The bill falls less
than the token count does, because most of what remains is thinking and
output, billed at the output rate rather than the input rate — call it about
two-thirds cheaper, not 90 %. A short local file is cheaper still: an 11 MB,
7-second `.mp4` came to `200 in + 457 thinking + 706 tool + 188 out = 1,551
total` in 23 s including the upload.

### Static

Here the whole video is tokenized on every call, so cost scales with **video
length**, not with the question. Measured on `gemini-2.5-flash`:

| | tokens per second of video |
|---|---|
| video frames (1 frame/s at default resolution) | 258 |
| audio track | 25 |
| **total** | **≈283** (≈1M tokens per hour of video) |

`gemini-3.7-flash` tokenizes video about 3× cheaper even on this path — the
same 30-second clip on Vertex was 2,880 input tokens on 3.7-flash against 8,639
on 2.5-flash. Two consequences either way:

- **You pay for the whole video on every run.** Vertex re-fetches and
  re-tokenizes it each call — there is no caching. Want several answers about
  one video? Bundle them into **one `--prompt`** — five separate runs pay for
  the video five times.
- **The answer is nearly free by comparison.** Even a 7,486-token full
  transcript is a rounding error next to ~288k input tokens.

Per-token prices differ by model and change; take them from the
[Vertex AI pricing page](https://cloud.google.com/vertex-ai/generative-ai/pricing)
rather than from here. As a scale marker, the six-run static experiment in
[docs/example.md](docs/example.md) — every mode plus a custom prompt on a
16:57 video, ~288k input tokens per video run on `gemini-2.5-flash` — came to
about $0.57 at July 2026 rates, roughly $0.006 per minute of video per
question.

Practical upshots:

- `comments` mode reads the YouTube Data API instead of the video, so it is a
  flat few thousand tokens regardless of video length.
- **Local files are re-tokenized every run too.** The proxy saves upload time
  and request size, not tokens — a 1280px frame and a 4K frame cost the same.
  The static levers are `--clip` (pay for less runtime) and `--fps 0.5` (pay
  for fewer frames). A silent video also skips the audio tokens: a 3:01 screen
  recording with no audio track came to 46,871 input tokens at `--fps 1` on
  2.5-flash — 259 per second of video, matching the rate above.

## Troubleshooting

**A partial transcript that claims to be complete (agentic).** On the first
fetch of a YouTube video the Developer API has not cached yet, it can return a
truncated transcript with status `completed`
([googleapis/python-genai #1898](https://github.com/googleapis/python-genai/issues/1898)).
Run the same command again; the second pass usually returns the full one.

**`Unsupported model interaction`.** An Interactions call reached Vertex, which
does not serve that endpoint yet. Check that `GEMINI_API_KEY` is set and
actually reaching tubetell (config precedence under [Setup](#setup)), and what
`--processing` you passed.

**404 on a Gemini 3.x model from Vertex.** The 3.x models are only served from
the `global` location. Set `GOOGLE_CLOUD_LOCATION=global` (or unset it — that
is the default); tubetell prints the hint when it recognizes the case.

**`--processing agentic is not possible here: ...`.** One of the auto
conditions in [Processing modes](#processing-modes) is unmet; the message
names which. Drop the flag to let auto fall back to static, or fix the
condition (set the key, pick a 3.x model, drop `--clip`/`--fps`, use a
non-`gs://` source).

**`500 INTERNAL` from Vertex, repeatedly (static).** Vertex re-fetches the
YouTube URL on every call, and analyzing the same video many times in a short
window gets the fetch rate-limited downstream. tubetell backs off and retries
(4s → 8s → 16s) automatically; if it still fails, wait a minute and retry, or
try a different video. Switching regions does not help.

**`Server disconnected without sending a response.`** A transient drop while a
request carrying inline media uploads. tubetell retries it on the same backoff
as the 500s; if it keeps happening, wait a minute.

**`the model supports HIGH media resolution only for single images`.** Nothing
in tubetell sets per-frame resolution to HIGH — see
[Framing what Gemini looks at](#framing-what-gemini-looks-at-static). If you
see this, something else in your environment is setting `media_resolution`.

**Long videos (static).** Cost and latency scale with video length, not with
the question — see [Cost](#cost). A local file that no proxy can squeeze under
the request cap has to be worked in `--clip` slices — or set `GEMINI_API_KEY`
and let agentic mode read it whole.

**Private/unlisted videos** can't be analyzed — both paths fetch the video
server-side and only public videos are supported. Download it and pass the
file instead.

**`ffmpeg is not installed`.** Only local files over ~12 MiB on the static
path need it: `brew install ffmpeg`.

## Development

```bash
git clone https://github.com/chhib/tubetell && cd tubetell
python3 -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/pytest
```

## License

MIT
