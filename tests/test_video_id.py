import pytest

from tubetell import TubetellError
from tubetell.youtube import video_id


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=vOVKnYoH1p4",
        "https://www.youtube.com/watch?v=vOVKnYoH1p4&t=42s",
        "https://youtu.be/vOVKnYoH1p4",
        "https://www.youtube.com/shorts/vOVKnYoH1p4",
        "https://www.youtube.com/embed/vOVKnYoH1p4",
        "vOVKnYoH1p4",
    ],
)
def test_extracts_id(url):
    assert video_id(url) == "vOVKnYoH1p4"


def test_garbage_raises():
    with pytest.raises(TubetellError, match="Could not extract"):
        video_id("https://example.com/not-a-video")
