# Gemini provider

The default provider (`--provider gemini`, or no flag at all).

Gemini reads the video itself (no download, no transcription step) and answers
a prompt about the content: what's said, what's shown, what's claimed, how the
tone shifts. It gets at the video one of two ways. **Agentic**: through the
Gemini Interactions API, where the model navigates the timeline and loads
transcript and frames on demand, so an hour-long video costs a few thousand
tokens. **Static**: through `generate_content` on Vertex AI, where every sampled
frame and second of audio is tokenized up front. tubetell picks agentic when it
can and static otherwise — see [Processing modes](#processing-modes) below. A
separate mode reads the comment section instead and analyzes audience
sentiment.

The default model is `gemini-3.8-flash`; `--model <model>` picks another.
[docs/example.md](../example.md) is a worked example: all five modes plus a
custom prompt on one video, in both processing modes, with real outputs and
token counts.

## Processing modes

`--processing {auto,agentic,static}`, default `auto`. Auto picks **agentic**
when all of these hold, and **static** otherwise:

| Condition | Why |
|---|---|
| `GEMINI_API_KEY` is set | agentic runs on the Gemini Developer API; Vertex's Interactions endpoint does not accept any model yet |
| the model is `gemini-3.8-flash` (default), `gemini-3.7-flash`, `gemini-3.6-flash` or `gemini-3.5-flash-lite` | the only models that support `processing: "agentic"` |
| the source is a YouTube URL/id or a local file | `gs://` objects can only be read by Vertex |
| neither `--clip` nor `--fps` is given | those steer the static frame sampler; the agentic model samples on its own |

When auto falls back to static even though a key is present, stderr says why:

```
processing: static — --clip is static-only
processing: static — gemini-2.5-pro is not an agentic model (supported: gemini-3.8-flash, gemini-3.7-flash, gemini-3.6-flash, gemini-3.5-flash-lite)
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
tokens: 2,377 in + 21,663 thinking + 5,513 tool + 5,232 out = 34,785 total  # agentic
tokens: 287,866 in + 1,534 thinking + 1,019 out = 290,419 total            # static
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
still won't fit it analyzes the video in consecutive clips and merges the answers
so you still receive a single answer. Without a runtime it can't plan, so the 400
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
| Any other video page | `https://x.com/<user>/status/<id>`, Vimeo | downloaded once with yt-dlp, then read as a local file |

Local video and audio both work — `.mov`, `.mp4`, `.webm`, `.mkv`, `.avi`,
`.mpeg`, `.flv`, `.wmv`, `.3gp`, `.mp3`, `.wav`, `.m4a`, `.aac`, `.ogg`,
`.flac`. An audio file on the agentic path is uploaded the same way but sent as
an audio part — the on-demand `processing` mode is video-only, so audio is
tokenized in full (about 32 tokens a second). Only `--mode comments` is
YouTube-only; it reads a comment section, which a local file doesn't have.

### Other sites: downloaded with yt-dlp

Vertex reads a YouTube URL by reference, but an X post or a Vimeo page is just
HTML to it. Any other `http(s)` URL is therefore downloaded with `yt-dlp`
(`brew install yt-dlp`) into `$TMPDIR/tubetell-downloads/`, keyed by URL, and
then handled exactly like a local file — a second run on the same URL skips
the download. tubetell asks for at most 720p (the proxy is 640–1280px anyway)
and eight concurrent fragments: X serves video as HLS fragments, and an hour
at 1080p one fragment at a time took nine minutes. The post's text from
yt-dlp's info JSON is the speaker roster, the way a YouTube description is.

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

If even the coarsest proxy won't fit — an hour of footage, say — tubetell
splits the runtime into equal spans sized so each fits at 640px / 1 fps (20%
under the cap, since some stretches compress worse than others), sends them one
after another, and stitches the answers back together. Each span's proxy
starts at 0:00, so its timestamps are shifted back onto the source's clock.
`transcript` and `claims` are concatenated in order; other modes get one merge
request. A file whose estimated proxy (about 0.75 MiB a minute at 640px / 1
fps) is more than twice the cap skips the whole-file attempts and is split
straight away. A 58-minute talk came out as five spans of about 11½ minutes,
each an 8–9 MiB proxy; trying it whole first had cost four full transcodes.

Spans are sent one at a time on purpose. Four 15-minute spans sent in parallel
spent the project's tokens-per-minute quota (429) and one of them hung; tubetell
now waits out a 429 (30 s, then doubling) and gives any request ten minutes
before retrying it.

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

## Setup

You need at least one of two credentials — either alone is enough to run, and
each unlocks a different set of features:

| Credential | Enables |
|---|---|
| `GEMINI_API_KEY` — a [Google AI Studio](https://aistudio.google.com/apikey) key | agentic processing (YouTube and local files) |
| `GOOGLE_CLOUD_PROJECT` with the Vertex AI API enabled, plus ADC or a service-account key | static processing (all sources, `--clip`, `--fps`, `gs://`) and `--mode comments` |

With only `GEMINI_API_KEY` set, anything that needs Vertex fails with a
message saying so; with only a project set, everything runs static. Where
tubetell looks for these settings is under
[Configuration](../../README.md#configuration) in the README.

```bash
export GEMINI_API_KEY=...                  # agentic
export GOOGLE_CLOUD_PROJECT=my-project-id  # static + comments
export GOOGLE_CLOUD_LOCATION=global        # optional, this is the default
```

Leave `GOOGLE_CLOUD_LOCATION` at `global`: on Vertex the Gemini 3.x models are
only served from there. A regional value such as `europe-west1` used to 404 every
call; tubetell now coerces it to `global` for those models and says so once on
stderr, so a stale regional setting from another project no longer breaks a run:

```
location: europe-west1 -> global (gemini-3.8-flash is only served from global)
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
tokens: 2,377 in + 21,663 thinking + 5,513 tool + 5,232 out = 34,785 total  # agentic
tokens: 287,866 in + 1,534 thinking + 1,019 out = 290,419 total            # static
```

### Agentic

The model reads only what it needs, so tokens no longer scale with runtime.
Measured on a 57:32 podcast (`vOVKnYoH1p4`) with `gemini-3.8-flash` (default):

| Run | Tokens | Wall time | Approx cost |
|---|---|---|---|
| `--mode claims` (45 claims, 01:28–54:17) | `2,377 in + 21,663 thinking + 5,513 tool + 5,232 out = 34,785 total` | 45 s | ~$0.10 |
| `--mode transcript` (complete, 00:00→57:30) | `275 in + 4,029 thinking + 321 tool + 13,350 out = 17,975 total` | 50 s | ~$0.065 |
| the same video static, low-res frames, `summary` | ~314,000 input tokens (transcript would need the clip planner) | 56 s | ~$0.094 |

That is roughly 90–98 % fewer video input tokens on an hour-long video. Most of
what is billed is thinking and output tokens at the generation rate. On a 17-minute
video (`LuA3FG-VCSs`), a default `summary` runs in 21 s and takes `8,020 total`
tokens (~$0.027). A short local file is cheaper still: an 11 MB, 7-second `.mp4`
came to `200 in + 457 thinking + 706 tool + 188 out = 1,551 total` in 23 s
including the upload.

### Static

Here the whole video is tokenized on every call, so cost scales with **video
length**, not with the question. Measured on `gemini-2.5-flash`:

| | tokens per second of video |
|---|---|
| video frames (1 frame/s at default resolution) | 258 |
| audio track | 25 |
| **total** | **≈283** (≈1M tokens per hour of video) |

The 3.x models (`gemini-3.8-flash`, `gemini-3.7-flash`) tokenize video about 3×
cheaper even on this path — the same 30-second clip on Vertex was 2,880 input
tokens on 3.x against 8,639 on 2.5-flash. Two consequences either way:

- **You pay for the whole video on every run.** Vertex re-fetches and
  re-tokenizes it each call — there is no caching. Want several answers about
  one video? Bundle them into **one `--prompt`** — five separate runs pay for
  the video five times.
- **The answer is nearly free by comparison.** Even a 7,486-token full
  transcript is a rounding error next to ~288k input tokens.

Per-token prices differ by model and change; take them from the
[Vertex AI pricing page](https://cloud.google.com/vertex-ai/generative-ai/pricing)
rather than from here. As a scale marker, the six-run static experiment in
[docs/example.md](../example.md) — every mode plus a custom prompt on a
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
actually reaching tubetell (config precedence under [Configuration](../../README.md#configuration)), and what
`--processing` you passed.

**404 on Vertex.** Tubetell automatically coerces Gemini 3.x models to the
`global` location (and reports `location: <region> -> global` on stderr if a
regional location was set). If Vertex still returns a 404, verify that the Vertex
AI API is enabled in your Google Cloud project and that the requested model is
accessible.

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
the request cap is split into spans automatically (see
[the proxy](#local-files-static-the-proxy)) — or set `GEMINI_API_KEY` and let
agentic mode read it whole.

**Private/unlisted videos** can't be analyzed — both paths fetch the video
server-side and only public videos are supported. Download it and pass the
file instead.

**`ffmpeg is not installed`.** Only local files over ~12 MiB on the static
path need it: `brew install ffmpeg`.
