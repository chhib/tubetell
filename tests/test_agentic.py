from types import SimpleNamespace

import pytest

from tubetell import TubetellError
from tubetell import agentic
from tubetell.agentic import (
    MAX_OUTPUT_TOKENS,
    answer,
    format_usage_interaction,
    make_developer_client,
    run,
    uploaded_file,
    video_input,
)

WATCH = "https://www.youtube.com/watch?v=vOVKnYoH1p4"


def step(kind, text=None, error=None):
    content = [SimpleNamespace(type="text", text=text)] if text is not None else None
    return SimpleNamespace(type=kind, content=content, error=error)


def usage(**over):
    base = dict(
        total_input_tokens=49,
        total_thought_tokens=20_045,
        total_tool_use_tokens=95,
        total_output_tokens=164,
        total_tokens=20_353,
    )
    base.update(over)
    return SimpleNamespace(**base)


def interaction(steps, *, status="completed", output_text=None, usage=None, errors=None):
    return SimpleNamespace(
        status=status, steps=steps, output_text=output_text, usage=usage, errors=errors
    )


def fake_client(response=None, *, create=None, files=None):
    calls = []

    def _create(**kw):
        calls.append(kw)
        if create is not None:
            return create(**kw)
        return response

    client = SimpleNamespace(interactions=SimpleNamespace(create=_create), files=files)
    client.calls = calls
    return client


# --- run: answer extraction ------------------------------------------------


def test_run_returns_model_output_text_and_skips_thoughts(capsys):
    resp = interaction(
        [
            step("processing_call"),
            step("processing_result"),
            step("thought", "I should look at 12:00"),
            step("model_output", "The answer."),
        ]
    )
    out = run(fake_client(resp), model="m", video=video_input(WATCH), text="Q")
    assert out == "The answer."
    assert "12:00" not in out
    assert capsys.readouterr().out == ""


def test_run_concatenates_multiple_model_output_steps_in_order():
    resp = interaction([step("model_output", "one "), step("thought", "x"), step("model_output", "two")])
    assert run(fake_client(resp), model="m", video=video_input(WATCH), text="Q") == "one two"


def test_run_prefers_output_text_when_present():
    resp = interaction([step("model_output", "from steps")], output_text="from output_text")
    assert run(fake_client(resp), model="m", video=video_input(WATCH), text="Q") == "from output_text"


def test_run_completed_without_text_raises():
    resp = interaction([step("processing_call"), step("thought", "hmm")])
    with pytest.raises(TubetellError, match="no answer"):
        run(fake_client(resp), model="m", video=video_input(WATCH), text="Q")


# --- run: usage line --------------------------------------------------------


def test_run_prints_usage_line_with_tool_tokens(capsys):
    resp = interaction([step("model_output", "ok")], usage=usage())
    run(fake_client(resp), model="m", video=video_input(WATCH), text="Q")
    err = capsys.readouterr().err
    assert "49 in + 20,045 thinking + 95 tool + 164 out = 20,353 total" in err


def test_format_usage_interaction_renders_none_as_zero():
    line = format_usage_interaction(usage(total_tool_use_tokens=None))
    assert "+ 0 tool +" in line
    assert "49 in + 20,045 thinking + 0 tool + 164 out = 20,353 total" in line


def test_format_usage_interaction_none_usage():
    assert format_usage_interaction(None) is None


# --- run: truncation and status ---------------------------------------------


@pytest.mark.parametrize("status", ["incomplete", "budget_exceeded"])
def test_run_warns_when_truncated(capsys, status):
    resp = interaction([step("model_output", "partial")], status=status)
    assert run(fake_client(resp), model="m", video=video_input(WATCH), text="Q") == "partial"
    err = capsys.readouterr().err
    assert "cut off" in err and "--processing static" in err and "--clip START-END" not in err.split("--processing static")[0]


def test_run_does_not_warn_when_completed(capsys):
    resp = interaction([step("model_output", "whole")], status="completed")
    run(fake_client(resp), model="m", video=video_input(WATCH), text="Q")
    assert "cut off" not in capsys.readouterr().err


def test_run_failed_status_raises_with_top_level_error(capsys):
    resp = interaction(
        [step("model_output")],
        status="failed",
        errors=[SimpleNamespace(code="x", message="Video too long")],
    )
    with pytest.raises(TubetellError, match="Video too long"):
        run(fake_client(resp), model="m", video=video_input(WATCH), text="Q")
    assert capsys.readouterr().out == ""


def test_run_failed_status_falls_back_to_the_step_error():
    resp = interaction(
        [step("model_output", error=SimpleNamespace(code="x", message="Unsupported codec"))],
        status="failed",
    )
    with pytest.raises(TubetellError, match="Unsupported codec"):
        run(fake_client(resp), model="m", video=video_input(WATCH), text="Q")


def test_run_failed_status_falls_back_to_status_when_no_message():
    resp = interaction([], status="cancelled")
    with pytest.raises(TubetellError, match="cancelled"):
        run(fake_client(resp), model="m", video=video_input(WATCH), text="Q")


# --- run: request shape and error wrapping ----------------------------------


def test_run_sends_agentic_video_input_and_output_cap():
    client = fake_client(interaction([step("model_output", "ok")]))
    run(client, model="gemini-3.7-flash", video=video_input("vOVKnYoH1p4"), text="Exact text.")
    (kw,) = client.calls
    assert kw["model"] == "gemini-3.7-flash"
    video, text = kw["input"]
    assert video["type"] == "video"
    assert video["processing"] == "agentic"
    assert video["uri"] == WATCH
    assert text == {"type": "text", "text": "Exact text."}
    assert kw["generation_config"]["max_output_tokens"] == MAX_OUTPUT_TOKENS == 65535


def test_run_wraps_sdk_exception_with_its_message():
    class SdkError(Exception):
        def __init__(self):
            super().__init__("boom")
            self.message = "Unsupported model interaction: x"

    def boom(**kw):
        raise SdkError()

    with pytest.raises(TubetellError, match="Unsupported model interaction: x"):
        run(fake_client(create=boom), model="m", video=video_input(WATCH), text="Q")


def test_run_wraps_plain_exception_with_str():
    def boom(**kw):
        raise ValueError("bad thing")

    with pytest.raises(TubetellError, match="bad thing"):
        run(fake_client(create=boom), model="m", video=video_input(WATCH), text="Q")


# --- make_developer_client --------------------------------------------------


def test_make_developer_client_without_key_names_it(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(TubetellError, match="GEMINI_API_KEY"):
        make_developer_client()


def test_make_developer_client_uses_explicit_key_and_no_vertex(monkeypatch):
    seen = {}

    def fake_client_ctor(**kw):
        seen.update(kw)
        return "client"

    monkeypatch.setenv("GEMINI_API_KEY", "the-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "other-key")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setattr(agentic.genai, "Client", fake_client_ctor)

    assert make_developer_client() == "client"
    assert seen["vertexai"] is False and seen["api_key"] == "the-key"
    # files.upload/get/delete only retry transient failures when retry options are set
    assert seen["http_options"].retry_options is not None


# --- Files API lifecycle ----------------------------------------------------


class FakeFiles:
    """files.upload / get / delete with a scripted sequence of states."""

    def __init__(self, states=("PROCESSING", "ACTIVE"), *, uri="https://files/abc", mime="video/mp4"):
        self.states = list(states)
        self.uri = uri
        self.mime = mime
        self.uploads = []
        self.gets = []
        self.deletes = []
        self.delete_error = None

    def _file(self, state):
        return SimpleNamespace(
            name="files/abc", uri=self.uri, mime_type=self.mime, state=SimpleNamespace(name=state), error=None
        )

    def upload(self, *, file, config=None):
        self.uploads.append((file, config))
        return self._file(self.states.pop(0))

    def get(self, *, name):
        self.gets.append(name)
        return self._file(self.states.pop(0) if self.states else "ACTIVE")

    def delete(self, *, name):
        self.deletes.append(name)
        if self.delete_error:
            raise self.delete_error


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(agentic.time, "sleep", lambda s: slept.append(s))
    return slept


@pytest.fixture
def clip(tmp_path):
    p = tmp_path / "clip.mov"
    p.write_bytes(b"\0" * 2048)
    return p


def test_local_file_is_uploaded_polled_used_and_deleted(clip, no_sleep, monkeypatch, capsys):
    files = FakeFiles(("PROCESSING", "ACTIVE"), mime="video/quicktime")
    client = fake_client(interaction([step("model_output", "described")]), files=files)
    monkeypatch.setattr(agentic, "make_developer_client", lambda: client)

    assert answer(str(clip), model="m", text="Describe.") == "described"

    (upload,) = files.uploads
    assert upload[0].name == str(clip) and upload[0].closed
    assert files.gets == ["files/abc"]
    assert files.deletes == ["files/abc"]
    (kw,) = client.calls
    video = kw["input"][0]
    assert video == {
        "type": "video",
        "uri": "https://files/abc",
        "mime_type": "video/quicktime",
        "processing": "agentic",
    }
    err = capsys.readouterr().err
    assert "uploading clip.mov" in err
    assert "processing..." in err


def test_interaction_failure_still_deletes_file(clip, no_sleep, monkeypatch):
    files = FakeFiles(("ACTIVE",))

    def boom(**kw):
        raise RuntimeError("interaction exploded")

    client = fake_client(create=boom, files=files)
    monkeypatch.setattr(agentic, "make_developer_client", lambda: client)

    with pytest.raises(TubetellError, match="interaction exploded"):
        answer(str(clip), model="m", text="Q")
    assert files.deletes == ["files/abc"]


def test_failed_processing_names_file_and_skips_interaction(clip, no_sleep, monkeypatch):
    files = FakeFiles(("PROCESSING", "FAILED"))
    client = fake_client(interaction([step("model_output", "never")]), files=files)
    monkeypatch.setattr(agentic, "make_developer_client", lambda: client)

    with pytest.raises(TubetellError, match="clip.mov"):
        answer(str(clip), model="m", text="Q")
    assert client.calls == []
    assert files.deletes == ["files/abc"]


def test_delete_failure_only_warns(clip, no_sleep, monkeypatch, capsys):
    files = FakeFiles(("ACTIVE",))
    files.delete_error = RuntimeError("delete denied")
    client = fake_client(interaction([step("model_output", "fine")]), files=files)
    monkeypatch.setattr(agentic, "make_developer_client", lambda: client)

    assert answer(str(clip), model="m", text="Q") == "fine"
    err = capsys.readouterr().err
    assert "warning" in err and "delete denied" in err


def test_poll_sleeps_between_processing_states_and_stops_on_active(clip, no_sleep):
    files = FakeFiles(("PROCESSING", "PROCESSING", "PROCESSING", "ACTIVE"))
    client = SimpleNamespace(files=files)

    with uploaded_file(client, str(clip)) as (uri, mime):
        assert (uri, mime) == ("https://files/abc", "video/mp4")

    assert len(no_sleep) == 3
    assert files.gets == ["files/abc"] * 3
    assert files.deletes == ["files/abc"]


def test_poll_gives_up_after_bound_and_deletes(clip, no_sleep, monkeypatch):
    files = FakeFiles(["PROCESSING"] * 1000)
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(agentic.time, "monotonic", lambda: float(next(clock)))
    client = SimpleNamespace(files=files)

    with pytest.raises(TubetellError, match="clip.mov"):
        with uploaded_file(client, str(clip)):
            pass
    assert files.deletes == ["files/abc"]
    assert len(files.gets) < 1000


def test_upload_uses_local_mime_when_file_reports_none(clip, no_sleep):
    files = FakeFiles(("ACTIVE",), mime=None)
    with uploaded_file(SimpleNamespace(files=files), str(clip)) as (uri, mime):
        assert mime == "video/quicktime"
    assert files.uploads[0][1] == {"mime_type": "video/quicktime", "display_name": "clip.mov"}


def test_upload_sends_bytes_not_a_path_so_non_ascii_names_survive(tmp_path, no_sleep):
    # The SDK copies a path's basename into an HTTP header, which httpx rejects
    # for non-ASCII; an open file has no such header and gets an ASCII display name.
    p = tmp_path / "Mötesinspelning.mp4"
    p.write_bytes(b"\0" * 64)
    files = FakeFiles(("ACTIVE",))
    with uploaded_file(SimpleNamespace(files=files), str(p)):
        pass
    handle, config = files.uploads[0]
    assert hasattr(handle, "read") and handle.closed
    assert config["display_name"].isascii() and config["display_name"].endswith(".mp4")


def test_missing_path_fails_before_upload(tmp_path, monkeypatch):
    files = FakeFiles()
    client = fake_client(files=files)
    monkeypatch.setattr(agentic, "make_developer_client", lambda: client)

    with pytest.raises(TubetellError, match="No such file"):
        answer(str(tmp_path / "nope.mp4"), model="m", text="Q")
    assert files.uploads == []


def test_directory_fails_before_upload(tmp_path, monkeypatch):
    files = FakeFiles()
    monkeypatch.setattr(agentic, "make_developer_client", lambda: fake_client(files=files))

    with pytest.raises(TubetellError, match="is a directory"):
        answer(str(tmp_path), model="m", text="Q")
    assert files.uploads == []


def test_unsupported_extension_fails_before_upload(tmp_path, monkeypatch):
    notes = tmp_path / "notes.txt"
    notes.write_text("hi")
    files = FakeFiles()
    monkeypatch.setattr(agentic, "make_developer_client", lambda: fake_client(files=files))

    with pytest.raises(TubetellError, match="Unsupported file type"):
        answer(str(notes), model="m", text="Q")
    assert files.uploads == []


def test_gs_source_is_rejected(monkeypatch):
    monkeypatch.setattr(agentic, "make_developer_client", lambda: fake_client())
    with pytest.raises(TubetellError, match="Cloud Storage"):
        answer("gs://bucket/clip.mp4", model="m", text="Q")


def test_youtube_answer_goes_straight_to_run(monkeypatch):
    client = fake_client(interaction([step("model_output", "yt")]))
    monkeypatch.setattr(agentic, "make_developer_client", lambda: client)
    assert answer("https://youtu.be/vOVKnYoH1p4", model="m", text="Q") == "yt"
    assert client.calls[0]["input"][0]["uri"] == "https://youtu.be/vOVKnYoH1p4"


def test_audio_files_are_sent_as_an_audio_block_without_processing(tmp_path, no_sleep):
    p = tmp_path / "talk.mp3"
    p.write_bytes(b"\0" * 64)
    files = FakeFiles(("ACTIVE",), mime="audio/mpeg")
    client = fake_client(interaction([step("model_output", "heard")]), files=files)
    with uploaded_file(client, str(p)) as uploaded:
        assert run(client, model="m", video=video_input(str(p), uploaded), text="Q") == "heard"
    (kw,) = client.calls
    media = kw["input"][0]
    assert media["type"] == "audio" and media["mime_type"] == "audio/mpeg"
    assert "processing" not in media
