from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as genai_errors

from tubetell import TubetellError
from google.genai import types

from tubetell.gemini import comments_body, format_usage, generate, media_contents


def test_comments_body_preset_carries_count_and_hard_rules():
    body = comments_body("1. (5 likes) great video", n=3, prompt=None)
    assert "3 comment(s)" in body
    assert "--- COMMENTS (3 total) ---" in body
    assert "do not invent" in body
    assert body.endswith("great video")


def test_comments_body_custom_prompt_replaces_preset():
    body = comments_body("1. (0 likes) hej", n=1, prompt="Vad tycker folk?")
    assert body.startswith("Vad tycker folk?")
    assert "--- COMMENTS (1 total) ---" in body
    assert "do not invent" not in body


def test_media_contents_pairs_the_media_part_with_the_prompt():
    media = types.Part(file_data=types.FileData(file_uri="https://youtu.be/x", mime_type="video/*"))
    content = media_contents(media, "Summarize this.")
    file_part, text_part = content.parts
    assert file_part is media
    assert text_part.text == "Summarize this."
    assert content.role == "user"


def test_format_usage_includes_thinking_when_present():
    usage = SimpleNamespace(
        prompt_token_count=121_434,
        thoughts_token_count=1_230,
        candidates_token_count=456,
        total_token_count=123_120,
    )
    assert format_usage(usage) == "tokens: 121,434 in + 1,230 thinking + 456 out = 123,120 total"


def test_format_usage_omits_zero_thinking_and_handles_none_counts():
    usage = SimpleNamespace(
        prompt_token_count=100,
        thoughts_token_count=None,
        candidates_token_count=20,
        total_token_count=120,
    )
    assert format_usage(usage) == "tokens: 100 in + 20 out = 120 total"
    assert format_usage(None) is None


def test_generate_prints_usage_to_stderr(capsys):
    resp = SimpleNamespace(
        text="ok",
        usage_metadata=SimpleNamespace(
            prompt_token_count=10,
            thoughts_token_count=0,
            candidates_token_count=5,
            total_token_count=15,
        ),
    )
    models = SimpleNamespace(generate_content=lambda *, model, contents: resp)
    client = SimpleNamespace(models=models)
    assert generate(client, model="m", contents="c") == "ok"
    assert "tokens: 10 in + 5 out = 15 total" in capsys.readouterr().err


class FlakyModels:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def generate_content(self, *, model, contents):
        self.calls += 1
        if self.calls <= self.failures:
            raise genai_errors.ServerError(500, {"error": {"message": "internal"}})
        return SimpleNamespace(text="ok")


def test_generate_retries_transient_errors(monkeypatch):
    delays = []
    monkeypatch.setattr("tubetell.gemini.time.sleep", delays.append)
    client = SimpleNamespace(models=FlakyModels(failures=2))
    assert generate(client, model="m", contents="c") == "ok"
    assert delays == [4.0, 8.0]


def test_generate_retries_a_dropped_connection(monkeypatch):
    monkeypatch.setattr("tubetell.gemini.time.sleep", lambda _: None)

    class DroppingModels:
        calls = 0

        def generate_content(self, *, model, contents):
            DroppingModels.calls += 1
            if DroppingModels.calls == 1:
                raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
            return SimpleNamespace(text="ok")

    client = SimpleNamespace(models=DroppingModels())
    assert generate(client, model="m", contents="c") == "ok"
    assert DroppingModels.calls == 2


def test_generate_gives_up_after_four_attempts(monkeypatch):
    delays = []
    monkeypatch.setattr("tubetell.gemini.time.sleep", delays.append)
    client = SimpleNamespace(models=FlakyModels(failures=99))
    with pytest.raises(TubetellError, match="rate-limited"):
        generate(client, model="m", contents="c")
    assert client.models.calls == 4
    assert delays == [4.0, 8.0, 16.0]
