import pytest

from tubetell import TubetellError
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
    return {"snippet": {"topLevelComment": {"snippet": {"textDisplay": text, "likeCount": likes}}}}


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
