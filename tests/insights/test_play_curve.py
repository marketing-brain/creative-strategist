from cs.insights.play_curve import BUCKETS, PLAYS_FLOOR, shape


def _rows(mine: bool, shares: list[float], plays: int) -> list[dict]:
    return [{"mine": mine, "bucket": i, "share": s, "plays": plays} for i, s in enumerate(shares)]


def test_buckets_match_metas_22():
    assert len(BUCKETS) == 22
    assert BUCKETS[0] == (0, 1) and BUCKETS[14] == (14, 15) and BUCKETS[15] == (15, 20)
    assert BUCKETS[-1] == (60, None)


def test_shape_scales_to_shares_and_cuts_at_the_videos_end():
    rows = _rows(True, [100.0] + [50.0] * 21, 5_000) + _rows(False, [100.0] + [40.0] * 21, 9_000)
    out = shape(rows, 12.5, "other videos 0-15s")
    assert out is not None
    # Buckets starting at 13s and later begin after the video has ended.
    assert [p["start_s"] for p in out["points"]] == list(range(13))
    assert out["points"][1]["share"] == 0.5 and out["points"][1]["baseline_share"] == 0.4
    assert out["baseline_label"] == "other videos 0-15s"


def test_shape_needs_enough_plays():
    assert shape(_rows(True, [100.0] * 22, PLAYS_FLOOR - 1), 90.0, None) is None
    thin_peers = _rows(True, [100.0] * 22, 5_000) + _rows(False, [100.0] * 22, 10)
    out = shape(thin_peers, 90.0, "peers")
    assert out is not None and out["baseline_label"] is None
    assert all(p["baseline_share"] is None for p in out["points"])
