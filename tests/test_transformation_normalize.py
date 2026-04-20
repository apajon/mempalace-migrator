"""Tests for transformation/_normalize.py — pure metadata normalisation.

Covers every row in M9 design §5 normalisation table:
  - Pass-through types (str, int, float, bool)
  - bool/int discrimination
  - int out of range → coerce to str (MEDIUM path)
  - float NaN / ±Inf → drop reason non_finite_float
  - None → drop reason metadata_value_none
  - list / tuple / dict / set → drop reason unsupported_metadata_value_type
  - non-str key → drop reason non_string_metadata_key
  - empty dict input → accepted with empty output
"""

from __future__ import annotations

import math

import pytest

from mempalace_migrator.transformation._normalize import normalize_metadata

# ---------------------------------------------------------------------------
# Pass-through cases
# ---------------------------------------------------------------------------


def test_string_value_passes_through():
    out, reason, coercions = normalize_metadata({"key": "hello"})
    assert reason is None
    assert out == {"key": "hello"}
    assert coercions == []


def test_int_value_in_range_passes_through():
    out, reason, coercions = normalize_metadata({"key": 42})
    assert reason is None
    assert out == {"key": 42}
    assert coercions == []


def test_float_value_passes_through():
    out, reason, coercions = normalize_metadata({"key": 3.14})
    assert reason is None
    assert out == {"key": 3.14}
    assert coercions == []


def test_bool_true_passes_through():
    out, reason, coercions = normalize_metadata({"key": True})
    assert reason is None
    assert out == {"key": True}
    assert isinstance(out["key"], bool)
    assert coercions == []


def test_bool_false_passes_through():
    out, reason, coercions = normalize_metadata({"key": False})
    assert reason is None
    assert out == {"key": False}
    assert isinstance(out["key"], bool)
    assert coercions == []


def test_empty_dict_accepted():
    out, reason, coercions = normalize_metadata({})
    assert reason is None
    assert out == {}
    assert coercions == []


def test_multiple_valid_keys():
    raw = {"a": "x", "b": 1, "c": 2.5, "d": True}
    out, reason, _ = normalize_metadata(raw)
    assert reason is None
    assert out == raw


# ---------------------------------------------------------------------------
# bool/int discrimination
# ---------------------------------------------------------------------------


def test_bool_not_treated_as_int():
    """True == 1 in Python, but we must preserve bool type, not coerce."""
    out, reason, coercions = normalize_metadata({"key": True})
    assert reason is None
    assert type(out["key"]) is bool, "bool must remain bool, not be treated as int"
    assert coercions == []


# ---------------------------------------------------------------------------
# int out of range → coercion (MEDIUM path)
# ---------------------------------------------------------------------------


def test_int_at_int64_max_passes():
    val = (2**63) - 1
    out, reason, coercions = normalize_metadata({"key": val})
    assert reason is None
    assert out["key"] == val
    assert coercions == []


def test_int_at_int64_min_passes():
    val = -(2**63)
    out, reason, coercions = normalize_metadata({"key": val})
    assert reason is None
    assert out["key"] == val
    assert coercions == []


def test_int_above_range_coerced():
    val = 2**63
    out, reason, coercions = normalize_metadata({"key": val})
    assert reason is None, "oversized int should coerce, not drop"
    assert out["key"] == str(val)
    assert len(coercions) == 1
    c = coercions[0]
    assert c["key"] == "key"
    assert c["reason"] == "int_out_of_range"
    assert c["new_value"] == str(val)


def test_int_below_range_coerced():
    val = -(2**63) - 1
    out, reason, coercions = normalize_metadata({"key": val})
    assert reason is None
    assert out["key"] == str(val)
    assert len(coercions) == 1
    assert coercions[0]["reason"] == "int_out_of_range"


def test_multiple_oversized_ints_each_coerced():
    """Two keys both out-of-range → two coercion records."""
    big = 2**64
    out, reason, coercions = normalize_metadata({"a": big, "b": -big})
    assert reason is None
    assert len(coercions) == 2
    assert {c["key"] for c in coercions} == {"a", "b"}


# ---------------------------------------------------------------------------
# float NaN / ±Inf → drop
# ---------------------------------------------------------------------------


def test_float_nan_drops():
    _, reason, _ = normalize_metadata({"key": float("nan")})
    assert reason == "non_finite_float"


def test_float_positive_inf_drops():
    _, reason, _ = normalize_metadata({"key": float("inf")})
    assert reason == "non_finite_float"


def test_float_negative_inf_drops():
    _, reason, _ = normalize_metadata({"key": float("-inf")})
    assert reason == "non_finite_float"


def test_float_drop_returns_empty_dict():
    out, reason, _ = normalize_metadata({"key": float("nan")})
    assert reason == "non_finite_float"
    assert out == {}


# ---------------------------------------------------------------------------
# None → drop
# ---------------------------------------------------------------------------


def test_none_value_drops():
    _, reason, _ = normalize_metadata({"key": None})
    assert reason == "metadata_value_none"


def test_none_value_drop_stops_at_first():
    """First bad value triggers drop; no coercion records produced."""
    _, reason, coercions = normalize_metadata({"a": None, "b": 2**64})
    assert reason == "metadata_value_none"
    assert coercions == []


# ---------------------------------------------------------------------------
# Unsupported collection types → drop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    [
        [],
        [1, 2],
        (),
        (1, 2),
        {},
        {"x": 1},
        {1, 2},
        object(),
        b"bytes",
        bytearray(b"ba"),
    ],
)
def test_collection_type_drops(bad_value):
    _, reason, _ = normalize_metadata({"key": bad_value})
    assert reason == "unsupported_metadata_value_type"


# ---------------------------------------------------------------------------
# Non-string key → drop
# ---------------------------------------------------------------------------


def test_integer_key_drops():
    _, reason, _ = normalize_metadata({1: "value"})
    assert reason == "non_string_metadata_key"


def test_none_key_drops():
    _, reason, _ = normalize_metadata({None: "value"})
    assert reason == "non_string_metadata_key"


def test_mixed_keys_first_non_str_drops():
    """The first non-string key encountered triggers a drop (dict ordering)."""
    # Python dicts preserve insertion order since 3.7.
    raw = {"good_key": "v", 99: "bad"}
    _, reason, _ = normalize_metadata(raw)
    assert reason == "non_string_metadata_key"
