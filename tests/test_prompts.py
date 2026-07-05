from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from tubetell import TubetellError
from tubetell.gemini import comments_body, generate, video_contents


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


def test_video_contents_wraps_url_as_filedata():
    content = video_contents("https://youtu.be/vOVKnYoH1p4", "Summarize this.")
    file_part, text_part = content.parts
    assert file_part.file_data.file_uri == "https://youtu.be/vOVKnYoH1p4"
    assert file_part.file_data.mime_type == "video/*"
    assert text_part.text == "Summarize this."


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


def test_generate_gives_up_after_four_attempts(monkeypatch):
    delays = []
    monkeypatch.setattr("tubetell.gemini.time.sleep", delays.append)
    client = SimpleNamespace(models=FlakyModels(failures=99))
    with pytest.raises(TubetellError, match="rate-limited"):
        generate(client, model="m", contents="c")
    assert client.models.calls == 4
    assert delays == [4.0, 8.0, 16.0]
