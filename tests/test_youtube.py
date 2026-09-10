import pytest
import requests

from tubetell import TubetellError
from tubetell import youtube
from tubetell.youtube import fetch_duration, parse_iso8601_duration


@pytest.mark.parametrize(
    "text, expected",
    [
        ("PT1H35M32S", 5732),
        ("PT9M13S", 553),
        ("PT44S", 44),
        ("PT2H", 7200),
        ("P1DT2H", 93_600),
        ("PT1M30.5S", 90.5),
    ],
)
def test_parse_iso8601_duration_reads_youtube_durations(text, expected):
    assert parse_iso8601_duration(text) == expected


@pytest.mark.parametrize("text", ["", "P0D", "90", "1:30", "PTS", None])
def test_parse_iso8601_duration_returns_none_for_unknown_runtimes(text):
    assert parse_iso8601_duration(text) is None


def test_fetch_duration_returns_none_without_an_api_key(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.setattr(
        youtube, "_get", lambda *a, **k: pytest.fail("must not call the API")
    )
    assert fetch_duration("https://youtu.be/vOVKnYoH1p4") is None


def test_fetch_duration_swallows_a_failed_call(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    monkeypatch.setattr(youtube, "_get", lambda *a, **k: (_ for _ in ()).throw(
        TubetellError("YouTube API videos -> 403")
    ))
    assert fetch_duration("https://youtu.be/vOVKnYoH1p4") is None

    monkeypatch.setattr(youtube, "_get", lambda *a, **k: (_ for _ in ()).throw(
        requests.ConnectionError("no network")
    ))
    assert fetch_duration("https://youtu.be/vOVKnYoH1p4") is None


def test_fetch_duration_reads_content_details(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    calls = []

    def fake_get(endpoint, params, key):
        calls.append((endpoint, params["id"], params["part"]))
        return {
            "items": [
                {
                    "contentDetails": {"duration": "PT1H35M32S"},
                    "snippet": {"description": "MEDVERKANDE\nJacob Bursell"},
                }
            ]
        }

    monkeypatch.setattr(youtube, "_get", fake_get)
    assert fetch_duration("https://www.youtube.com/watch?v=5R64LDNJbK0") == 5732
    assert calls == [("videos", "5R64LDNJbK0", "contentDetails,snippet")]


def test_fetch_duration_handles_an_empty_item_list(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    monkeypatch.setattr(youtube, "_get", lambda *a, **k: {"items": []})
    assert fetch_duration("https://youtu.be/vOVKnYoH1p4") is None
