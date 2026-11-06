import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tubetell.foundry import Frame, TranscriptionUsage, settings
from tubetell.screen import (
    Observation,
    batches,
    format_observations,
    load,
    parse_answer,
    save,
    screen_cache_path,
    screen_request,
)

ENDPOINT = "https://data-and-analytics-resource.cognitiveservices.azure.com"


def test_batches_cut_the_frames_in_order(tmp_path):
    frames = [Frame(float(i), tmp_path / f"{i}.jpg") for i in range(7)]
    assert [len(b) for b in batches(frames, 3)] == [3, 3, 1]
    assert batches(frames, 3)[2][0].time == 6.0
    assert batches([], 3) == []


def test_screen_request_lists_the_frames_it_expects(tmp_path):
    frames = [Frame(5.0, tmp_path / "a.jpg"), Frame(3605.0, tmp_path / "b.jpg")]
    text = screen_request(frames)
    assert "2 still frames" in text
    assert "00:05" in text and "1:00:05" in text
    assert "JSON" in text


def test_parse_answer_reads_one_object_per_line_and_tolerates_fences():
    answer = (
        "```json\n"
        '{"frame": "00:05", "shown": "a dashboard", "legible": "Revenue 12.3", "wrong": "nothing"}\n'
        '{"frame": "00:15", "shown": "a slide", "legible": "Q3", "wrong": "date says 2025"}\n'
        "```"
    )
    obs = parse_answer(answer, [5.0, 15.0])
    assert obs == [
        Observation(5.0, "a dashboard", "Revenue 12.3", "nothing"),
        Observation(15.0, "a slide", "Q3", "date says 2025"),
    ]


def test_parse_answer_keeps_prose_as_one_observation_per_batch():
    obs = parse_answer("Both frames show a login page.", [5.0, 15.0])
    assert obs == [Observation(5.0, "Both frames show a login page.", "", "")]


def test_observations_round_trip_as_jsonl_and_format_as_lines(tmp_path):
    obs = [Observation(5.0, "a dashboard", "Revenue 12.3", "nothing"), Observation(3605.0, "a slide", "", "")]
    save(tmp_path / "s.jsonl", obs)
    assert len((tmp_path / "s.jsonl").read_text().splitlines()) == 2
    assert load(tmp_path / "s.jsonl") == obs
    assert format_observations(obs) == (
        "[00:05] shown: a dashboard | legible: Revenue 12.3 | wrong: nothing\n"
        "[1:00:05] shown: a slide"
    )


def test_screen_cache_path_is_keyed_on_content_span_deployment_and_sampling(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    src = tmp_path / "m.mp4"
    src.write_bytes(b"v")
    a = screen_cache_path(src, (0.0, 300.0), "gpt-6.1-terra", fps=None)
    assert a.name == "screen-gpt-6.1-terra-0-300-auto.jsonl"
    assert screen_cache_path(src, (0.0, 300.0), "gpt-6.1-terra", fps=0.5).name.endswith("-0.5.jsonl")
    assert a.parent.parent.name == "transcripts"


def test_observe_sends_batches_and_caches(monkeypatch, tmp_path, capsys):
    from tubetell import screen

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(screen, "BATCH_FRAMES", 2)
    src = tmp_path / "m.mp4"
    src.write_bytes(b"v")
    frames = []
    for i in range(3):
        p = tmp_path / f"{i}.jpg"
        p.write_bytes(b"\xff\xd8")
        frames.append(Frame(float(i * 10), p))
    calls = []

    def create(**kw):
        calls.append(kw)
        images = [c for c in kw["messages"][0]["content"] if c["type"] == "image_url"]
        times = [c["text"] for c in kw["messages"][0]["content"] if c.get("text", "").startswith("[frame")]
        lines = "\n".join(
            json.dumps({"frame": t[7:-1], "shown": f"pic {t}", "legible": "", "wrong": "nothing"})
            for t in times
        )
        assert len(images) == len(times)
        parsed = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=lines))],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )
        return parsed

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(with_raw_response=SimpleNamespace(
        create=lambda **kw: SimpleNamespace(headers={}, parse=lambda: create(**kw))
    ))))
    usage = TranscriptionUsage()
    obs = screen.observe(client, settings(), src, (0.0, 30.0), frames, fps=None, usage=usage)
    assert len(calls) == 2
    assert usage.cost is not None and usage.cost > 0  # both requests charged to the run
    assert [o.time for o in obs] == [0.0, 10.0, 20.0]
    assert obs[2].shown == "pic [frame 00:20]"
    again = screen.observe(client, settings(), src, (0.0, 30.0), frames, fps=None)
    assert again == obs
    assert len(calls) == 2
    assert "screen: cached" in capsys.readouterr().err
