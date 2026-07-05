# Worked example: every mode on one video

All six runs below hit the same video —
[The Greek BBQ You Need to Make This Summer](https://youtu.be/LuA3FG-VCSs)
by Keiran Mustafa (16:57, published June 2026) — with the default
`gemini-2.5-flash` model. Each run's token line is what tubetell printed to
stderr; outputs are excerpted, not complete.

> **Rate-limit note:** Vertex re-fetches the video on every call, so the five
> video-mode runs were spaced ~1 minute apart. Back-to-back runs on the same
> URL trigger 500s (tubetell backs off and retries automatically). The
> `comments` run reads the YouTube Data API instead and never touches the
> video.

## summary (default)

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

## transcript

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

## claims

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

## sentiment

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

## comments

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

## --prompt (ask anything)

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

The whole six-run suite cost about $0.57 — see the
[Cost section in the README](../README.md#cost) for the per-run breakdown.
