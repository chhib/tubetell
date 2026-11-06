import json

import pytest

from tubetell import TubetellError
from tubetell.transcript import Segment
from tubetell.transcript import (
    Transcript,
    cache_path,
    is_multipart,
    load_file,
    parse_text,
    slice_segments,
)

SEGMENTS = [
    Segment(0.0, 4.7, "Speaker A", "Thanks for joining."),
    Segment(4.7, 11.8, "Speaker B", "Glad to be here."),
    Segment(3612.1, 3618.5, "Speaker A", "Let's start."),
]


# --- the cache file ---------------------------------------------------------------


def test_cache_path_is_keyed_on_content_span_and_deployment(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    src = tmp_path / "meeting.mp4"
    src.write_bytes(b"video")
    a = cache_path(src, (0.0, 300.0), "gpt-4o-transcribe-diarize")
    assert a.parent.parent == tmp_path / "tubetell" / "transcripts"
    assert a.name == "gpt-4o-transcribe-diarize-0-300.json"
    assert cache_path(src, (0.0, 300.0), "other") != a
    assert cache_path(src, (0.0, 301.0), "gpt-4o-transcribe-diarize") != a
    same = tmp_path / "copy.mp4"
    same.write_bytes(b"video")
    assert cache_path(same, (0.0, 300.0), "gpt-4o-transcribe-diarize") == a
    src.write_bytes(b"other video")
    assert cache_path(src, (0.0, 300.0), "gpt-4o-transcribe-diarize") != a


def test_cache_path_takes_a_tag(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    src = tmp_path / "meeting.mp4"
    src.write_bytes(b"video")
    plain = cache_path(src, (0.0, 300.0), "stt")
    tagged = cache_path(src, (0.0, 300.0), "stt", tag="ada+bob")
    assert tagged != plain
    assert tagged.name == "stt-0-300-ada+bob.json"


def test_transcript_round_trips_through_its_file(tmp_path):
    path = tmp_path / "t.json"
    t = Transcript(source="meeting.mp4", span=(0.0, 3620.0), deployment="stt", segments=SEGMENTS)
    t.save(path)
    data = json.loads(path.read_text())
    assert data["span"] == [0.0, 3620.0]
    assert data["segments"][1] == {
        "start": 4.7, "end": 11.8, "speaker": "Speaker B", "text": "Glad to be here."
    }
    assert Transcript.load(path) == t


def test_slice_keeps_segments_whose_midpoint_is_in_the_span():
    assert slice_segments(SEGMENTS, (3.0, 60.0)) == [SEGMENTS[1]]
    assert slice_segments(SEGMENTS, (0.0, 4000.0)) == SEGMENTS
    assert slice_segments(SEGMENTS, (100.0, 200.0)) == []


def test_transcript_covers_a_span_inside_its_own():
    t = Transcript(source="m", span=(0.0, 3620.0), deployment="stt", segments=SEGMENTS)
    assert t.covers((60.0, 120.0))
    assert t.covers((0.0, 3620.0))
    assert not t.covers((60.0, 4000.0))


# --- a finished transcript as text ------------------------------------------------


def test_parse_text_reads_tubetells_transcript_format():
    text = (
        "[00:00] Speaker A: Thanks for joining.\n"
        "[00:04] Ada: Glad to be here.\n"
        "\n"
        "[1:00:12] Speaker A: Let's start.\n"
    )
    segs = parse_text(text)
    assert [s.speaker for s in segs] == ["Speaker A", "Ada", "Speaker A"]
    assert [s.start for s in segs] == [0.0, 4.0, 3612.0]
    # Each segment ends where the next starts; the last one is a point.
    assert [s.end for s in segs] == [4.0, 3612.0, 3612.0]
    assert segs[1].text == "Glad to be here."


def test_parse_text_rejects_a_file_with_no_transcript_lines():
    with pytest.raises(TubetellError, match="no transcript lines"):
        parse_text("Just prose, no timestamps.\n")


def test_load_file_takes_json_or_text(tmp_path):
    t = Transcript(source="m", span=(0.0, 20.0), deployment="stt", segments=SEGMENTS[:2])
    t.save(tmp_path / "t.json")
    assert load_file(tmp_path / "t.json").segments == SEGMENTS[:2]
    (tmp_path / "t.md").write_text("[00:05] Ada: hi\n")
    loaded = load_file(tmp_path / "t.md")
    assert loaded.segments == [Segment(5.0, 5.0, "Ada", "hi")]
    assert loaded.deployment is None


def test_load_file_missing_is_a_clear_error(tmp_path):
    with pytest.raises(TubetellError, match="No such transcript"):
        load_file(tmp_path / "nope.md")


# --- what the labels say about the parts -------------------------------------------


def test_is_multipart_reads_the_part_prefix_off_the_labels():
    assert not is_multipart(SEGMENTS)
    assert is_multipart([Segment(0, 1, "Speaker 2-A", "x")])
    assert not is_multipart([Segment(0, 1, "Ada", "x")])
