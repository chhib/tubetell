from pathlib import Path

import pytest

from tubetell import TubetellError
from tubetell import speakers
from tubetell.speakers import Speaker, cache_tag, parse_flag, references, resolve


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "tubetell" / "speakers"


def test_parse_flag_reads_a_name_and_an_optional_span():
    assert parse_flag("Ada=0:01-0:10") == ("Ada", (1, 10))
    assert parse_flag("Ada Lovelace") == ("Ada Lovelace", None)


@pytest.mark.parametrize("flag", ["=0:01-0:10", "Ada=0:01", "Ada=0:10-0:01", "Ada=0:00-0:01", "Ada=0:00-0:11"])
def test_parse_flag_rejects_a_bad_span(flag):
    with pytest.raises(TubetellError, match="--speaker"):
        parse_flag(flag)


def test_resolve_cuts_a_new_reference_from_the_recording_and_keeps_it(config, monkeypatch):
    cuts = []

    def cut(src, dest, span):
        cuts.append((src, dest, span))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"mp3")

    monkeypatch.setattr(speakers, "cut_reference", cut)
    out = resolve(["Ada=0:01-0:10"], Path("meeting.mp4"))
    assert out == [Speaker("Ada", config / "Ada.mp3")]
    assert cuts == [(Path("meeting.mp4"), config / "Ada.mp3", (1, 10))]
    assert (config / "Ada.mp3").read_bytes() == b"mp3"


def test_resolve_finds_a_stored_reference_by_name(config, monkeypatch):
    config.mkdir(parents=True)
    (config / "Bob.mp3").write_bytes(b"mp3")
    monkeypatch.setattr(speakers, "cut_reference", lambda *a: pytest.fail("stored, no cut"))
    assert resolve(["Bob"], Path("meeting.mp4")) == [Speaker("Bob", config / "Bob.mp3")]


def test_resolve_names_the_missing_reference_and_where_it_looked(config):
    with pytest.raises(TubetellError, match=r"Bob.*speakers/Bob\.mp3"):
        resolve(["Bob"], Path("meeting.mp4"))


def test_resolve_allows_at_most_four(config):
    config.mkdir(parents=True)
    for n in "ABCDE":
        (config / f"{n}.mp3").write_bytes(b"x")
    with pytest.raises(TubetellError, match="at most 4"):
        resolve(list("ABCDE"), Path("m.mp4"))


def test_references_are_data_urls_and_the_tag_is_the_sorted_names(tmp_path):
    a = tmp_path / "Ada.mp3"
    a.write_bytes(b"\xff\xfb")
    found = [Speaker("Zed", a), Speaker("Ada", a)]
    assert references(found) == ["data:audio/mp3;base64,//s=", "data:audio/mp3;base64,//s="]
    assert cache_tag(found) == "Ada+Zed"
    assert cache_tag([]) == ""


def test_cut_reference_uses_the_pipelines_audio_settings(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(speakers, "_run", lambda cmd, what: seen.update(cmd=cmd))
    speakers.cut_reference(Path("in.mov"), tmp_path / "Ada.mp3", (61, 70))
    cmd = seen["cmd"]
    assert cmd[cmd.index("-ss") + 1] == "61"
    assert cmd[cmd.index("-to") + 1] == "70"
    assert "libmp3lame" in cmd
