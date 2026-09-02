# Worked example: every mode on one video

Every run below hits the same video —
[The Greek BBQ You Need to Make This Summer](https://youtu.be/LuA3FG-VCSs)
by Keiran Mustafa (16:57, published June 2026). Each run's token line is what
tubetell printed to stderr; outputs are excerpted, not complete.

Two suites: the [static runs](#static-runs-vertex-gemini-25-flash) were made
with tubetell 0.3 on Vertex with `gemini-2.5-flash`, where the whole video is
tokenized up front. The [agentic runs](#agentic-runs-gemini-37-flash) repeat
the same six commands with tubetell 0.4 and `GEMINI_API_KEY` set, so `auto`
picks the Interactions API with the default `gemini-3.7-flash` — same video,
same prompts, ~1-2 % of the tokens.

## Static runs (Vertex, `gemini-2.5-flash`)

> **Rate-limit note:** Vertex re-fetches the video on every call, so the five
> video-mode runs were spaced ~1 minute apart. Back-to-back runs on the same
> URL trigger 500s (tubetell backs off and retries automatically). The
> `comments` run reads the YouTube Data API instead and never touches the
> video.

### summary (default)

```console
$ tubetell https://youtu.be/LuA3FG-VCSs
tokens: 287,866 in + 1,534 thinking + 1,019 out = 290,419 total
```

> **Summary:** This video provides a detailed guide to preparing a traditional
> Greek feast, focusing on pork and chicken souvlaki, a creamy fava dip, and a
> fresh Greek salad. The chef meticulously demonstrates each step, from
> marinating the meats with specific ingredients like Polish mustard and
> Sifnos oregano to slowly grilling them over white-hot coals. [...]
>
> **Notable Claims:** "Fava is a yellow split pea, it's not a lentil." [...]
> Chicken thigh is superior to chicken breast for souvlaki due to taste and
> moisture retention. [...]
>
> **Overall Tone:** instructive and enthusiastic, conveying a passion for
> Greek cuisine.

### transcript

```console
$ tubetell https://youtu.be/LuA3FG-VCSs --mode transcript --out transcript.md
tokens: 287,867 in + 47 thinking + 7,439 out = 295,353 total
Wrote transcript output -> transcript.md
```

269 timestamped lines covering the full runtime, `[00:00]` to `[16:34]`:

```
[00:10] So, we're going to do a Greek Feast.
[00:13] We're going to do pork souvlaki, chicken souvlaki, Greek salad, horiatiki salata.
[00:17] We're going to make fava, whipped fava dip.
[00:24] So, fava is a yellow split pea. It's not a lentil.
...
[16:03] And you see how I was saying about the salt and the oil mixed together. That's your dressing.
[16:34] Delicious.
```

### claims

```console
$ tubetell https://youtu.be/LuA3FG-VCSs --mode claims
tokens: 287,859 in + 62 thinking + 684 out = 288,605 total
```

23 timestamped claims in chronological order:

```
- [00:23] So fava is a yellow split pea. It's not a lentil. (Speaker: Male chef)
- [00:26] The beauty of them is that it doesn't require soaking. (Speaker: Male chef)
- [02:15] It's not quite as punchy as Dijon. (Speaker: Male chef)
- [03:22] If you get pork belly and the skin is wet, that means it's fresh. (Speaker: Male chef)
- [05:22] I would choose chicken thigh for this over chicken breast. (Speaker: Male chef)
...
```

### sentiment

```console
$ tubetell https://youtu.be/LuA3FG-VCSs --mode sentiment
tokens: 287,858 in + 499 out = 288,357 total
```

> This video shows a positive and engaging sentiment. [...]
>
> **00:00 - 00:09**: The opening montages of the prepared food and the man
> enjoying a bite immediately set a positive and appetizing tone. The music is
> upbeat and cheerful. [...]
>
> There are no noticeable shifts in sentiment that would be considered
> negative or tense. The video maintains an upbeat, passionate, and
> delicious-focused tone throughout.

### comments

```console
$ tubetell https://youtu.be/LuA3FG-VCSs --mode comments --max-comments 50
tokens: 2,013 in + 4,051 thinking + 956 out = 7,020 total
```

> This analysis is based on the provided 50 comments.
>
> **Audience Sentiment Split:** Positive ~80% · Neutral/Suggestive ~20% ·
> Negative ~0%
>
> **Most Common Criticism:** There is no direct negative criticism of the
> video or the host. Any "criticism" is exclusively in the form of polite,
> constructive suggestions [...] — *"One point; no lemon in the Greek salad.
> Greeks put lemon everywhere, but not in the Greek salad."* (Comment 50)

Note the token counts: no video is ingested, so the whole run is ~7k tokens —
~40x fewer than a video-mode run, and ~8x cheaper.

### --prompt (ask anything)

```console
$ tubetell https://youtu.be/LuA3FG-VCSs --prompt "Write a complete shopping list to reproduce everything cooked in this video, grouped by supermarket section, with approximate quantities."
tokens: 287,833 in + 1,791 thinking + 640 out = 290,264 total
```

> **Greek Feast Shopping List**
>
> **1. Produce Section:**
> * **Onions:** 2 medium-sized (1 for fava dip, 1 sliced for Greek salad, remaining for fava topping)
> * **Garlic:** 1 head (for fava dip & souvlaki marinade)
> * **Lemons:** 2 (for souvlaki marinade, Greek salad dressing, and serving)
> [...]
>
> **2. Meat & Poultry Section:**
> * **Pork Belly (skin off):** 300-400g (for pork souvlaki)
> * **Pork Shoulder/Collar (boneless):** 300-400g (for pork souvlaki)
> * **Boneless, Skinless Chicken Thighs:** 300-400g (approx. 2-3 thighs)
> [...]

The whole six-run suite cost about $0.57 at July 2026 `gemini-2.5-flash` rates
— see the [Cost section in the README](../README.md#cost).

## Agentic runs (`gemini-3.7-flash`)

Same six commands, tubetell 0.4, `GEMINI_API_KEY` set, nothing else changed:
`--processing auto` resolves to agentic for the five video modes, and the
usage line grows a `tool` bucket for the model's timeline navigation. The six
runs went back to back with no rate limiting — the Developer API path does not
re-fetch the video the way Vertex does. `comments` still runs on Vertex (it
reads the YouTube Data API, not the video), now on 3.7-flash.

### summary (default)

```console
$ tubetell https://youtu.be/LuA3FG-VCSs
tokens: 298 in + 5,768 thinking + 344 tool + 719 out = 7,129 total
```

18 s wall time, against 290,419 tokens static.

> **Summary:** This video provides a step-by-step culinary guide to preparing an
> authentic Greek feast consisting of pork and chicken souvlaki, a creamy yellow
> split-pea fava dip, and a classic Greek salad. [...]
>
> **Key Points**
> * **Fava Dip Preparation [00:20] – [01:29], [06:47] – [07:35]:** Yellow split
>   peas are simmered, skimmed, and seasoned before being blended smooth [...]
> * **Souvlaki Marinade [01:30] – [02:59]:** A single marinade is prepared for
>   both pork and chicken using crushed garlic, mild Polish mustard, black
>   pepper, dried oregano, red wine vinegar, olive oil, salt, and fresh lemon
>   juice.
> [...]
>
> **Notable Claims, People, and Entities**
> * **HG Walter:** Mentioned as the London butcher where high-quality aged pork
>   was sourced [03:11].
> * **Island of Sifnos:** The origin of the dried Greek oregano [02:22].
> [...]
>
> **Overall Tone:** Casual, instructional, and enthusiastic.

### transcript

```console
$ tubetell https://youtu.be/LuA3FG-VCSs --mode transcript
tokens: 247 in + 5,126 thinking + 293 tool + 3,458 out = 9,124 total
```

21 s. 100 timestamped lines covering the full runtime, `[00:11]` to `[16:43]`
— the agentic model segments in ~10-second chunks where the static run gave
269 shorter lines, so the wording is the same but the granularity is coarser:

```
[00:11] We're going to do a Greek feast. We're going to do pork souvlaki, chicken souvlaki, Greek salads, horiatiki salads. We're going to make fava, whipped fava dip.
[00:20] First off, we'll start off with making the fava. So, fava is a yellow split pea. It's not a lentil. The beauty of them is that it doesn't require soaking, but
[00:30] you do want to wash and check for any wood stems. Just want to put them on the stove. If you're buying good quality fava, it will cook nicely on its own. However,
...
[16:00] The Greek salad you want to break the feta up. And you see how what I was saying about the salt and the oil mix together, that's your dressing.
[16:43] It's really neutral. Delicious.
```

### claims

```console
$ tubetell https://youtu.be/LuA3FG-VCSs --mode claims
tokens: 363 in + 792 thinking + 2,756 tool + 629 out = 4,540 total
```

12 s. 17 timestamped claims in chronological order (the static run found 23,
phrased closer to the spoken words; this pass paraphrases):

```
* [00:20] – Greek fava is made from yellow split peas, which are not lentils. (Presenter)
* [00:25] – Yellow split peas do not require soaking prior to cooking. (Presenter)
* [02:12] – Polish mustard is less punchy/sharp in flavor than Dijon mustard. (Presenter)
* [03:16] – Dry skin on pork belly indicates that the meat has been hung and aged, whereas wet skin indicates it is fresh/un-aged. (Presenter)
* [05:14] – Acidic ingredients such as vinegar and lemon juice tenderize meat by breaking down muscle fibers. (Presenter)
* [08:00] – Charcoal embers must be completely covered in white ash before grilling fatty pork to prevent flare-ups. (Presenter)
...
```

### sentiment

```console
$ tubetell https://youtu.be/LuA3FG-VCSs --mode sentiment
tokens: 314 in + 677 thinking + 360 tool + 747 out = 2,098 total
```

10 s.

> The overall sentiment is **warm, casual, enthusiastic, and instructional**.
> [...]
>
> * **`[00:11]` – `[07:55]` | Methodical, Cheerful & Educational (Prep &
>   Kitchen Phase)** [...]
> * **`[07:56]` – `[12:30]` | Dynamic, Sensory & Resourceful (Grilling & Salad
>   Assembly)** — When he realizes he forgot black olives for the salad around
>   `[09:00]`, he remains completely unfazed and lighthearted [...]
> * **`[12:31]` – `[15:35]` | Anticipatory & Satisfying (Finishing & Plating)**
> * **`[15:36]` – `[16:57]` | Indulgent, Relaxed & Gratifying (Tasting &
>   Wrap-Up)**

### comments

```console
$ tubetell https://youtu.be/LuA3FG-VCSs --mode comments --max-comments 50
tokens: 2,209 in + 1,042 thinking + 664 out = 3,915 total
```

18 s. No `tool` bucket: this mode never touches the video and still runs on
Vertex.

> **Sentiment Split (Approximate):** Positive ~92% · Neutral / Informational
> ~8% · Negative ~0%
>
> **Criticisms & Constructive Notes:** There is no outright negative
> criticism; feedback is limited to constructive culinary advice and pointing
> out an omitted ingredient in the video description.
>
> - "I'll be cooking this tomorrow. Can't wait. Just a note, you've missed
>   mustard off your ingredients list for the meat marinade."

### --prompt (ask anything)

```console
$ tubetell https://youtu.be/LuA3FG-VCSs --prompt "Write a complete shopping list to reproduce everything cooked in this video, grouped by supermarket section, with approximate quantities."
tokens: 234 in + 1,564 thinking + 280 tool + 885 out = 2,963 total
```

14 s.

> **1. Meat & Poultry**
> * **Pork Belly** – ~400–500 g (skin removed before cubing) `[03:10]`
> * **Pork Collar / Shoulder** – ~400–500 g (cubed) `[03:38]`
> * **Boneless, Skinless Chicken Thighs** – ~500 g (approx. 4–6 fillets) `[05:18]`
>
> **2. Fresh Produce (Vegetables, Fruit & Herbs)**
> * **White / Yellow Onions** – 3–4 medium (for simmering into fava `[00:43]`,
>   slicing for the Greek salad `[09:51]`, and dicing for the fava topping
>   `[10:20]`)
> * **Garlic** – 1 whole bulb (approx. 4–5 cloves crushed for the marinade) `[01:35]`
> [...]

### The two suites side by side

| Run | Static, 2.5-flash | Agentic, 3.7-flash |
|---|---|---|
| `summary` | 290,419 | 7,129 |
| `transcript` | 295,353 | 9,124 |
| `claims` | 288,605 | 4,540 |
| `sentiment` | 288,357 | 2,098 |
| `--prompt` (shopping list) | 290,264 | 2,963 |
| `comments` (50, Vertex both times) | 7,020 | 3,915 |

Five video runs: ~1.45M tokens static, ~26k agentic. Note that most of the
agentic total is thinking and output, which are billed at the output rate, so
the cost drop is smaller than the token drop — see the
[Cost section in the README](../README.md#cost).

### A longer video

The 16:57 cooking video fits one static request. A 57:32 podcast
(`vOVKnYoH1p4`) does not — static needs the low-res frame planner for a
summary (~314,000 input tokens, 56 s) and the clip planner for a transcript.
Agentic, same model:

```console
$ tubetell vOVKnYoH1p4 --mode claims
tokens: 241 in + 1,602 thinking + 287 tool + 1,320 out = 3,450 total
```

33 claims spanning 01:29–54:17, 14 s.

```console
$ tubetell vOVKnYoH1p4 --mode transcript --out transcript.md
tokens: 289 in + 2,174 thinking + 335 tool + 16,141 out = 18,939 total
```

381 lines, `00:00` to `57:30`, complete, 57 s. Three and a half times the
runtime, and the claims run came in under the short video's; the transcript
costs more only because there is more to write out.
