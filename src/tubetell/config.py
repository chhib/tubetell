"""Configuration discovery.

tubetell used to read only the `.env` found by walking up from the shell cwd.
That made it silently cwd-dependent: run from a scratch directory it failed
with "GOOGLE_CLOUD_PROJECT is not set", and a relative
GOOGLE_APPLICATION_CREDENTIALS from a found `.env` pointed at the wrong place.

Now `.env` files are loaded in this order, first definition wins, and real
environment variables always beat all of them:

1. the nearest `.env` walking up from the cwd
2. the file named by $TUBETELL_ENV
3. $XDG_CONFIG_HOME/tubetell/.env  (default ~/.config/tubetell/.env)

A relative GOOGLE_APPLICATION_CREDENTIALS is resolved against the directory of
the `.env` that defined it, not against the cwd.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values, find_dotenv

from . import TubetellError

CREDS = "GOOGLE_APPLICATION_CREDENTIALS"


AGENTIC_MODELS = ("gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite")


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "tubetell" / ".env"


def candidate_paths() -> list[Path]:
    paths: list[Path] = []
    near = find_dotenv(usecwd=True)
    if near:
        paths.append(Path(near))
    explicit = os.environ.get("TUBETELL_ENV")
    if explicit:
        paths.append(Path(explicit).expanduser())
    paths.append(user_config_path())
    seen: set[Path] = set()
    out = []
    for p in paths:
        r = p.expanduser().resolve()
        if r not in seen:
            seen.add(r)
            out.append(p)
    return out


def load_env() -> list[Path]:
    """Load config files into os.environ (never overriding). Returns files read."""
    loaded: list[Path] = []
    for path in candidate_paths():
        if not path.is_file():
            continue
        values = dotenv_values(path)
        for key, val in values.items():
            if val is None or key in os.environ:
                continue
            if key == CREDS and not Path(val).expanduser().is_absolute():
                val = str((path.resolve().parent / Path(val).expanduser()).resolve())
            os.environ[key] = val
        loaded.append(path)
    return loaded


def check_credentials_file() -> None:
    """Fail early and clearly if the SA key path points at nothing."""
    creds = os.environ.get(CREDS)
    if creds and not Path(creds).expanduser().is_file():
        raise TubetellError(
            f"{CREDS} points at {creds!r}, which does not exist. Use an absolute path, "
            "or a path relative to the .env file that sets it."
        )


def load_gemini_api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or None


def _files_read(loaded: list[Path]) -> str:
    return ", ".join(str(p) for p in loaded) if loaded else "none found"


def missing_credentials_message(loaded: list[Path]) -> str:
    return (
        "Neither GEMINI_API_KEY nor GOOGLE_CLOUD_PROJECT is set. Export one of them, "
        "or put it in a .env file in the directory you run tubetell from, or in "
        f"{user_config_path()} (config files read: {_files_read(loaded)})."
    )


def _unmet_condition(
    *, has_key: bool, model: str, source_kind: str, clip: tuple[int, int] | None, fps: float | None
) -> str | None:
    if not has_key:
        return "GEMINI_API_KEY is not set"
    if model not in AGENTIC_MODELS:
        return f"{model} is not an agentic model (supported: {', '.join(AGENTIC_MODELS)})"
    if source_kind == "gs":
        return "agentic mode does not support Cloud Storage (gs://) sources"
    if clip is not None:
        return "--clip is static-only"
    if fps is not None:
        return "--fps is static-only"
    return None


def static_reason(
    *, has_key: bool, model: str, source_kind: str, clip: tuple[int, int] | None, fps: float | None
) -> str | None:
    """Why `auto` fell back to static despite a GEMINI_API_KEY; None otherwise."""
    if not has_key:
        return None
    return _unmet_condition(has_key=has_key, model=model, source_kind=source_kind, clip=clip, fps=fps)


def resolve_processing(
    explicit: str,
    *,
    has_key: bool,
    model: str,
    source_kind: str,
    clip: tuple[int, int] | None,
    fps: float | None,
) -> str:
    """Pick the request path: `explicit` is auto, agentic, or static."""
    if explicit == "static":
        return "static"
    reason = _unmet_condition(has_key=has_key, model=model, source_kind=source_kind, clip=clip, fps=fps)
    if reason is None:
        return "agentic"
    if explicit == "agentic":
        raise TubetellError(f"--processing agentic is not possible here: {reason}.")
    return "static"
