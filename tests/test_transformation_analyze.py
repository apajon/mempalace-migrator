"""Tests for transformation/_analyze.py — build_summary.

Covers:
  - sample_ids capped at 20 and sorted
  - metadata_keys union across all drawers, sorted
  - wing_room_counts grouping and sorting
  - length_profile math (min/max/mean/p50/p95) on various input sizes
  - Empty drawer list → all-zero profile
"""

from __future__ import annotations

import pytest

from mempalace_migrator.transformation._analyze import build_summary
from mempalace_migrator.transformation._types import TransformedDrawer


def _drawer(id: str, doc: str = "x", meta: dict | None = None) -> TransformedDrawer:
    return TransformedDrawer(id=id, document=doc, metadata=meta or {})


# ---------------------------------------------------------------------------
# sample_ids
# ---------------------------------------------------------------------------


def test_sample_ids_sorted():
    drawers = [_drawer("b"), _drawer("a"), _drawer("c")]
    s = build_summary(drawers, dropped_count=0, coerced_count=0)
    assert s.sample_ids == ("a", "b", "c")


def test_sample_ids_capped_at_20():
    drawers = [_drawer(f"id_{i:03d}") for i in range(30)]
    s = build_summary(drawers, dropped_count=0, coerced_count=0)
    assert len(s.sample_ids) == 20
    assert s.sample_ids == tuple(sorted(d.id for d in drawers)[:20])


def test_sample_ids_empty():
    s = build_summary([], dropped_count=0, coerced_count=0)
    assert s.sample_ids == ()


# ---------------------------------------------------------------------------
# metadata_keys
# ---------------------------------------------------------------------------


def test_metadata_keys_union_sorted():
    drawers = [
        _drawer("a", meta={"z": 1, "b": 2}),
        _drawer("b", meta={"a": "x"}),
        _drawer("c", meta={}),
    ]
    s = build_summary(drawers, dropped_count=0, coerced_count=0)
    assert s.metadata_keys == ("a", "b", "z")


def test_metadata_keys_no_duplicates():
    drawers = [_drawer("a", meta={"key": 1}), _drawer("b", meta={"key": 2})]
    s = build_summary(drawers, dropped_count=0, coerced_count=0)
    assert s.metadata_keys == ("key",)


def test_metadata_keys_empty_drawers():
    s = build_summary([], dropped_count=0, coerced_count=0)
    assert s.metadata_keys == ()


# ---------------------------------------------------------------------------
# wing_room_counts
# ---------------------------------------------------------------------------


def test_wing_room_counts_groups_correctly():
    drawers = [
        _drawer("1", meta={"wing": "A", "room": "r1"}),
        _drawer("2", meta={"wing": "A", "room": "r1"}),
        _drawer("3", meta={"wing": "A", "room": "r2"}),
        _drawer("4", meta={"wing": "B", "room": "r1"}),
        _drawer("5", meta={}),  # no wing/room → not counted
    ]
    s = build_summary(drawers, dropped_count=0, coerced_count=0)
    wrc = s.wing_room_counts
    assert ("A", "r1", 2) in wrc
    assert ("A", "r2", 1) in wrc
    assert ("B", "r1", 1) in wrc
    assert len(wrc) == 3


def test_wing_room_counts_sorted():
    drawers = [
        _drawer("1", meta={"wing": "Z", "room": "r"}),
        _drawer("2", meta={"wing": "A", "room": "r"}),
    ]
    s = build_summary(drawers, dropped_count=0, coerced_count=0)
    assert s.wing_room_counts[0][0] == "A"
    assert s.wing_room_counts[1][0] == "Z"


def test_wing_room_counts_non_string_ignored():
    """wing or room that is not a str should not appear in wing_room_counts."""
    drawers = [
        _drawer("1", meta={"wing": 1, "room": "r"}),  # int wing
        _drawer("2", meta={"wing": "A", "room": None}),  # None room — already filtered
    ]
    s = build_summary(drawers, dropped_count=0, coerced_count=0)
    assert s.wing_room_counts == ()


# ---------------------------------------------------------------------------
# length_profile
# ---------------------------------------------------------------------------


def test_length_profile_empty():
    s = build_summary([], dropped_count=0, coerced_count=0)
    lp = s.length_profile
    assert lp.min == 0
    assert lp.max == 0
    assert lp.mean == 0.0
    assert lp.p50 == 0
    assert lp.p95 == 0


def test_length_profile_single_drawer():
    s = build_summary([_drawer("a", doc="hello")], dropped_count=0, coerced_count=0)
    lp = s.length_profile
    assert lp.min == 5
    assert lp.max == 5
    assert lp.mean == 5.0
    assert lp.p50 == 5
    assert lp.p95 == 5


def test_length_profile_mean_rounded():
    # docs: len 1, 2, 3 → mean = 2.0 (exact)
    drawers = [_drawer("a", doc="x"), _drawer("b", doc="xx"), _drawer("c", doc="xxx")]
    s = build_summary(drawers, dropped_count=0, coerced_count=0)
    assert s.length_profile.mean == 2.0


def test_length_profile_p50_p95():
    # 10 drawers, lengths 1..10
    drawers = [_drawer(str(i), doc="x" * i) for i in range(1, 11)]
    s = build_summary(drawers, dropped_count=0, coerced_count=0)
    lp = s.length_profile
    # p50: ceil(50/100 * 10) - 1 = ceil(5) - 1 = 4 → sorted[4] = 5
    assert lp.p50 == 5
    # p95: ceil(95/100 * 10) - 1 = ceil(9.5) - 1 = 9 → sorted[9] = 10
    assert lp.p95 == 10


def test_length_profile_uses_codepoints_not_bytes():
    """Length is measured in Unicode code points, not bytes."""
    # U+1F600 (emoji) is 1 code point but 4 bytes in UTF-8.
    emoji_doc = "\U0001f600" * 3  # 3 code points, 12 bytes
    s = build_summary([_drawer("a", doc=emoji_doc)], dropped_count=0, coerced_count=0)
    assert s.length_profile.min == 3


# ---------------------------------------------------------------------------
# summary fields pass-through
# ---------------------------------------------------------------------------


def test_dropped_and_coerced_counts_stored():
    s = build_summary([], dropped_count=7, coerced_count=2)
    assert s.dropped_count == 7
    assert s.coerced_count == 2


def test_drawer_count_matches_input():
    drawers = [_drawer("a"), _drawer("b")]
    s = build_summary(drawers, dropped_count=0, coerced_count=0)
    assert s.drawer_count == 2
