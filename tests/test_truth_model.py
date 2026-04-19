"""Tests for the M3 Truth Model: structured anomaly representation.

These tests verify the structural invariants of the anomaly system —
they do NOT test what extraction or pipeline emit (that is covered by
the per-stage tests). The goal is to prove that:

  * AnomalyType is a closed enum (free-form types are rejected).
  * Severity is a closed enum (invalid severities are rejected).
  * Every Anomaly has a non-empty stage via AnomalyLocation.
  * Every Anomaly has at least one AnomalyEvidence entry.
  * Anomalies serialise to a stable, machine-readable shape.
  * The legacy add_anomaly shape is still accepted but still produces
    structurally-valid anomalies.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mempalace_migrator.core.context import (
    SEVERITIES,
    Anomaly,
    AnomalyEvidence,
    AnomalyLocation,
    AnomalyType,
    MigrationContext,
    Severity,
)


def _ctx(tmp_path: Path) -> MigrationContext:
    return MigrationContext(source_path=tmp_path)


# --- Enums ----------------------------------------------------------------


def test_severity_enum_has_four_levels():
    assert {s.value for s in Severity} == {"low", "medium", "high", "critical"}
    # str-Enum subclassing keeps backward-compat string equality
    assert Severity.HIGH == "high"
    assert SEVERITIES == ("low", "medium", "high", "critical")


def test_anomaly_type_is_closed_registry():
    # Sample of registered types (proves the enum exists and contains them).
    assert AnomalyType.BLANK_EMBEDDING_ID.value == "blank_embedding_id"
    assert AnomalyType.UNSUPPORTED_VERSION.value == "unsupported_version"
    assert AnomalyType.NOT_IMPLEMENTED.value == "not_implemented"


def test_unknown_anomaly_type_string_is_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError, match="unknown anomaly type"):
        ctx.add_anomaly(
            type="totally_made_up_type",
            severity=Severity.LOW,
            message="should not be accepted",
            stage="extract",
        )


def test_invalid_severity_is_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError, match="invalid severity"):
        ctx.add_anomaly(
            type=AnomalyType.BLANK_EMBEDDING_ID,
            severity="catastrophic",
            message="bad severity",
            stage="extract",
        )


# --- Location -------------------------------------------------------------


def test_location_requires_non_empty_stage():
    with pytest.raises(ValueError, match="stage must be a non-empty string"):
        AnomalyLocation(stage="")
    with pytest.raises(ValueError, match="stage must be a non-empty string"):
        AnomalyLocation(stage="   ")


def test_add_anomaly_requires_location_or_stage(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError, match="requires either `location=` or `stage=`"):
        ctx.add_anomaly(
            type=AnomalyType.NOT_IMPLEMENTED,
            severity=Severity.LOW,
            message="missing both",
        )


def test_add_anomaly_rejects_inconsistent_stage_and_location(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError, match="disagrees with location.stage"):
        ctx.add_anomaly(
            type=AnomalyType.NOT_IMPLEMENTED,
            severity=Severity.LOW,
            message="conflict",
            stage="extract",
            location=AnomalyLocation(stage="detect"),
        )


def test_anomaly_stage_property_delegates_to_location(tmp_path):
    ctx = _ctx(tmp_path)
    a = ctx.add_anomaly(
        type=AnomalyType.NOT_IMPLEMENTED,
        severity=Severity.LOW,
        message="stub",
        location=AnomalyLocation(stage="reconstruct"),
        evidence=AnomalyEvidence(kind="observation", detail="x"),
    )
    assert a.stage == "reconstruct"
    assert a.location.stage == "reconstruct"


# --- Evidence -------------------------------------------------------------


def test_evidence_requires_non_empty_kind():
    with pytest.raises(ValueError, match="kind must be a non-empty string"):
        AnomalyEvidence(kind="", detail="x")


def test_anomaly_requires_at_least_one_evidence_entry():
    loc = AnomalyLocation(stage="extract")
    with pytest.raises(ValueError, match="at least one entry"):
        Anomaly(
            type=AnomalyType.NOT_IMPLEMENTED,
            severity=Severity.LOW,
            message="x",
            location=loc,
            evidence=(),
        )


def test_legacy_call_synthesises_evidence(tmp_path):
    """Call sites that have not migrated yet still produce a valid anomaly."""
    ctx = _ctx(tmp_path)
    a = ctx.add_anomaly(
        type=AnomalyType.NOT_IMPLEMENTED,
        severity=Severity.LOW,
        message="legacy",
        stage="transform",
        context={"foo": 1},
    )
    assert len(a.evidence) == 1
    assert a.evidence[0].kind == "legacy_context"
    assert a.evidence[0].data == {"foo": 1}
    # And the structured location is built from `stage`.
    assert a.location.stage == "transform"
    assert a.location.extra == {"foo": 1}


def test_structured_evidence_is_preserved(tmp_path):
    ctx = _ctx(tmp_path)
    a = ctx.add_anomaly(
        type=AnomalyType.DUPLICATE_EMBEDDING_IDS,
        severity=Severity.HIGH,
        message="duplicates",
        location=AnomalyLocation(stage="extract", source="embeddings"),
        evidence=[
            AnomalyEvidence(kind="sample", detail="2 dups", data={"sample": ["a", "b"]}),
            AnomalyEvidence(kind="count", detail="count=2", data={"count": 2}),
        ],
    )
    assert len(a.evidence) == 2
    assert a.evidence[0].kind == "sample"
    assert a.evidence[1].data == {"count": 2}


# --- Serialisation --------------------------------------------------------


def test_anomaly_to_dict_shape(tmp_path):
    ctx = _ctx(tmp_path)
    a = ctx.add_anomaly(
        type=AnomalyType.BLANK_EMBEDDING_ID,
        severity=Severity.HIGH,
        message="row pk=7 has blank id",
        location=AnomalyLocation(
            stage="extract",
            source="embeddings",
            record_pk=7,
        ),
        evidence=[
            AnomalyEvidence(
                kind="observation",
                detail="embedding_id is NULL or blank",
                data={"embedding_pk": 7},
            ),
        ],
    )
    d = a.to_dict()
    assert d["type"] == "blank_embedding_id"
    assert d["severity"] == "high"
    assert d["stage"] == "extract"
    assert d["message"] == "row pk=7 has blank id"
    assert d["location"] == {
        "stage": "extract",
        "source": "embeddings",
        "identifier": None,
        "record_pk": 7,
        "path": None,
        "extra": {},
    }
    assert d["evidence"] == [
        {
            "kind": "observation",
            "detail": "embedding_id is NULL or blank",
            "data": {"embedding_pk": 7},
        }
    ]


def test_context_property_merges_location_and_evidence(tmp_path):
    """The `context` view exists for legacy consumers and must merge both."""
    ctx = _ctx(tmp_path)
    a = ctx.add_anomaly(
        type=AnomalyType.METADATA_QUERY_FAILED,
        severity=Severity.HIGH,
        message="x",
        location=AnomalyLocation(
            stage="extract",
            source="embedding_metadata",
            identifier="row-7",
            record_pk=7,
        ),
        evidence=[
            AnomalyEvidence(
                kind="sqlite_error",
                detail="boom",
                data={"error": "DatabaseError(...)"},
            ),
        ],
    )
    ctx_view = a.context
    assert ctx_view["embedding_id"] == "row-7"
    assert ctx_view["embedding_pk"] == 7
    assert ctx_view["source"] == "embedding_metadata"
    assert ctx_view["error"] == "DatabaseError(...)"


# --- Wiring sanity -------------------------------------------------------


def test_every_emitted_extraction_anomaly_is_structurally_valid(tmp_path):
    """Smoke test: simulate emission by calling add_anomaly with each
    extraction-relevant type and verify all structural invariants hold."""
    ctx = _ctx(tmp_path)
    for at in [
        AnomalyType.BLANK_EMBEDDING_ID,
        AnomalyType.CONTROL_CHARS_IN_ID,
        AnomalyType.DUPLICATE_EMBEDDING_IDS,
        AnomalyType.METADATA_QUERY_FAILED,
        AnomalyType.ORPHAN_EMBEDDING,
        AnomalyType.DOCUMENT_STRING_VALUE_NULL,
        AnomalyType.METADATA_ALL_NULL,
        AnomalyType.DOCUMENT_MISSING,
        AnomalyType.DOCUMENT_MULTIPLE,
        AnomalyType.DUPLICATE_METADATA_KEYS,
        AnomalyType.EMBEDDINGS_SCAN_ABORTED,
        AnomalyType.EXTRACTION_ARITHMETIC_MISMATCH,
    ]:
        ctx.add_anomaly(
            type=at,
            severity=Severity.HIGH,
            message=f"sample {at.value}",
            location=AnomalyLocation(stage="extract"),
            evidence=AnomalyEvidence(kind="observation", detail="sample"),
        )

    for a in ctx.anomalies:
        assert isinstance(a.type, AnomalyType)
        assert isinstance(a.severity, Severity)
        assert a.location.stage == "extract"
        assert len(a.evidence) >= 1
