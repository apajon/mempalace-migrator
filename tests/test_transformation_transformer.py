"""Tests for transformation/transformer.py — transform(ctx) entry point.

Covers:
  - Happy path: N valid drawers in → N drawers out, zero anomalies
  - Per-drop-reason matrix (one drawer per reason, exactly one anomaly each)
  - Duplicate id: second occurrence dropped with TRANSFORM_DUPLICATE_ID_DROPPED
  - Coercion path: oversized int survives, TRANSFORM_METADATA_COERCED/MEDIUM emitted
  - All-drop: every drawer invalid → bundle with drawer_count==0, no raise
  - Missing input: ctx.extracted_data is None → TransformError raised
  - Determinism: output order follows input order
  - stage 'transform' is set on every emitted anomaly
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from mempalace_migrator.core.context import AnomalyType, MigrationContext, Severity
from mempalace_migrator.core.errors import TransformError
from mempalace_migrator.extraction.chroma_06_reader import DrawerRecord, ExtractionResult, FailedRow
from mempalace_migrator.transformation import TransformedBundle, transform

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(drawers: tuple[DrawerRecord, ...] | None = None) -> MigrationContext:
    ctx = MigrationContext(source_path=Path("/fake/palace"))
    if drawers is not None:
        ctx.extracted_data = ExtractionResult(
            palace_path="/fake/palace",
            sqlite_path="/fake/palace/chroma.sqlite3",
            drawers=drawers,
            failed_rows=(),
            sqlite_embedding_row_count=len(drawers),
            pragma_integrity_check="ok",
            collection_name="mempalace_drawers",
        )
    return ctx


def _dr(id: str, doc: str = "hello", meta: dict | None = None) -> DrawerRecord:
    return DrawerRecord(id=id, document=doc, metadata=meta or {})


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_all_drawers_pass():
    drawers = (_dr("a"), _dr("b"), _dr("c"))
    ctx = _make_ctx(drawers)
    bundle = transform(ctx)
    assert isinstance(bundle, TransformedBundle)
    assert bundle.summary.drawer_count == 3
    assert bundle.summary.dropped_count == 0
    assert bundle.summary.coerced_count == 0
    assert len(bundle.drawers) == 3
    transform_anomalies = [a for a in ctx.anomalies if a.stage == "transform"]
    assert transform_anomalies == []


def test_happy_path_metadata_preserved():
    meta = {"wing": "north", "room": "101", "priority": 5, "active": True}
    ctx = _make_ctx((_dr("id1", meta=meta),))
    bundle = transform(ctx)
    assert bundle.drawers[0].metadata == meta


def test_happy_path_stores_on_ctx():
    ctx = _make_ctx((_dr("a"),))
    bundle = transform(ctx)
    assert ctx.transformed_data is bundle


# ---------------------------------------------------------------------------
# Missing input → TransformError
# ---------------------------------------------------------------------------


def test_missing_extracted_data_raises_transform_error():
    ctx = _make_ctx(None)
    with pytest.raises(TransformError) as exc_info:
        transform(ctx)
    assert exc_info.value.stage == "transform"
    assert exc_info.value.code == "transform_input_missing"


def test_missing_extracted_data_emits_critical_anomaly():
    ctx = _make_ctx(None)
    with pytest.raises(TransformError):
        transform(ctx)
    critical = [a for a in ctx.anomalies if a.severity == Severity.CRITICAL and a.stage == "transform"]
    assert len(critical) >= 1


# ---------------------------------------------------------------------------
# Per-drop-reason matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "drawer,expected_reason_fragment",
    [
        # blank id
        (_dr("", doc="doc"), "invalid_id"),
        # control char in id
        (_dr("bad\x01id", doc="doc"), "invalid_id"),
        # empty document
        (_dr("good-id", doc=""), "invalid_document"),
        # non-string document (raw dict — shouldn't happen after extraction, but defensive)
        (DrawerRecord(id="good-id", document=None, metadata={}), "invalid_document"),  # type: ignore[arg-type]
        # None metadata value
        (_dr("good-id", doc="doc", meta={"key": None}), "metadata_value_none"),
        # list metadata value
        (_dr("good-id", doc="doc", meta={"key": [1, 2]}), "unsupported_metadata_value_type"),
        # dict metadata value
        (_dr("good-id", doc="doc", meta={"key": {"x": 1}}), "unsupported_metadata_value_type"),
        # non-string metadata key
        (DrawerRecord(id="good-id", document="doc", metadata={1: "val"}), "non_string_metadata_key"),  # type: ignore[arg-type]
        # NaN float
        (_dr("good-id", doc="doc", meta={"key": float("nan")}), "non_finite_float"),
        # +Inf float
        (_dr("good-id", doc="doc", meta={"key": float("inf")}), "non_finite_float"),
    ],
)
def test_drop_reason(drawer, expected_reason_fragment):
    ctx = _make_ctx((drawer,))
    bundle = transform(ctx)
    assert bundle.summary.drawer_count == 0
    assert bundle.summary.dropped_count == 1
    drop_anomalies = [
        a for a in ctx.anomalies if a.type == AnomalyType.TRANSFORM_DRAWER_DROPPED and a.stage == "transform"
    ]
    assert len(drop_anomalies) == 1
    reason = drop_anomalies[0].evidence[0].data.get("reason", "")
    assert expected_reason_fragment in reason


def test_drop_emits_exactly_one_anomaly_even_with_multiple_failures():
    """A drawer failing both id and document only produces one anomaly (first-failure-wins)."""
    d = DrawerRecord(id="", document="", metadata={})
    ctx = _make_ctx((d,))
    bundle = transform(ctx)
    drop_anomalies = [a for a in ctx.anomalies if a.type == AnomalyType.TRANSFORM_DRAWER_DROPPED]
    assert len(drop_anomalies) == 1


# ---------------------------------------------------------------------------
# Duplicate id
# ---------------------------------------------------------------------------


def test_duplicate_id_second_occurrence_dropped():
    drawers = (_dr("same", doc="first"), _dr("same", doc="second"))
    ctx = _make_ctx(drawers)
    bundle = transform(ctx)
    assert bundle.summary.drawer_count == 1
    assert bundle.summary.dropped_count == 1
    dup_anomalies = [a for a in ctx.anomalies if a.type == AnomalyType.TRANSFORM_DUPLICATE_ID_DROPPED]
    assert len(dup_anomalies) == 1
    assert dup_anomalies[0].severity == Severity.HIGH
    assert dup_anomalies[0].stage == "transform"


def test_duplicate_id_first_occurrence_kept():
    drawers = (_dr("same", doc="first doc"), _dr("same", doc="second doc"))
    ctx = _make_ctx(drawers)
    bundle = transform(ctx)
    assert bundle.drawers[0].document == "first doc"


# ---------------------------------------------------------------------------
# Coercion path (int out of range → MEDIUM, no drop)
# ---------------------------------------------------------------------------


def test_oversized_int_coercion_emits_medium_anomaly():
    big = 2**63
    ctx = _make_ctx((_dr("id1", meta={"big": big}),))
    bundle = transform(ctx)
    assert bundle.summary.drawer_count == 1
    assert bundle.summary.dropped_count == 0
    assert bundle.summary.coerced_count == 1
    coerce_anomalies = [a for a in ctx.anomalies if a.type == AnomalyType.TRANSFORM_METADATA_COERCED]
    assert len(coerce_anomalies) == 1
    assert coerce_anomalies[0].severity == Severity.MEDIUM


def test_oversized_int_coerced_value_is_string():
    big = 2**63
    ctx = _make_ctx((_dr("id1", meta={"big": big}),))
    bundle = transform(ctx)
    assert bundle.drawers[0].metadata["big"] == str(big)


def test_coercion_evidence_has_key_and_original_repr():
    big = 2**63
    ctx = _make_ctx((_dr("id1", meta={"big": big}),))
    transform(ctx)
    a = next(a for a in ctx.anomalies if a.type == AnomalyType.TRANSFORM_METADATA_COERCED)
    data = a.evidence[0].data
    assert data["key"] == "big"
    assert "original_repr" in data
    assert data["reason"] == "int_out_of_range"


# ---------------------------------------------------------------------------
# All-drop: zero-drawer bundle is not a raise
# ---------------------------------------------------------------------------


def test_all_drawers_dropped_returns_empty_bundle():
    drawers = (_dr("", doc=""), _dr("", doc=""))  # all blank ids
    ctx = _make_ctx(drawers)
    bundle = transform(ctx)  # must NOT raise
    assert bundle.summary.drawer_count == 0
    assert bundle.summary.dropped_count == 2


# ---------------------------------------------------------------------------
# Determinism: output order follows input order
# ---------------------------------------------------------------------------


def test_output_order_follows_input_order():
    ids = [f"id_{i:03d}" for i in range(20)]
    drawers = tuple(_dr(i) for i in ids)
    ctx = _make_ctx(drawers)
    bundle = transform(ctx)
    assert [d.id for d in bundle.drawers] == ids


# ---------------------------------------------------------------------------
# Stage attribution
# ---------------------------------------------------------------------------


def test_all_anomalies_have_transform_stage():
    """Every anomaly emitted by transform() must carry stage='transform'."""
    drawers = (
        _dr("", doc="drop_blank_id"),
        _dr("dup"),
        _dr("dup"),
        _dr("ok", meta={"big": 2**64}),
    )
    ctx = _make_ctx(drawers)
    transform(ctx)
    for a in ctx.anomalies:
        assert a.stage == "transform", f"unexpected stage on anomaly {a.type}: {a.stage!r}"


# ---------------------------------------------------------------------------
# Bundle collection_name
# ---------------------------------------------------------------------------


def test_bundle_collection_name_is_expected():
    from mempalace_migrator.extraction.chroma_06_reader import EXPECTED_COLLECTION_NAME

    ctx = _make_ctx((_dr("a"),))
    bundle = transform(ctx)
    assert bundle.collection_name == EXPECTED_COLLECTION_NAME


def test_bundle_collection_metadata_is_empty_dict():
    ctx = _make_ctx((_dr("a"),))
    bundle = transform(ctx)
    assert bundle.collection_metadata == {}
    ctx = _make_ctx((_dr("a"),))
    bundle = transform(ctx)
    assert bundle.collection_metadata == {}
