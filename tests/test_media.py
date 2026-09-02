import pytest

from tubetell import TubetellError
from tubetell import media
from tubetell.media import (
    format_offset,
    looks_like_path,
    mime_type,
    parse_clip,
    parse_offset,
    source_duration,
    source_kind,
    source_part,
    video_metadata,
    youtube_url,
)


@pytest.fixture(autouse=True)
def proxy_cache(tmp_path, monkeypatch):
    """Keep test proxies out of the shared cache in the system temp dir."""
    cache = tmp_path / "proxy-cache"
    cache.mkdir()
    monkeypatch.setattr(media, "_cache_dir", lambda: cache)
    return cache


@pytest.mark.parametrize(
    "source, expected",
    [
        ("https://youtu.be/vOVKnYoH1p4", False),
        ("gs://bucket/game.mp4", False),
        ("vOVKnYoH1p4", False),
        ("recording.mov", True),
        ("~/clips/game.mp4", True),
        ("./out.webm", True),
        ("/Users/me/Skärminspelning.mov", True),
    ],
)
def test_looks_like_path_separates_files_from_remote_sources(source, expected):
    assert looks_like_path(source) is expected


def test_looks_like_path_catches_an_existing_extensionless_file(tmp_path, monkeypatch):
    (tmp_path / "clip").write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    assert looks_like_path("clip") is True


@pytest.mark.parametrize(
    "text, seconds",
    [("45", 45), ("1:30", 90), ("0:05", 5), ("1:00:00", 3600), ("2:03:04", 7384)],
)
def test_parse_offset_reads_every_timestamp_shape(text, seconds):
    assert parse_offset(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "1:2:3:4", "1:xx"])
def test_parse_offset_rejects_non_timestamps(text):
    with pytest.raises(TubetellError, match="timestamp"):
        parse_offset(text)


def test_parse_clip_returns_a_second_range():
    assert parse_clip("1:30-2:45") == (90, 165)


def test_parse_clip_requires_a_range_and_a_forward_span():
    with pytest.raises(TubetellError, match="START-END"):
        parse_clip("1:30")
    with pytest.raises(TubetellError, match="end must come after"):
        parse_clip("2:45-1:30")


def test_mime_type_rejects_formats_gemini_cannot_read(tmp_path):
    with pytest.raises(TubetellError, match="Unsupported file type"):
        mime_type(tmp_path / "notes.txt")


@pytest.mark.parametrize(
    "source, expected",
    [
        ("recording.mov", "local"),
        ("gs://bucket/game.mp4", "gs"),
        ("vOVKnYoH1p4", "youtube"),
        ("https://youtu.be/vOVKnYoH1p4", "youtube"),
        ("https://www.youtube.com/watch?v=vOVKnYoH1p4", "youtube"),
    ],
)
def test_source_kind_classifies_local_gs_and_youtube(source, expected):
    assert source_kind(source) == expected


def test_youtube_url_expands_a_bare_id_and_keeps_a_full_url():
    assert youtube_url("vOVKnYoH1p4") == "https://www.youtube.com/watch?v=vOVKnYoH1p4"
    assert youtube_url("https://youtu.be/vOVKnYoH1p4") == "https://youtu.be/vOVKnYoH1p4"


def test_source_part_passes_a_youtube_url_through_as_a_reference():
    part = source_part("https://youtu.be/vOVKnYoH1p4")
    assert part.file_data.file_uri == "https://youtu.be/vOVKnYoH1p4"
    assert part.file_data.mime_type == "video/*"
    assert part.inline_data is None


def test_source_part_expands_a_bare_video_id_to_a_watch_url():
    part = source_part("vOVKnYoH1p4")
    assert part.file_data.file_uri == "https://www.youtube.com/watch?v=vOVKnYoH1p4"


def test_source_part_keeps_the_gcs_uri_and_names_its_type():
    part = source_part("gs://bucket/clips/game.webm")
    assert part.file_data.file_uri == "gs://bucket/clips/game.webm"
    assert part.file_data.mime_type == "video/webm"


def test_source_part_sends_a_small_local_file_inline_untouched(tmp_path, monkeypatch):
    clip = tmp_path / "game.mp4"
    clip.write_bytes(b"tiny video")
    monkeypatch.setattr(media, "transcode", _fail_if_called)

    part = source_part(str(clip))

    assert part.inline_data.data == b"tiny video"
    assert part.inline_data.mime_type == "video/mp4"


def test_source_part_reports_a_missing_file_instead_of_guessing_a_video_id(tmp_path):
    with pytest.raises(TubetellError, match="No such file"):
        source_part(str(tmp_path / "gone.mp4"))


def test_source_part_rejects_a_directory(tmp_path):
    with pytest.raises(TubetellError, match="directory"):
        source_part(str(tmp_path))


def test_no_transcode_refuses_an_oversized_file_rather_than_failing_at_vertex(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "INLINE_LIMIT", 4)
    clip = tmp_path / "game.mp4"
    clip.write_bytes(b"much too large")

    with pytest.raises(TubetellError, match="--no-transcode"):
        source_part(str(clip), transcode_enabled=False)


def test_an_oversized_file_is_proxied_and_sent_inline(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "INLINE_LIMIT", 10)
    clip = tmp_path / "game.mov"
    clip.write_bytes(b"a 300 MB retina screen recording")
    calls = []

    def fake_transcode(src, dest, *, width, fps, clip):
        calls.append((width, fps, clip))
        dest.write_bytes(b"proxied")

    monkeypatch.setattr(media, "transcode", fake_transcode)

    part = source_part(str(clip), width=1280, fps=2.0)

    assert part.inline_data.data == b"proxied"
    assert part.inline_data.mime_type == "video/mp4"
    assert calls == [(1280, 2.0, None)]


def test_a_proxy_still_over_the_cap_falls_back_to_coarser_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "INLINE_LIMIT", 10)
    monkeypatch.setattr(media, "PROXY_LADDER", ((960, 1.0), (640, 1.0)))
    clip = tmp_path / "long.mov"
    clip.write_bytes(b"a very long recording")
    calls = []

    def fake_transcode(src, dest, *, width, fps, clip):
        calls.append(width)
        dest.write_bytes(b"x" * (100 if width > 640 else 5))

    monkeypatch.setattr(media, "transcode", fake_transcode)

    part = source_part(str(clip))

    assert calls == [1280, 960, 640]
    assert part.inline_data.data == b"xxxxx"


def test_a_file_that_never_fits_says_to_clip_it(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "INLINE_LIMIT", 10)
    clip = tmp_path / "movie.mov"
    clip.write_bytes(b"a feature film")
    monkeypatch.setattr(media, "transcode", lambda src, dest, **kw: dest.write_bytes(b"x" * 99))

    with pytest.raises(TubetellError, match="--clip"):
        source_part(str(clip))


def test_a_proxy_is_transcoded_once_and_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "INLINE_LIMIT", 10)
    clip = tmp_path / "game.mov"
    clip.write_bytes(b"a 300 MB retina screen recording")
    calls = []

    def fake_transcode(src, dest, *, width, fps, clip):
        calls.append(width)
        dest.write_bytes(b"proxied")

    monkeypatch.setattr(media, "transcode", fake_transcode)

    source_part(str(clip))
    source_part(str(clip))
    source_part(str(clip).replace("/game.mov", "/./game.mov"))

    assert calls == [1280]


def test_the_proxy_cuts_the_clip_so_gemini_does_not_cut_it_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "INLINE_LIMIT", 10)
    clip_file = tmp_path / "game.mov"
    clip_file.write_bytes(b"a 300 MB retina screen recording")
    monkeypatch.setattr(media, "transcode", lambda src, dest, **kw: dest.write_bytes(b"cut"))

    part = source_part(str(clip_file), clip=(90, 165), fps=2.0)

    assert part.video_metadata.fps == 2.0
    assert part.video_metadata.start_offset is None
    assert part.video_metadata.end_offset is None


def test_a_small_local_file_lets_gemini_do_the_clipping(tmp_path, monkeypatch):
    clip_file = tmp_path / "game.mp4"
    clip_file.write_bytes(b"tiny")
    monkeypatch.setattr(media, "transcode", _fail_if_called)

    part = source_part(str(clip_file), clip=(90, 165))

    assert part.video_metadata.start_offset == "90s"
    assert part.video_metadata.end_offset == "165s"


def test_video_metadata_is_left_unset_when_nothing_was_asked_for():
    assert video_metadata(fps=None, clip=None) is None
    assert source_part("vOVKnYoH1p4").video_metadata is None


def _fail_if_called(*args, **kwargs):
    raise AssertionError("should not transcode")


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0, "0:00"),
        (9, "0:09"),
        (95, "1:35"),
        (3599, "59:59"),
        (3600, "1:00:00"),
        (5732, "1:35:32"),
        (5731.6, "1:35:32"),
        (37_205, "10:20:05"),
    ],
)
def test_format_offset_switches_to_hours_only_past_the_hour(seconds, expected):
    assert format_offset(seconds) == expected


@pytest.mark.parametrize("text", ["0:09", "1:35", "59:59", "1:00:00", "1:35:32"])
def test_format_offset_round_trips_parse_offset(text):
    assert format_offset(parse_offset(text)) == text


def test_source_duration_probes_a_local_file(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(media, "probe", lambda path: {"width": 1920, "duration": 5732.0})
    assert source_duration(str(clip)) == 5732.0


def test_source_duration_is_none_for_a_missing_file_or_gcs_object(tmp_path):
    assert source_duration(str(tmp_path / "gone.mp4")) is None
    assert source_duration("gs://bucket/game.mp4") is None


def test_source_duration_asks_youtube_for_a_remote_video(monkeypatch):
    seen = []
    monkeypatch.setattr(media, "fetch_duration", lambda url: seen.append(url) or 1234.0)
    assert source_duration("https://youtu.be/vOVKnYoH1p4") == 1234.0
    assert seen == ["https://youtu.be/vOVKnYoH1p4"]
