import pytest

from tubetell import download


@pytest.mark.parametrize(
    "url",
    ["https://x.com/shirosignal/status/2111895887098139007", "https://vimeo.com/123", "http://twitter.com/a/status/1"],
)
def test_other_sites_are_downloaded(url):
    assert download.is_downloadable(url)


@pytest.mark.parametrize(
    "source",
    [
        "https://www.youtube.com/watch?v=vOVKnYoH1p4",
        "https://youtu.be/vOVKnYoH1p4",
        "https://m.youtube.com/shorts/vOVKnYoH1p4",
        "vOVKnYoH1p4",
        "gs://bucket/clip.mp4",
        "clip.mp4",
    ],
)
def test_youtube_gcs_and_files_are_not(source):
    assert not download.is_downloadable(source)


def test_cached_download_is_reused_without_yt_dlp(tmp_path, monkeypatch):
    monkeypatch.setattr(download.tempfile, "gettempdir", lambda: str(tmp_path))
    url = "https://x.com/a/status/1"
    d = download._cache_dir(url)
    (d / "video.mp4").write_bytes(b"x")
    (d / "video.info.json").write_text('{"description": "the post"}')
    monkeypatch.setattr(download.shutil, "which", lambda _: pytest.fail("must not download"))
    assert download.fetch(url) == (d / "video.mp4", "the post")


def test_partial_download_is_not_mistaken_for_the_video(tmp_path):
    (tmp_path / "video.mp4.part").write_bytes(b"x")
    (tmp_path / "video.info.json").write_text("{}")
    assert download._cached(tmp_path) is None
