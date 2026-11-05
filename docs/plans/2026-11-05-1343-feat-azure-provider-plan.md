---
title: Azure Provider - Plan
type: feat
date: 2026-11-05
artifact_readiness: implemented-untested-live
execution: code
---

# Azure Provider - Plan

## Goal

The same CLI runs on the company's Azure AI Foundry resource, so meeting
recordings stay in the company tenant: `--provider {gemini,azure}`, default
`gemini`, nothing changes for existing users.

## Requirements

- R1. `--provider azure` accepts a local file, a YouTube URL/id, or any page
  yt-dlp can download (downloaded first). `gs://` and `--mode comments` fail
  with a clear error; so does `--processing` other than `auto`.
- R2. Step 1: the audio track is transcribed with speakers by the transcription
  deployment (`diarized_json`), in chunks that fit one request, merged on the
  source's clock. Speaker labels are per chunk and generic; names are never
  invented.
- R3. Step 2: stills every 10 s plus scene changes, ≤1024 px, JPEG, each with
  its timestamp; `--fps` and `--clip` respected; at most 50, thinned evenly.
- R4. Step 3: transcript + frames (each preceded by its timestamp) + mode prompt
  or `--prompt` to the chat deployment, carrying the timestamp rule, the speaker
  rule and the runtime upper bound. `--mode transcript` without `--prompt` skips
  this step and formats the transcript directly.
- R5. Auth: Entra ID via `DefaultAzureCredential` and
  `get_bearer_token_provider(..., "https://cognitiveservices.azure.com/.default")`;
  `AZURE_OPENAI_API_KEY` is a fallback. Nothing prints a token or key.
- R6. Config: `AZURE_OPENAI_ENDPOINT` (required), `AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT`
  (default `gpt-4o-transcribe-diarize`), `AZURE_OPENAI_CHAT_DEPLOYMENT` (default
  `gpt-6.1-terra`); `--model` overrides the chat deployment.
- R7. stderr usage: a `transcribed:` line (audio seconds, requests) and the
  usual `tokens:` line for the chat request.

## Key decisions

- KTD1. **One module, `src/tubetell/foundry.py`**, owns the Azure path, as
  `agentic.py` owns the Interactions path. Named `foundry`, not `azure`, so it
  never shadows the `azure` namespace package. `cli.analyze_azure` only
  resolves the source to a local file.
- KTD2. **The v1 API (`<endpoint>/openai/v1/`) through the plain `openai.OpenAI`
  client**, with a callable `api_key` (the bearer token provider). No
  `api-version` to pin. The Azure REST reference documents
  `POST /openai/v1/audio/transcriptions` with `diarized_json` and
  `chunking_strategy`.
- KTD3. **Chunks of 1200 s with 10 s overlap.** Under the 25 MB file limit by a
  factor of five (32 kbps mono MP3) and under the ~1400 s duration cap reported
  for the diarize model (not in the official docs). A segment in an overlap is
  kept by the chunk whose owned half of the overlap holds its midpoint.
- KTD4. **Per-chunk speaker labels `Speaker N-A`.** The diarizer's letters are
  only consistent inside one request. The chat prompt explains the labels and
  asks for reconciliation only where content makes it clear; a plain transcript
  keeps them as they are.
- KTD5. **One ffmpeg pass for frames**, `fps=2,select=…,scale,showinfo`, with
  timestamps parsed from showinfo's `pts_time` plus the span start (input
  seeking resets the clock).
- KTD6. **Timestamps stay on the source's clock, also for `--clip`**; the bound
  is the end of the span analyzed.

## Verification

- Unit tests (`tests/test_foundry.py`, `tests/test_cli.py`): chunk planning,
  offsets and merging, diarized_json → transcript format (fixture shaped like
  the documented response), frame select expression and showinfo parsing,
  thinning, prompt assembly, chat content, usage lines, settings and provider
  selection, error messages. Network mocked throughout.
- The ffmpeg commands were checked on a synthetic 45 s test clip.
- **Untested:** every live call (transcription, chat, Entra token). The
  data-plane role was still pending, so a live call returns 401.

## Sources

- Azure REST reference, Create a transcription:
  https://learn.microsoft.com/en-us/azure/foundry/reference/rest/audio-create-transcription
- OpenAI speech-to-text guide (diarization, `chunking_strategy` required over
  30 s, 25 MB, deprecation date): https://developers.openai.com/api/docs/guides/speech-to-text
- Azure transcription quickstart (25 MB limit):
  https://learn.microsoft.com/en-us/azure/foundry/openai/whisper-quickstart
- ~1400 s diarize chunk limit (informal): https://ndurner.github.io/gpt-4o-diarize
