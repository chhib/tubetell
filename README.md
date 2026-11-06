# tubetell

Ask a model anything about a video — a YouTube URL, a `gs://` object, a file
on your disk, or a post on X (or any other site yt-dlp can download): what's
said, what's shown, what's claimed, how the tone shifts. A separate mode reads
the comment section instead and analyzes audience sentiment.

```bash
tubetell https://www.youtube.com/watch?v=vOVKnYoH1p4
tubetell <url> --mode transcript --out transcript.md
tubetell <url> --mode claims
tubetell <url> --mode comments --max-comments 100
tubetell <url> --prompt "What products are recommended, and by whom?"

tubetell ~/screen-recording.mov --prompt "What did I click, and when?"
tubetell gameplay.mov --fps 2 --clip 1:30-2:45      # static: exact frames, one span

tubetell https://x.com/<user>/status/<id> --mode transcript --out t.md   # downloaded first

tubetell meeting.mp4 --provider azure --mode claims   # on Azure AI Foundry instead
tubetell meeting.mp4 --provider azure --transcript corrected.md --mode claims   # from a fixed transcript
tubetell meeting.mp4 --provider azure --speaker "Ada=0:01-0:10" --speaker Bob     # name known voices
tubetell meeting.mp4 --provider azure --glossary terms.txt                      # restore misheard names and products
```

## Providers

| Provider | How it reads the video | Details |
|---|---|---|
| `gemini` (default) | Gemini reads the video itself, agentic (Interactions API) or static (Vertex AI) | [docs/providers/gemini.md](docs/providers/gemini.md) |
| `azure` | the media stays in your Azure tenant: audio → transcript with speakers, plus sampled frames, answered by a chat deployment | [docs/providers/azure.md](docs/providers/azure.md) |

## Modes

| Mode | What you get |
|------|--------------|
| `summary` (default) | 3-sentence summary, key points, notable entities, overall tone |
| `transcript` | full timestamped transcript, one line per segment, speakers attributed |
| `claims` | every checkable factual claim with timestamp and speaker |
| `sentiment` | how tone/sentiment shifts across the runtime, each shift timestamped |
| `comments` | audience sentiment from top comments (reads comments, not the video) |
| `screen` | what is on each sampled frame: shown, legible, wrong (`--provider azure` only) |

`--prompt "..."` overrides the mode preset entirely — ask the video anything.
`comments` is Gemini-only; `screen` is Azure-only.
`--model <model>` selects the model (Gemini default: `gemini-3.8-flash`; with
`--provider azure`, the chat deployment).

Every answer carries a timestamp rule and a speaker rule, so the model neither
writes impossible timestamps nor invents names: see
[docs/rules.md](docs/rules.md). Names are still the weakest part of any answer;
check them before you quote anyone.

## Install

```bash
pipx install git+https://github.com/chhib/tubetell
# or run without installing:
uvx --from git+https://github.com/chhib/tubetell tubetell --help
```

Requires Python 3.10+. The dependency pin `google-genai>=2.21.0` matters: that
is the first release that sends the `processing` field the agentic path needs.

## Configuration

tubetell reads configuration from, in order of precedence (see
[`.env.example`](.env.example)):

1. real environment variables
2. the nearest `.env` walking up from the directory you run it from
3. the file named by `$TUBETELL_ENV`
4. `~/.config/tubetell/.env` (or `$XDG_CONFIG_HOME/tubetell/.env`)

Put your keys in `~/.config/tubetell/.env` once and tubetell works from any
directory — the same files hold both providers' settings. A relative
`GOOGLE_APPLICATION_CREDENTIALS` is resolved against the
`.env` file that sets it, not the cwd.

What each provider needs: [Gemini setup](docs/providers/gemini.md#setup),
[Azure setup](docs/providers/azure.md#setup).

## More

- [docs/providers/gemini.md](docs/providers/gemini.md): processing modes, long videos, sources, setup, cost, troubleshooting
- [docs/providers/azure.md](docs/providers/azure.md): the Azure pipeline, deployments, Entra ID auth, limits
- [docs/rules.md](docs/rules.md): the timestamp and speaker rules
- [docs/example.md](docs/example.md): all five modes on one video, with real outputs and token counts
- [docs/agent-skill.md](docs/agent-skill.md): let Claude Code, agy or Codex reach for tubetell ([`skills/tubetell/SKILL.md`](skills/tubetell/SKILL.md))

## Development

```bash
git clone https://github.com/chhib/tubetell && cd tubetell
python3 -m venv .venv && .venv/bin/pip install -e . pytest
.venv/bin/pytest
```

## License

MIT

