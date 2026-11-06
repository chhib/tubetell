# Azure provider

`--provider azure` runs tubetell on an Azure AI Foundry (Azure OpenAI) resource
instead of Gemini, so a meeting recording never leaves your company's tenant.
The modes, `--prompt`, the transcript format and the timestamp and speaker
rules are the same.

```bash
tubetell meeting.mp4 --provider azure                      # summary
tubetell meeting.mp4 --provider azure --mode transcript --out t.md
tubetell meeting.mp4 --provider azure --prompt "Which decisions were made, by whom?"
tubetell meeting.mp4 --provider azure --clip 10:00-25:00 --fps 0.2
```

## How it works

No Azure model reads video, so tubetell takes the media apart locally with
ffmpeg and sends the pieces:

1. **Audio → transcript with speakers.** The audio track is cut to mono 16 kHz
   32 kbps MP3 in 20-minute chunks that overlap by 10 s (5-minute chunks, sent
   several at a time, with `--speaker`; see below). Each chunk goes to the
   transcription deployment with `response_format="diarized_json"` and
   `chunking_strategy="auto"`. Segment times are moved onto the source's clock
   and the chunks are merged; a segment in an overlap is kept once, by the
   chunk that holds its midpoint.
2. **Frames.** Stills are sampled across the runtime: one every 10 s plus one
   at each scene change (a new slide), never two within 2 s, scaled to at most
   1024 px wide, JPEG. `--fps` replaces this with a fixed rate (`--fps 0.2` is
   one frame every 5 s, no scene detection). More than 50 frames are thinned
   evenly, keeping the first and the last.
3. **Answer.** The mode prompt (or `--prompt`), the timestamp and speaker rules,
   the timestamped transcript, and the frames — each preceded by its timestamp
   as text — go to the chat deployment in one request. The runtime (or the end
   of `--clip`) is the upper bound for every timestamp.

`--mode transcript` without `--prompt` stops after step 1: tubetell formats the
transcript itself (`[mm:ss] Speaker A: text`) and makes no chat request.

Sources: local files work as-is. A YouTube URL or id, an X post, or any other
page yt-dlp can read is downloaded first (`brew install yt-dlp`), the same way
the Gemini provider downloads non-YouTube pages; the video's description or the
post's text becomes the speaker roster. `gs://` objects are not supported, and
neither is `--mode comments`, which reads YouTube comments through Gemini.

The pieces live in a temporary directory that is deleted when the run ends.
The transcript is kept, see below.

## The transcript is made once

Transcription is the slow and expensive step (about a minute and 0.03 USD per
minute of audio), and one recording is usually asked several things. So the
diarized segments are written once, as JSON, to
`~/.cache/tubetell/transcripts/<content hash>/<deployment>-<start>-<end>.json`
(`$XDG_CACHE_HOME` replaces `~/.cache`), and every later run on the same file
reads them back:

```
transcript: cached (~/.cache/tubetell/transcripts/3f2a…/gpt-4o-transcribe-diarize-0-2096.json, 518 lines)
```

The key is the file's content, not its name, so a copy or a renamed file hits
the same cache. A `--clip` inside a cached span is sliced from it without a
request; a span the cache does not cover is transcribed and cached on its own.
A different transcription deployment gets its own file. Delete the file to
transcribe again.

### Glossary: `--glossary`

The transcription model mishears names and products, and the diarize model
rejects the `prompt` field a glossary would normally go in (HTTP 400, "Prompt
is not supported for diarization models"). So the glossary is applied after
transcription: one text-only chat request gets the numbered transcript and the
terms, and answers with the replacements it would make, as JSON. tubetell
applies them itself, and only a replacement whose target is a listed term and
whose source is really in that line; every change is printed:

```
glossary: checking 71 lines against 10 terms...
tokens: 2,024 in + 1,068 thinking + 92 out = 3,184 total
glossary: 4 changes
  [00:47] "den deluxe" -> "Benelux"
  [01:37] "smanska laget" -> "semantiska lagret"
  [01:57] "VDBT" -> "dbt"
  [02:16] "semanska laget" -> "semantiska lagret"
```

```bash
tubetell meeting.mp4 --provider azure --glossary terms.txt
```

`terms.txt` holds one term per line; `#` starts a comment. Without the flag,
`~/.config/tubetell/glossary.txt` is used when it exists (`$XDG_CONFIG_HOME`
replaces `~/.config`). The corrected transcript is what the cache holds, under
its own key (a hash of the terms), so the plain transcript and the corrected
one do not mix. The request costs about 30,000 tokens for a 35-minute meeting.

**`--transcript <file>`** skips transcription and uses a finished transcript
instead: the cached JSON, or tubetell's own text format (`[mm:ss] Speaker:
text`, one line per segment, as `--mode transcript` writes it). Correct the
names and the terms the model misheard in that file once, and every later
answer, the ticket prompt included, is built on the corrected text. With
`--clip` the file is sliced to the span.

```bash
tubetell meeting.mp4 --provider azure --mode transcript --out t.md   # transcribes once
# fix names in t.md, then:
tubetell meeting.mp4 --provider azure --transcript t.md --prompt "Which decisions were made, by whom?"
```

## Speakers

The diarizer labels voices `A`, `B`, … per request, so `A` in one 20-minute
chunk is not necessarily `A` in the next. tubetell does not pretend they line
up. With one chunk the labels are `Speaker A`, `Speaker B`; with several they
carry the chunk number, `Speaker 2-A` meaning voice A of part 2. In every mode
except a plain transcript, the chat model is told this and asked to merge labels
only where the content makes it clear they are the same voice. Names follow the
[speaker rule](../rules.md#speakers): only names spoken in the transcript,
legible in a frame, or listed in the description are used.

### Known speakers: `--speaker`

Give the diarizer a few seconds of a voice and a name, and it labels that voice
with the name, in every chunk:

```bash
# cut 2-10 s of each voice (alone) from this recording, keep them, and use them
tubetell meeting.mp4 --provider azure --mode transcript \
    --speaker "Ada=0:01-0:10" --speaker "Bob=1:26-1:35"
# later recordings: the kept clips by name
tubetell other.mp4 --provider azure --speaker Ada --speaker Bob
```

`Name=START-END` cuts that span from the recording (mono 16 kHz MP3, like the
audio chunks) and keeps it as `~/.config/tubetell/speakers/Name.mp3`
(`$XDG_CONFIG_HOME` replaces `~/.config`); `Name` alone reuses the kept file.
At most four per run (the model's limit). The names and clips go with every
transcription request as `known_speaker_names` / `known_speaker_references`.
A known voice is labelled with its name (`[01:26] Ada: …`), an unknown one
keeps the generic label. The chat model is told that a name label is a voice
match and that only generic labels need reconciling. A transcript made with
known speakers is cached under its own key (the names), next to the plain one.

A reference should be one person talking, 2 to 10 s, from the same kind of
recording (a meeting voice sounds different from a phone voice).

**Short chunks, several at a time.** Without known speakers the chunks are 20
minutes long, one after the other, because every chunk boundary is a new set
of labels to reconcile. With `--speaker` the names are stable across chunks,
so the audio is cut in 5-minute chunks (same 10 s overlap and merge) and sent
several at a time: the first chunk alone, then the rest with as many requests
at once as the deployment's requests-per-minute header allows
(`x-ratelimit-limit-requests` / 2,000, at most 8; 2 when the header is
missing). A 5-minute chunk takes about 100 s, so a 35-minute recording
transcribes in two to three minutes instead of twenty.

```
transcribing 1/7 00:00-05:00 (1.1 MB)...
  6 more chunks, 8 at a time
transcribing 2/7 04:50-09:50 (1.1 MB)...
```

## Deployments

| Setting | Default | Used for |
|---|---|---|
| `AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT` | `gpt-4o-transcribe-diarize` | step 1, speech to text with speakers |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | `gpt-6.1-terra` | step 3, the answer (text + image input) |

`--model <deployment>` overrides the chat deployment for one run. The chat
step talks to the resource's v1 API (`<endpoint>/openai/v1/`), which takes no
`api-version` parameter. The transcription step uses the classic deployment
route (`<endpoint>/openai/deployments/<name>/audio/transcriptions`,
`api-version=2025-03-01-preview`): on v1 the diarize model answers 404
DeploymentNotFound.

## Setup

```bash
az login                                   # Entra ID; nothing else to store
export AZURE_OPENAI_ENDPOINT=https://<resource>.cognitiveservices.azure.com
```

Put `AZURE_OPENAI_ENDPOINT` in `~/.config/tubetell/.env` once (see
[Configuration](../../README.md#configuration)). Gemini credentials are not
needed for this provider. ffmpeg and ffprobe must be on PATH
(`brew install ffmpeg`).

**Auth is Entra ID.** tubetell uses `DefaultAzureCredential` with the
`https://cognitiveservices.azure.com/.default` scope, so your `az login`
session is enough. Anyone on the team can use it with their own login once
they have the **Cognitive Services OpenAI User** role on the resource (a new
role assignment can take a few minutes to apply). Without the role, every call
returns 401 or 403 and tubetell says which role is missing.

**API key, fallback only.** If `AZURE_OPENAI_API_KEY` is set, tubetell uses it
instead of Entra ID. Keep a 1Password reference in `.env`, not the key, and run
under `op run`:

```bash
# .env: AZURE_OPENAI_API_KEY=op://<vault>/<item>/credential
op run --env-file=.env -- tubetell meeting.mp4 --provider azure
```

Neither the token nor the key is ever printed.

## Limits

| Limit | Value | How tubetell handles it |
|---|---|---|
| Transcription file size | 25 MB per request | 20-minute chunks are ~4.8 MB |
| Transcription duration | not documented; reported as ~1400 s for the diarize model | 20-minute (1200 s) chunks; 5-minute with `--speaker` |
| Speaker labels | consistent within one request only | per-chunk labels, reconciled in step 3 |
| Frames per chat request | 50 (tubetell's cap; ~1,000 input tokens each at high detail) | thinned evenly |
| Model lifetime | OpenAI lists `gpt-4o-transcribe-diarize` as deprecated, shutting down 2027-02-26 (Azure retirement dates may differ) | point `AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT` at its successor |

The OpenAI client retries 429s and 5xx itself (up to 4 times) and gives each
request ten minutes.

## Cost

stderr shows what each step used:

```
transcribed: 3,452 s audio in 3 requests
tokens: 61,204 in + 1,830 thinking + 2,115 out = 65,149 total
```

Transcription is billed by audio duration; the chat request by tokens, where
frames are most of the input. `--clip` cuts both; `--fps` with a low rate cuts
the frames.

## Troubleshooting

**`Azure OpenAI error 401` / `403`.** Your identity lacks the Cognitive Services
OpenAI User role on the resource, or `az login` is on another tenant
(`az login --tenant <tenant-id>`).

**`Azure OpenAI error 404`.** The endpoint is wrong, or the deployment name
does not exist on that resource.

**`Could not get an Entra ID token`.** Run `az login`, or set
`AZURE_OPENAI_API_KEY`.
