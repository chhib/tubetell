import re

import pytest

from tubetell import TubetellError
from tubetell.config import (
    load_gemini_api_key,
    missing_credentials_message,
    resolve_processing,
    static_reason,
)
from tubetell.gemini import make_client
from tubetell.youtube import fetch_comments, load_api_key


def test_make_client_requires_project(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    with pytest.raises(TubetellError, match="GOOGLE_CLOUD_PROJECT"):
        make_client()


def test_load_api_key_requires_key(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    with pytest.raises(TubetellError, match="YOUTUBE_API_KEY"):
        load_api_key()


def _thread(text, likes):
    return {"snippet": {"topLevelComment": {"snippet": {"textOriginal": text, "likeCount": likes}}}}


def test_fetch_comments_falls_back_to_html_text(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    page = {"items": [{"snippet": {"topLevelComment": {"snippet": {"textDisplay": "hi", "likeCount": 1}}}}]}
    monkeypatch.setattr("tubetell.youtube._get", lambda e, p, k: page)
    block, n = fetch_comments("vOVKnYoH1p4", limit=5)
    assert block == "1. (1 likes) hi"


def test_fetch_comments_paginates_and_numbers(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    pages = [
        {"items": [_thread("first", 10), _thread("second", 2)], "nextPageToken": "p2"},
        {"items": [_thread("third\nwith newline", 0)]},
    ]
    calls = []

    def fake_get(endpoint, params, key):
        calls.append(params)
        return pages[len(calls) - 1]

    monkeypatch.setattr("tubetell.youtube._get", fake_get)
    block, n = fetch_comments("vOVKnYoH1p4", limit=10)
    assert n == 3
    assert block.splitlines() == [
        "1. (10 likes) first",
        "2. (2 likes) second",
        "3. (0 likes) third with newline",
    ]
    assert calls[1]["pageToken"] == "p2"


def test_fetch_comments_respects_limit(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    page = {"items": [_thread(f"c{i}", i) for i in range(5)], "nextPageToken": "more"}
    monkeypatch.setattr("tubetell.youtube._get", lambda e, p, k: page)
    block, n = fetch_comments("vOVKnYoH1p4", limit=3)
    assert n == 3
    assert len(block.splitlines()) == 3


def test_fetch_comments_empty_raises(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    monkeypatch.setattr("tubetell.youtube._get", lambda e, p, k: {"items": []})
    with pytest.raises(TubetellError, match="disabled"):
        fetch_comments("vOVKnYoH1p4", limit=10)

def test_load_gemini_api_key_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert load_gemini_api_key() is None
    monkeypatch.setenv("GEMINI_API_KEY", "")
    assert load_gemini_api_key() is None
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert load_gemini_api_key() == "k"

def test_missing_credentials_message_names_both_variables_and_files(tmp_path):
    msg = missing_credentials_message([tmp_path / ".env"])
    assert "GEMINI_API_KEY" in msg
    assert "GOOGLE_CLOUD_PROJECT" in msg
    assert str(tmp_path / ".env") in msg

AGENTIC_OK = dict(has_key=True, model="gemini-3.7-flash", source_kind="youtube", clip=None, fps=None)

def test_auto_picks_agentic_when_every_condition_holds():
    assert resolve_processing("auto", **AGENTIC_OK) == "agentic"

@pytest.mark.parametrize(
    "override",
    [
        {"model": "gemini-2.5-flash"},
        {"source_kind": "gs"},
        {"clip": (0, 60)},
        {"fps": 1.0},
        {"has_key": False},
    ],
)
def test_auto_falls_back_to_static_when_a_condition_fails(override):
    assert resolve_processing("auto", **{**AGENTIC_OK, **override}) == "static"

def test_auto_accepts_a_local_file():
    assert resolve_processing("auto", **{**AGENTIC_OK, "source_kind": "local"}) == "agentic"

@pytest.mark.parametrize(
    "override, expected",
    [
        ({"has_key": False}, "GEMINI_API_KEY"),
        ({"source_kind": "gs"}, "Cloud Storage"),
        ({"clip": (0, 60)}, "--clip"),
        ({"fps": 1.0}, "--fps"),
        ({"model": "gemini-2.5-flash"}, "gemini-2.5-flash"),
    ],
)
def test_explicit_agentic_names_the_unmet_condition(override, expected):
    with pytest.raises(TubetellError, match=expected):
        resolve_processing("agentic", **{**AGENTIC_OK, **override})

def test_explicit_agentic_error_lists_supported_models():
    with pytest.raises(TubetellError, match="gemini-3.6-flash"):
        resolve_processing("agentic", **{**AGENTIC_OK, "model": "gemini-2.5-flash"})

def test_explicit_agentic_runs_when_conditions_hold():
    assert resolve_processing("agentic", **AGENTIC_OK) == "agentic"

def test_explicit_static_always_wins():
    assert resolve_processing("static", **AGENTIC_OK) == "static"

@pytest.mark.parametrize(
    "override",
    [{"fps": 1.0}, {"model": "gemini-2.5-flash"}, {"source_kind": "gs"}],
)
def test_static_reason_explains_the_fallback_when_a_key_is_present(override):
    reason = static_reason(**{**AGENTIC_OK, **override})
    assert reason
    with pytest.raises(TubetellError, match=re.escape(reason)):
        resolve_processing("agentic", **{**AGENTIC_OK, **override})

def test_static_reason_is_none_when_agentic_runs_or_no_key_is_set():
    assert static_reason(**AGENTIC_OK) is None
    assert static_reason(**{**AGENTIC_OK, "has_key": False, "fps": 1.0}) is None
