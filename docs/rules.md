# Timestamps, speakers and quotes

Rules every answer carries, whichever provider runs it. The Azure pipeline adds
its own notes on top; see [providers/azure.md](providers/azure.md#speakers).

## Timestamps

Every video-mode request — preset or `--prompt` — carries a timestamp rule,
because left alone Gemini writes `mm:ss` past the hour mark (1:47:00 comes back
as `47:00` or `107:00`) and will cite a position past the end of the video. The
rule pins one format per answer (`mm:ss` in the first hour, `h:mm:ss` after it)
and tells the model to drop the timestamp rather than guess. When the runtime
can be established — ffprobe for a local file, `videos.list` for a YouTube URL
if `YOUTUBE_API_KEY` happens to be set — it goes in the prompt as a hard upper
bound. Both lookups are best effort: no ffprobe or no key just means no bound.

## Speakers

Every video-mode request also carries a **speaker rule**, for the same reason.
Gemini will not say it does not know who is talking: asked to attribute a line
it invents a plausible name from the video's subject area and then uses it
consistently for the whole answer, which is what makes the mistake survive
review. On a Swedish finance podcast it produced "Anders Malmström" — a name
that reads as real and never wavers — and two runs over the same video invented
*different* names, so the transcript and the claims list disagreed about who
said what. The rule allows only names actually heard in the audio or seen on
screen, and requires a stable generic label (`Host`, `Guest`, `Speaker 1`)
otherwise.

Where the name can be checked, it is: the same `videos.list` call that fetches
the runtime now also fetches the **description**, and the participant list most
channels publish there goes into the prompt as the roster to match voices
against. This costs nothing extra — one call, one quota unit, both facts. A
local file or a `gs://` object has no description, so those answers fall back to
generic labels.

Names are still the weakest part of any answer. They are the one thing that
cannot be checked against the media itself, so verify them against the video
description before attributing a quote to a person in anything you publish.

## Comments

The `comments` prompt is hardened against hallucination: quotes must be
verbatim from the fetched comments, and small samples are summarized without
invented percentage splits.
