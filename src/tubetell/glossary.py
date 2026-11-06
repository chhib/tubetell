"""Glossary post-correction for the Azure transcript.

The transcription model mishears names and products ("Kronan" for Kiana,
"Apsis Central" for Business Central), and the diarize model rejects the
`prompt` field a glossary would normally go in (HTTP 400). So the glossary is
applied afterwards: one text-only chat request gets the numbered transcript
and the terms, and answers with the replacements it would make, as JSON.
tubetell applies them itself, and only those whose target is a listed term
and whose source is really in that line, so the model can neither rewrite the
transcript nor introduce a word that is not in the list. Every change is
printed, so a wrong one is easy to spot and the file easy to fix by hand.

Terms come from `--glossary <file>` or, when it exists,
`~/.config/tubetell/glossary.txt` (`$XDG_CONFIG_HOME` replaces `~/.config`):
one term per line, `#` starts a comment.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from . import TubetellError
from .config import user_config_path
from .transcript import Segment

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def default_path() -> Path:
    return user_config_path().parent / "glossary.txt"


def load_terms(path: Path) -> list[str]:
    if not path.is_file():
        raise TubetellError(f"No such glossary: {path}")
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        term = line.split("#", 1)[0].strip()
        if term:
            terms.append(term)
    if not terms:
        raise TubetellError(f"{path} holds no terms (one per line, # starts a comment).")
    return terms


def glossary_tag(terms: list[str]) -> str:
    """What separates a corrected transcript's cache file from the plain one."""
    if not terms:
        return ""
    digest = hashlib.sha256("\n".join(sorted(terms)).encode()).hexdigest()[:8]
    return f"glossary-{digest}"


def _stamp(seconds: float) -> str:
    from .foundry import stamp

    return stamp(seconds)


def correction_request(segments: list[Segment], terms: list[str]) -> str:
    lines = "\n".join(
        f"{i}\t[{_stamp(s.start)}] {s.speaker}: {s.text}" for i, s in enumerate(segments, start=1)
    )
    listed = "\n".join(f"- {t}" for t in terms)
    return (
        "Below is a speech-to-text transcript, one numbered line per segment, and a "
        "glossary of names and terms that occur in this recording. The transcription "
        "model did not know the glossary, so some of these terms were written as "
        "similar-sounding words. Find every place where a word or phrase in the "
        "transcript is a mishearing of a glossary term, and answer with the "
        "replacements only, as JSON: {\"changes\": [{\"line\": <line number>, "
        "\"from\": \"<exact text in that line>\", \"to\": \"<the glossary term>\"}, ...]}. "
        "Rules: `to` must be a glossary term exactly as listed; `from` must appear "
        "verbatim in that line; change nothing that is not a mishearing of a listed "
        "term, and do not rephrase, translate or correct anything else. When nothing "
        "needs changing, answer {\"changes\": []}.\n\n"
        f"--- GLOSSARY ---\n{listed}\n--- END GLOSSARY ---\n\n"
        f"--- TRANSCRIPT ---\n{lines}\n--- END TRANSCRIPT ---"
    )


def apply_changes(
    segments: list[Segment], changes: list[dict], terms: list[str]
) -> tuple[list[Segment], list[str]]:
    """The segments with the model's accepted replacements, and one log line
    per change. A change is accepted only when its target is a listed term
    and its source is in that line."""
    out = list(segments)
    log = []
    allowed = set(terms)
    for change in changes:
        try:
            index = int(change["line"]) - 1
            source, target = str(change["from"]), str(change["to"])
        except (KeyError, TypeError, ValueError):
            continue
        if target not in allowed or not 0 <= index < len(out) or not source or source not in out[index].text:
            continue
        seg = out[index]
        out[index] = Segment(seg.start, seg.end, seg.speaker, seg.text.replace(source, target))
        log.append(f'[{_stamp(seg.start)}] "{source}" -> "{target}"')
    return out, log


def _parse(answer: str) -> list[dict] | None:
    try:
        data = json.loads(FENCE.sub("", answer.strip()))
    except json.JSONDecodeError:
        return None
    changes = data.get("changes") if isinstance(data, dict) else None
    return changes if isinstance(changes, list) else None


def correct(client, cfg, segments: list[Segment], terms: list[str]) -> list[Segment]:
    """The transcript with glossary terms restored, after one chat request."""
    from .foundry import ask

    if not segments:
        return segments
    print(f"glossary: checking {len(segments)} lines against {len(terms)} terms...", file=sys.stderr)
    answer = ask(client, cfg, [{"type": "text", "text": correction_request(segments, terms)}])
    changes = _parse(answer)
    if changes is None:
        print("glossary: the model's answer was not JSON; transcript left as it is", file=sys.stderr)
        return segments
    out, log = apply_changes(segments, changes, terms)
    if not log:
        print("glossary: no changes", file=sys.stderr)
        return segments
    print(f"glossary: {len(log)} change{'s' if len(log) != 1 else ''}", file=sys.stderr)
    for line in log:
        print(f"  {line}", file=sys.stderr)
    return out
