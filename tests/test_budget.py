from tubetell.budget import INPUT_BUDGET, estimate_tokens, plan


def test_hour_of_video_overflows_at_default_sampling():
    # The failure that motivated this: ~65 min talking-head video, 400 from Vertex.
    assert estimate_tokens(65 * 60, fps=None, low_res=False) > 1_048_576
    assert estimate_tokens(65 * 60, fps=None, low_res=True) < INPUT_BUDGET


def test_short_video_needs_no_plan():
    p = plan(20 * 60, fps=None, clip=None)
    assert not p.low_res and not p.clips


def test_unknown_runtime_means_no_plan():
    assert plan(None, fps=None, clip=None).clips == ()


def test_hour_drops_to_low_res_without_chunking():
    p = plan(65 * 60, fps=None, clip=None)
    assert p.low_res and not p.chunked


def test_very_long_video_is_chunked_contiguously():
    p = plan(5 * 3600, fps=None, clip=None)
    assert p.low_res and p.chunked
    assert p.clips[0][0] == 0 and p.clips[-1][1] == 5 * 3600
    for (_, a_end), (b_start, _) in zip(p.clips, p.clips[1:]):
        assert a_end == b_start
    for a, b in p.clips:
        assert estimate_tokens(b - a, fps=None, low_res=True) <= INPUT_BUDGET


def test_user_clip_is_the_range_that_is_fitted():
    p = plan(5 * 3600, fps=None, clip=(0, 600))
    assert not p.low_res and not p.clips


def test_user_fps_is_respected_in_estimate():
    assert plan(65 * 60, fps=0.2, clip=None) == plan(20 * 60, fps=None, clip=None)
