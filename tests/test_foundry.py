import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from tubetell import TubetellError
from tubetell import foundry
from tubetell.foundry import (
    CHAT_DEPLOYMENT,
    MAX_FRAMES,
    TRANSCRIBE_DEPLOYMENT,
    Frame,
    Segment,
    TranscriptionUsage,
    answer_text,
    base_url,
    chat_content,
    format_chat_usage,
    format_transcript,
    frame_select,
    frame_times,
    frames_command,
    merge_chunks,
    plan_chunks,
    segments_from,
    settings,
    speaker_label,
    stamp,
    thin,
)
from tubetell.gemini import SPEAKER_RULE

AZURE_KEYS = (
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT",
    "AZURE_OPENAI_CHAT_DEPLOYMENT",
    "AZURE_OPENAI_API_KEY",
)
ENDPOINT = "https://data-and-analytics-resource.cognitiveservices.azure.com"

# Shaped like the documented diarized_json response
# (https://learn.microsoft.com/en-us/azure/foundry/reference/rest/audio-create-transcription).
DIARIZED = json.loads(
    """
{
  "task": "transcribe",
  "duration": 27.4,
  "text": "A: Thanks for joining. B: Glad to be here. A: Let's start.",
  "segments": [
    {"type": "transcript.text.segment", "id": "seg_001", "start": 0.0, "end": 4.7,
     "text": "Thanks for joining.", "speaker": "A"},
    {"type": "transcript.text.segment", "id": "seg_002", "start": 4.7, "end": 11.8,
     "text": " Glad to be here. ", "speaker": "B"},
    {"type": "transcript.text.segment", "id": "seg_003", "start": 12.1, "end": 18.5,
     "text": "Let's start.", "speaker": "A"},
    {"type": "transcript.text.segment", "id": "seg_004", "start": 18.5, "end": 19.0,
     "text": "  ", "speaker": "B"}
  ],
  "usage": {"type": "duration", "seconds": 27}
}
"""
)


@pytest.fixture(autouse=True)
def clean_azure_env(monkeypatch):
    for k in AZURE_KEYS:
        monkeypatch.delenv(k, raising=False)


# --- configuration -------------------------------------------------------------


def test_settings_requires_an_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with pytest.raises(TubetellError, match="AZURE_OPENAI_ENDPOINT is not set"):
        settings()


def test_settings_defaults_the_two_deployments_and_has_no_key(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    cfg = settings()
    assert cfg.transcribe_deployment == TRANSCRIBE_DEPLOYMENT == "gpt-4o-transcribe-diarize"
    assert cfg.chat_deployment == CHAT_DEPLOYMENT == "gpt-6.1-terra"
    assert cfg.api_key is None


def test_settings_env_overrides_and_model_beats_env(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT", "my-stt")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "my-chat")
    assert settings().chat_deployment == "my-chat"
    assert settings().transcribe_deployment == "my-stt"
    assert settings("from-flag").chat_deployment == "from-flag"


@pytest.mark.parametrize(
    "endpoint",
    [ENDPOINT, ENDPOINT + "/", ENDPOINT + "/openai/v1", ENDPOINT + "/openai/v1/"],
)
def test_base_url_points_at_the_v1_api_once(endpoint):
    assert base_url(endpoint) == ENDPOINT + "/openai/v1/"


@pytest.mark.parametrize(
    "endpoint",
    [ENDPOINT, ENDPOINT + "/", ENDPOINT + "/openai/v1", ENDPOINT + "/openai/v1/"],
)
def test_transcription_uses_the_deployment_route(endpoint):
    from tubetell.foundry import Settings, make_transcribe_client

    client = make_transcribe_client(Settings(endpoint=endpoint, transcribe_deployment="stt", chat_deployment="chat", api_key="k"))
    assert str(client.base_url) == ENDPOINT + "/openai/"
    assert client._api_version == "2025-03-01-preview"


def test_make_client_uses_entra_by_default(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    seen = {}

    def provider(cred, scope):
        seen["scope"] = scope
        seen["cred"] = cred
        return lambda: "token"

    monkeypatch.setattr("azure.identity.get_bearer_token_provider", provider)
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", lambda: "default-cred")
    monkeypatch.setattr("openai.OpenAI", lambda **kw: seen.update(kw) or "client")

    assert foundry.make_client(settings()) == "client"
    assert seen["scope"] == "https://cognitiveservices.azure.com/.default"
    assert seen["cred"] == "default-cred"
    assert callable(seen["api_key"])
    assert seen["base_url"] == ENDPOINT + "/openai/v1/"


def test_make_client_falls_back_to_the_api_key(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.setattr(
        "azure.identity.DefaultAzureCredential", lambda: pytest.fail("no Entra with a key")
    )
    seen = {}
    monkeypatch.setattr("openai.OpenAI", lambda **kw: seen.update(kw))
    foundry.make_client(settings())
    assert seen["api_key"] == "k"


def _status_error(code, message="denied"):
    request = httpx.Request("POST", ENDPOINT + "/openai/v1/chat/completions")
    response = httpx.Response(code, request=request, json={"error": {"message": message}})
    return openai.APIStatusError(message, response=response, body=None)


def test_401_names_the_role_and_never_the_credential():
    err = foundry._api_error(_status_error(401, "PermissionDenied"))
    assert "Azure OpenAI error 401" in str(err)
    assert "Cognitive Services OpenAI User" in str(err)


def test_entra_failure_says_az_login():
    from azure.core.exceptions import ClientAuthenticationError

    err = foundry._api_error(ClientAuthenticationError("no account"))
    assert "az login" in str(err)


# --- step 1: chunks, offsets, merging, formatting ------------------------------------


def test_plan_chunks_short_span_is_one_chunk():
    assert plan_chunks(0, 600) == [(0, 600)]


def test_plan_chunks_overlap_and_cover_the_span():
    chunks = plan_chunks(0, 3000, length=1200, overlap=10)
    assert chunks == [(0, 1200), (1190, 2390), (2380, 3000)]


def test_plan_chunks_respect_a_clip_start():
    assert plan_chunks(90, 165) == [(90, 165)]
    assert plan_chunks(100, 100) == []


def test_speaker_label_is_generic_and_carries_the_part():
    assert speaker_label("A", None) == "Speaker A"
    assert speaker_label("B", 2) == "Speaker 2-B"
    assert speaker_label("", None) == "Speaker ?"


def test_segments_from_offsets_and_skips_empty_text():
    segs = segments_from(DIARIZED, offset=1190, part=2)
    assert [s.start for s in segs] == [1190.0, 1194.7, 1202.1]
    assert segs[1] == Segment(1194.7, 1201.8, "Speaker 2-B", "Glad to be here.")


def test_segments_from_reads_sdk_objects_too():
    resp = SimpleNamespace(
        segments=[SimpleNamespace(start=1.0, end=2.0, speaker="A", text="hej")]
    )
    assert segments_from(resp, offset=0, part=None) == [Segment(1.0, 2.0, "Speaker A", "hej")]


def test_merge_chunks_keeps_each_overlap_segment_once():
    chunks = [(0, 1200), (1190, 2000)]
    first = [
        Segment(1180, 1188, "Speaker 1-A", "before the overlap"),
        Segment(1192, 1199, "Speaker 1-A", "in the overlap"),  # midpoint 1195.5 > 1195
    ]
    second = [
        Segment(1192, 1199, "Speaker 2-B", "in the overlap"),
        Segment(1205, 1210, "Speaker 2-B", "after"),
    ]
    merged = merge_chunks(chunks, [first, second])
    assert [s.text for s in merged] == ["before the overlap", "in the overlap", "after"]
    assert merged[1].speaker == "Speaker 2-B"


def test_merge_single_chunk_keeps_everything():
    segs = segments_from(DIARIZED, offset=0, part=None)
    assert merge_chunks([(0, 30)], [segs]) == segs


def test_stamp_switches_format_at_the_hour():
    assert stamp(0) == "00:00"
    assert stamp(65.9) == "01:05"
    assert stamp(3599) == "59:59"
    assert stamp(3600) == "1:00:00"
    assert stamp(6420) == "1:47:00"


def test_format_transcript_is_tubetells_transcript_format():
    segs = segments_from(DIARIZED, offset=3590, part=None)
    assert format_transcript(segs) == (
        "[59:50] Speaker A: Thanks for joining.\n"
        "[59:54] Speaker B: Glad to be here.\n"
        "[1:00:02] Speaker A: Let's start."
    )


def test_transcription_usage_line_counts_seconds_and_requests():
    usage = TranscriptionUsage()
    usage.add(DIARIZED["usage"])
    usage.add({"type": "duration", "seconds": 1200})
    assert usage.line() == "transcribed: 1,227 s audio in 2 requests"
    tokens = TranscriptionUsage()
    tokens.add({"type": "tokens", "total_tokens": 1500})
    assert tokens.line() == "transcribed: 1,500 tokens in 1 request"


def _raw(data, headers=None):
    return SimpleNamespace(headers=headers or {}, parse=lambda: data)


def _stt_client(create):
    transcriptions = SimpleNamespace(with_raw_response=SimpleNamespace(create=create))
    return SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))


def test_transcribe_calls_diarize_per_chunk_and_merges(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    calls = []

    def fake_run(cmd, what):
        Path(cmd[-1]).write_bytes(b"mp3")

    def create(**kw):
        calls.append(kw)
        return _raw(DIARIZED)

    monkeypatch.setattr(foundry, "_run", fake_run)
    client = _stt_client(create)
    usage = TranscriptionUsage()
    segs = foundry.transcribe(client, settings(), Path("in.mov"), (0, 1500), tmp_path, usage)

    assert len(calls) == 2
    assert {c["response_format"] for c in calls} == {"diarized_json"}
    assert {c["chunking_strategy"] for c in calls} == {"auto"}
    assert {c["model"] for c in calls} == {"gpt-4o-transcribe-diarize"}
    assert segs[0].speaker == "Speaker 1-A"
    assert segs[-1].start == pytest.approx(1190 + 12.1)
    assert usage.requests == 2
    assert "transcribing 2/2" in capsys.readouterr().err


# --- step 2: frames -------------------------------------------------------------------


def test_frame_select_default_is_interval_plus_scene_changes():
    expr = frame_select(None)
    assert "gte(t-prev_selected_t\\,10)" in expr
    assert "gt(scene\\,0.3)" in expr


def test_frame_select_with_fps_is_a_fixed_rate_only():
    expr = frame_select(0.5)
    assert "gte(t-prev_selected_t\\,2)" in expr
    assert "scene" not in expr


def test_frames_command_seeks_to_the_span_and_downscales(tmp_path):
    cmd = frames_command(Path("in.mov"), tmp_path, (90, 165), fps=None)
    assert cmd[cmd.index("-ss") + 1] == "90"
    assert cmd[cmd.index("-to") + 1] == "165"
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.startswith("fps=2,")
    assert "scale='min(1024,iw)':-2" in vf
    assert vf.endswith("showinfo")
    assert cmd[-1].endswith("frame-%05d.jpg")


def test_frames_command_decodes_fast_enough_for_a_high_fps(tmp_path):
    cmd = frames_command(Path("in.mov"), tmp_path, (0, 10), fps=4)
    assert cmd[cmd.index("-vf") + 1].startswith("fps=4,")


def test_frame_times_parse_showinfo_and_add_the_span_start():
    log = (
        "[Parsed_showinfo_2 @ 0x6000] config in time_base: 1/2\n"
        "[Parsed_showinfo_2 @ 0x6000] n:   0 pts:      0 pts_time:0       duration: 1\n"
        "[Parsed_showinfo_2 @ 0x6000] n:   1 pts:     20 pts_time:10      duration: 1\n"
        "[Parsed_showinfo_2 @ 0x6000] n:   2 pts:     27 pts_time:13.5    duration: 1\n"
    )
    assert frame_times(log, 90) == [90.0, 100.0, 103.5]


def test_thin_keeps_everything_under_the_cap():
    assert thin([1, 2, 3], 5) == [1, 2, 3]


def test_thin_spreads_evenly_and_keeps_both_ends():
    items = list(range(100))
    kept = thin(items, 5)
    assert kept == [0, 25, 50, 74, 99]
    assert len(thin(items, MAX_FRAMES)) == MAX_FRAMES


def test_thin_edge_caps():
    assert thin([1, 2, 3], 1) == [1]
    assert thin([1, 2, 3], 0) == []


# --- step 3: prompt assembly ---------------------------------------------------------


def test_answer_text_keeps_the_standing_rules_and_the_bound():
    text = answer_text(
        "Summarize.",
        "[00:00] Speaker A: hi",
        duration=3725,
        description=None,
        multipart=False,
        has_frames=True,
    )
    assert text.startswith("Summarize.")
    assert "1:02:05" in text  # runtime upper bound
    assert "[h:mm:ss]" in text  # timestamp rule
    assert SPEAKER_RULE in text
    assert "--- TRANSCRIPT ---\n[00:00] Speaker A: hi\n--- END TRANSCRIPT ---" in text
    assert "frames follow" in text
    assert "Speaker N-A" not in text


def test_answer_text_explains_per_part_labels_and_carries_the_roster():
    text = answer_text(
        "Q", "", duration=None, description="Guests: Ada Lovelace", multipart=True, has_frames=False
    )
    assert "Speaker N-A" in text
    assert "Ada Lovelace" in text
    assert "no speech was transcribed" in text
    assert "frames follow" not in text


def test_chat_content_puts_each_timestamp_before_its_frame(tmp_path):
    frames = [Frame(5.0, tmp_path / "a.jpg"), Frame(3605.0, tmp_path / "b.jpg")]
    content = chat_content("prompt", frames, image_url=lambda p: f"url:{p.name}")
    assert content[0] == {"type": "text", "text": "prompt"}
    assert content[1] == {"type": "text", "text": "[frame 00:05]"}
    assert content[2]["image_url"]["url"] == "url:a.jpg"
    assert content[3] == {"type": "text", "text": "[frame 1:00:05]"}
    assert content[4]["type"] == "image_url"


def test_chat_content_data_url_is_base64_jpeg(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"\xff\xd8jpeg")
    url = chat_content("x", [Frame(0, p)])[2]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")


def test_format_chat_usage_splits_reasoning_from_output():
    usage = SimpleNamespace(
        prompt_tokens=60_000,
        completion_tokens=3_000,
        total_tokens=63_000,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=2_000),
    )
    assert format_chat_usage(usage) == (
        "tokens: 60,000 in + 2,000 thinking + 1,000 out = 63,000 total"
    )
    plain = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert format_chat_usage(plain) == "tokens: 10 in + 5 out = 15 total"
    assert format_chat_usage(None) is None


def _chat_client(text="The answer.", finish="stop", calls=None):
    def create(**kw):
        if calls is not None:
            calls.append(kw)
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=text))],
            usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_ask_sends_one_user_message_to_the_chat_deployment(monkeypatch, capsys):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    calls = []
    out = foundry.ask(_chat_client(calls=calls), settings(), [{"type": "text", "text": "Q"}])
    assert out == "The answer."
    assert calls[0]["model"] == "gpt-6.1-terra"
    assert calls[0]["messages"][0]["role"] == "user"
    assert "tokens: 1 in + 2 out = 3 total" in capsys.readouterr().err


def test_ask_warns_on_truncation_and_fails_on_empty(monkeypatch, capsys):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    foundry.ask(_chat_client(finish="length"), settings(), [])
    assert "cut off" in capsys.readouterr().err
    with pytest.raises(TubetellError, match="no answer"):
        foundry.ask(_chat_client(text=""), settings(), [])


def test_ask_wraps_api_errors(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)

    def create(**kw):
        raise _status_error(403)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(TubetellError, match="Azure OpenAI error 403"):
        foundry.ask(client, settings(), [])


# --- the whole path, network mocked ---------------------------------------------------


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    src = tmp_path / "meeting.mp4"
    src.write_bytes(b"video")
    monkeypatch.setattr(foundry, "_require", lambda tool: None)
    monkeypatch.setattr(
        foundry, "streams", lambda p: {"audio": True, "video": True, "duration": 300.0}
    )
    monkeypatch.setattr(
        foundry,
        "transcribe",
        lambda client, cfg, path, span, workdir, usage: segments_from(
            DIARIZED, offset=span[0], part=None
        ),
    )
    frames_seen = {}

    def frames(path, span, workdir, *, fps):
        frames_seen.update(span=span, fps=fps)
        return [Frame(span[0], tmp_path / "f.jpg")]

    monkeypatch.setattr(foundry, "sample_frames", frames)
    (tmp_path / "f.jpg").write_bytes(b"jpg")
    calls = []
    monkeypatch.setattr(foundry, "make_client", lambda cfg: _chat_client(calls=calls))
    return SimpleNamespace(src=src, calls=calls, frames=frames_seen)


def test_answer_transcript_mode_skips_the_chat_step(pipeline):
    out = foundry.answer(
        pipeline.src, mode="transcript", prompt=None, model=None, clip=None, fps=None, description=None
    )
    assert out.startswith("[00:00] Speaker A: Thanks for joining.")
    assert pipeline.calls == []
    assert pipeline.frames == {}


def test_answer_summary_sends_transcript_and_frames(pipeline):
    out = foundry.answer(
        pipeline.src, mode="summary", prompt=None, model="other-chat", clip=(60, 120), fps=0.5,
        description=None,
    )
    assert out == "The answer."
    assert pipeline.frames == {"span": (60.0, 120.0), "fps": 0.5}
    call = pipeline.calls[0]
    assert call["model"] == "other-chat"
    content = call["messages"][0]["content"]
    assert "[01:00] Speaker A: Thanks for joining." in content[0]["text"]
    assert "which runs 2:00" in content[0]["text"]  # bound is the clip's end
    assert content[1] == {"type": "text", "text": "[frame 01:00]"}


def test_answer_custom_prompt_in_transcript_mode_still_asks(pipeline):
    foundry.answer(
        pipeline.src, mode="transcript", prompt="Who decided what?", model=None, clip=None,
        fps=None, description=None,
    )
    assert pipeline.calls[0]["messages"][0]["content"][0]["text"].startswith("Who decided what?")


def test_answer_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    with pytest.raises(TubetellError, match="No such file"):
        foundry.answer(
            tmp_path / "nope.mp4", mode="summary", prompt=None, model=None, clip=None, fps=None,
            description=None,
        )


# --- the transcript cache and --transcript -------------------------------------------


@pytest.fixture
def cached_pipeline(pipeline, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    transcribed = []

    def transcribe(client, cfg, path, span, workdir, usage, speakers=()):
        transcribed.append(span)
        return segments_from(DIARIZED, offset=span[0], part=None)

    monkeypatch.setattr(foundry, "transcribe", transcribe)
    pipeline.transcribed = transcribed
    return pipeline


def _run(p, **kw):
    args = dict(mode="transcript", prompt=None, model=None, clip=None, fps=None, description=None, transcript=None)
    args.update(kw)
    return foundry.answer(p.src, **args)


def test_second_run_reads_the_cached_transcript(cached_pipeline, capsys):
    first = _run(cached_pipeline)
    second = _run(cached_pipeline)
    assert first == second
    assert cached_pipeline.transcribed == [(0.0, 300.0)]
    assert "transcript: cached" in capsys.readouterr().err


def test_a_clip_is_sliced_from_the_cached_full_transcript(cached_pipeline):
    _run(cached_pipeline)
    out = _run(cached_pipeline, clip=(3, 60))
    assert cached_pipeline.transcribed == [(0.0, 300.0)]
    assert out == "[00:04] Speaker B: Glad to be here.\n[00:12] Speaker A: Let's start."


def test_a_clip_outside_the_cache_is_transcribed_and_cached(cached_pipeline):
    _run(cached_pipeline, clip=(60, 120))
    _run(cached_pipeline, clip=(60, 120))
    assert cached_pipeline.transcribed == [(60.0, 120.0)]


def test_another_transcribe_deployment_has_its_own_cache(cached_pipeline, monkeypatch):
    _run(cached_pipeline)
    monkeypatch.setenv("AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT", "other-stt")
    _run(cached_pipeline)
    assert len(cached_pipeline.transcribed) == 2


def test_transcript_file_replaces_transcription(cached_pipeline, tmp_path, capsys):
    corrected = tmp_path / "corrected.md"
    corrected.write_text("[00:00] Ada: Thanks for joining.\n[00:05] Bob: Glad to be here.\n")
    out = _run(cached_pipeline, transcript=corrected)
    assert out == "[00:00] Ada: Thanks for joining.\n[00:05] Bob: Glad to be here."
    assert cached_pipeline.transcribed == []
    assert "transcript: corrected.md" in capsys.readouterr().err
    # The corrected names are what the chat step sees, and no part-label note.
    _run(cached_pipeline, transcript=corrected, mode="summary")
    text = cached_pipeline.calls[0]["messages"][0]["content"][0]["text"]
    assert "[00:05] Bob: Glad to be here." in text
    assert "Speaker N-A" not in text


def test_transcript_file_is_sliced_by_clip(cached_pipeline, tmp_path):
    t = tmp_path / "t.md"
    t.write_text("[00:00] Ada: one\n[01:00] Ada: two\n[02:00] Ada: three\n")
    assert _run(cached_pipeline, transcript=t, clip=(50, 110)) == "[01:00] Ada: two"


def test_voice_note_follows_the_labels_not_the_chunk_plan(cached_pipeline, tmp_path):
    t = tmp_path / "t.md"
    t.write_text("[00:00] Speaker 1-A: one\n[00:10] Speaker 2-B: two\n")
    _run(cached_pipeline, transcript=t, mode="summary")
    assert "Speaker N-A" in cached_pipeline.calls[0]["messages"][0]["content"][0]["text"]


# --- known speakers -----------------------------------------------------------------


def test_speaker_label_keeps_a_known_name_bare():
    assert speaker_label("Ada", 2, known=("Ada",)) == "Ada"
    assert speaker_label("A", 2, known=("Ada",)) == "Speaker 2-A"


def test_transcribe_sends_the_references_with_every_chunk(monkeypatch, tmp_path, capsys):
    from tubetell.speakers import Speaker

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    ref = tmp_path / "Ada.mp3"
    ref.write_bytes(b"\xff\xfb")
    calls = []

    def fake_run(cmd, what):
        Path(cmd[-1]).write_bytes(b"mp3")

    def create(**kw):
        calls.append(kw)
        named = json.loads(json.dumps(DIARIZED))
        named["segments"][0]["speaker"] = "Ada"
        return _raw(named)

    monkeypatch.setattr(foundry, "_run", fake_run)
    client = _stt_client(create)
    segs = foundry.transcribe(
        client, settings(), Path("in.mov"), (0, 1500), tmp_path, TranscriptionUsage(),
        speakers=[Speaker("Ada", ref)],
    )
    assert len(calls) == 6  # 5-minute chunks (10 s overlap) with known speakers
    assert calls[1]["known_speaker_names"] == ["Ada"]
    assert calls[1]["known_speaker_references"] == ["data:audio/mp3;base64,//s="]
    assert segs[0].speaker == "Ada"
    assert segs[1].speaker == "Speaker 1-B"


def test_known_speakers_get_their_own_cache_and_a_softer_voice_note(cached_pipeline, monkeypatch, tmp_path):
    from tubetell.speakers import Speaker

    ref = tmp_path / "Ada.mp3"
    ref.write_bytes(b"x")
    seen = []
    monkeypatch.setattr(
        foundry, "transcribe",
        lambda client, cfg, path, span, workdir, usage, speakers=(): seen.append(
            [s.name for s in speakers]
        ) or [Segment(0, 1, "Ada", "hi"), Segment(1, 2, "Speaker 1-B", "yo"), Segment(1200, 1201, "Speaker 2-A", "x")],
    )
    _run(cached_pipeline)  # plain: cached under the plain key
    _run(cached_pipeline, speakers=[Speaker("Ada", ref)], mode="summary")
    _run(cached_pipeline, speakers=[Speaker("Ada", ref)], mode="summary")
    assert seen == [[], ["Ada"]]
    text = cached_pipeline.calls[0]["messages"][0]["content"][0]["text"]
    assert "Speaker N-A" in text
    assert "A label that is a name" in text


# --- short, parallel chunks with known speakers ---------------------------------------


def test_known_speakers_make_the_chunks_short():
    from tubetell.foundry import SHORT_CHUNK_SECONDS, chunk_length

    assert chunk_length(named=False) == 1200
    assert chunk_length(named=True) == SHORT_CHUNK_SECONDS == 300


def test_parallelism_follows_the_requests_per_minute_header():
    from tubetell.foundry import parallelism

    assert parallelism({"x-ratelimit-limit-requests": "30000"}) == 8
    assert parallelism({"x-ratelimit-limit-requests": "5000"}) == 2
    assert parallelism({"x-ratelimit-limit-requests": "1000"}) == 1
    assert parallelism({}) == 2


def test_transcribe_runs_the_later_chunks_at_the_same_time(monkeypatch, tmp_path, capsys):
    import threading

    from tubetell.speakers import Speaker

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    ref = tmp_path / "Ada.mp3"
    ref.write_bytes(b"x")
    monkeypatch.setattr(foundry, "_run", lambda cmd, what: Path(cmd[-1]).write_bytes(b"mp3"))
    # Four chunks (0-300, 290-590, 580-880, 870-1000): the first alone, then
    # three together. The barrier only opens when all three run at once.
    together = threading.Barrier(3, timeout=5)
    seen = []

    def create(**kw):
        seen.append(kw["file"].name)
        if len(seen) > 1:
            together.wait()
        return _raw(DIARIZED, {"x-ratelimit-limit-requests": "30000"})

    usage = TranscriptionUsage()
    segs = foundry.transcribe(
        _stt_client(create), settings(), Path("in.mov"), (0, 1000), tmp_path, usage,
        speakers=[Speaker("Ada", ref)],
    )
    assert len(seen) == 4
    assert usage.requests == 4
    assert [s.speaker for s in segs][:2] == ["Speaker 1-A", "Speaker 1-B"]
    assert segs[-1].start == pytest.approx(870 + 12.1)
    assert "8 at a time" in capsys.readouterr().err


# --- glossary -------------------------------------------------------------------------


def test_glossary_corrects_before_caching_and_keys_the_cache(cached_pipeline, monkeypatch, tmp_path):
    from tubetell import glossary

    corrected = []
    monkeypatch.setattr(
        glossary, "correct",
        lambda client, cfg, segments, terms: corrected.append(terms)
        or [Segment(s.start, s.end, s.speaker, s.text.replace("joining", "Kiana")) for s in segments],
    )
    out = _run(cached_pipeline, glossary_terms=["Kiana"])
    assert out.startswith("[00:00] Speaker A: Thanks for Kiana.")
    assert corrected == [["Kiana"]]
    # The corrected transcript is what the cache holds; the plain key is separate.
    assert _run(cached_pipeline, glossary_terms=["Kiana"]) == out
    assert corrected == [["Kiana"]]
    assert _run(cached_pipeline).startswith("[00:00] Speaker A: Thanks for joining.")
    assert cached_pipeline.transcribed == [(0.0, 300.0), (0.0, 300.0)]
