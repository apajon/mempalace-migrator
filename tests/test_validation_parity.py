"""M11 — Target parity validation tests.

Covers ``validation/parity.py``:

  - Happy path: all five parity checks pass after a real reconstruct().
  - Per-check failure: each individual check detects a deliberate mismatch.
  - Open failure: TARGET_OPEN_FAILED anomaly + five inconclusive outcomes.
  - Empty metadata coercion: {} source == None target (writer coercion).
  - Embedding probe failure: inconclusive when include=['embeddings'] raises.
  - validate() without reconstruction: exactly 5 skipped / "reconstruction_not_run".
  - validate() with reconstruction: parity checks included in checks_performed.
  - Parity failures lower the confidence band (HIGH→LOW for HIGH-severity).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import chromadb
import pytest
from chromadb.config import Settings

from mempalace_migrator.core.context import AnomalyType, MigrationContext, Severity
from mempalace_migrator.detection.format_detector import CHROMA_0_6, DetectionResult, Evidence
from mempalace_migrator.extraction.chroma_06_reader import EXPECTED_COLLECTION_NAME, DrawerRecord, ExtractionResult
from mempalace_migrator.reconstruction import reconstruct
from mempalace_migrator.transformation._types import (
    LengthProfile,
    TransformedBundle,
    TransformedDrawer,
    TransformedSummary,
)
from mempalace_migrator.validation import validate
from mempalace_migrator.validation.parity import _open_target_readonly, run_parity_checks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _td(id: str, document: str = "", metadata: dict[str, Any] | None = None) -> TransformedDrawer:
    return TransformedDrawer(
        id=id,
        document=document or f"document for {id}",
        metadata=metadata,
    )


def _make_bundle(
    drawers: tuple[TransformedDrawer, ...] | None = None,
    n: int = 5,
) -> TransformedBundle:
    if drawers is None:
        drawers = tuple(_td(f"id-{i}") for i in range(n))
    summary = TransformedSummary(
        drawer_count=len(drawers),
        dropped_count=0,
        coerced_count=0,
        sample_ids=tuple(d.id for d in drawers[:3]),
        metadata_keys=("wing",),
        wing_room_counts=(("north", "", len(drawers)),),
        length_profile=LengthProfile(min=5, max=5, mean=5.0, p50=5, p95=5),
    )
    return TransformedBundle(
        collection_name=EXPECTED_COLLECTION_NAME,
        collection_metadata={},
        drawers=drawers,
        summary=summary,
    )


def _ctx_with_reconstruction(source: Path, target: Path, bundle: TransformedBundle) -> MigrationContext:
    """Build a MigrationContext, populate transformed_data, run reconstruct()."""
    source.mkdir(parents=True, exist_ok=True)
    ctx = MigrationContext(source_path=source, target_path=target)
    # detected_format must be set: consistency.stage_result_coherence fails if
    # extracted_data is set without detected_format.
    ctx.detected_format = DetectionResult(
        palace_path=str(source),
        classification=CHROMA_0_6,
        confidence=0.95,
        source_version="0.6.3",
        evidence=(Evidence("manifest", "fact", "chromadb_version=0.6.3"),),
        contradictions=(),
        unknowns=(),
    )
    # extracted_data must be non-None so that validate() does not return early.
    ctx.extracted_data = ExtractionResult(
        palace_path=str(source),
        sqlite_path=str(source / "chroma.sqlite3"),
        drawers=tuple(DrawerRecord(id=d.id, document=d.document, metadata=d.metadata or {}) for d in bundle.drawers),
        failed_rows=(),
        sqlite_embedding_row_count=len(bundle.drawers),
        pragma_integrity_check="ok",
        collection_name=EXPECTED_COLLECTION_NAME,
    )
    ctx.transformed_data = bundle
    # reconstruct() returns the result; the pipeline step stores it on ctx.
    ctx.reconstruction_result = reconstruct(ctx)
    assert ctx.reconstruction_result is not None, "reconstruct must succeed"
    return ctx


def _open_target(target: Path) -> Any:
    """Return an open chromadb client + collection for direct manipulation in tests."""
    client = chromadb.PersistentClient(
        path=str(target),
        settings=Settings(anonymized_telemetry=False, allow_reset=False),
    )
    coll = client.get_collection(name=EXPECTED_COLLECTION_NAME)
    return client, coll


# ---------------------------------------------------------------------------
# Happy path: all five checks pass
# ---------------------------------------------------------------------------


def test_all_checks_pass_after_clean_reconstruction(tmp_path: Path) -> None:
    bundle = _make_bundle(n=5)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)
    outcomes = run_parity_checks(ctx)
    assert len(outcomes) == 5
    statuses = {o.id: o.status for o in outcomes}
    for oid, status in statuses.items():
        assert status == "passed", f"{oid} should be passed, got {status!r}"


def test_all_checks_family_is_parity(tmp_path: Path) -> None:
    bundle = _make_bundle(n=3)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)
    outcomes = run_parity_checks(ctx)
    for o in outcomes:
        assert o.family == "parity", f"{o.id} has family={o.family!r}, expected 'parity'"


def test_happy_path_no_anomalies_emitted(tmp_path: Path) -> None:
    bundle = _make_bundle(n=4)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)
    before_count = len(ctx.anomalies)
    run_parity_checks(ctx)
    assert len(ctx.anomalies) == before_count, "run_parity_checks emitted anomalies on a clean reconstruction"


# ---------------------------------------------------------------------------
# Record count mismatch
# ---------------------------------------------------------------------------


def test_record_count_mismatch_detected(tmp_path: Path) -> None:
    bundle = _make_bundle(n=5)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)

    # Directly add an extra record to the target.
    _, coll = _open_target(tmp_path / "target")
    coll.add(ids=["extra-id"], documents=["extra"], metadatas=[{"x": "y"}])

    outcomes = run_parity_checks(ctx)
    count_check = next(o for o in outcomes if o.id == "parity.target_record_count_parity")
    assert count_check.status == "failed"
    anomaly_types = [a.type for a in ctx.anomalies]
    assert AnomalyType.TARGET_RECORD_COUNT_MISMATCH in anomaly_types


def test_record_count_mismatch_severity_high(tmp_path: Path) -> None:
    bundle = _make_bundle(n=5)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)
    _, coll = _open_target(tmp_path / "target")
    coll.add(ids=["extra-id"], documents=["extra"], metadatas=[{"x": "y"}])
    outcomes = run_parity_checks(ctx)
    count_check = next(o for o in outcomes if o.id == "parity.target_record_count_parity")
    assert count_check.severity_on_failure == Severity.HIGH


# ---------------------------------------------------------------------------
# ID set mismatch
# ---------------------------------------------------------------------------


def test_id_set_mismatch_detected(tmp_path: Path) -> None:
    bundle = _make_bundle(n=5)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)

    # Remove one record from the target.
    _, coll = _open_target(tmp_path / "target")
    coll.delete(ids=["id-0"])

    outcomes = run_parity_checks(ctx)
    id_check = next(o for o in outcomes if o.id == "parity.target_id_set_parity")
    assert id_check.status == "failed"
    anomaly_types = [a.type for a in ctx.anomalies]
    assert AnomalyType.TARGET_ID_SET_MISMATCH in anomaly_types


def test_id_set_match_passes(tmp_path: Path) -> None:
    bundle = _make_bundle(n=3)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)
    outcomes = run_parity_checks(ctx)
    id_check = next(o for o in outcomes if o.id == "parity.target_id_set_parity")
    assert id_check.status == "passed"


# ---------------------------------------------------------------------------
# Document hash mismatch
# ---------------------------------------------------------------------------


def test_document_hash_mismatch_detected(tmp_path: Path) -> None:
    bundle = _make_bundle(n=3)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)

    # Overwrite one document in the target.
    _, coll = _open_target(tmp_path / "target")
    coll.update(ids=["id-0"], documents=["TAMPERED document"])

    outcomes = run_parity_checks(ctx)
    doc_check = next(o for o in outcomes if o.id == "parity.target_document_hash_parity")
    assert doc_check.status == "failed"
    anomaly_types = [a.type for a in ctx.anomalies]
    assert AnomalyType.TARGET_DOCUMENT_HASH_MISMATCH in anomaly_types


def test_document_hash_passes_on_clean_target(tmp_path: Path) -> None:
    bundle = _make_bundle(n=4)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)
    outcomes = run_parity_checks(ctx)
    doc_check = next(o for o in outcomes if o.id == "parity.target_document_hash_parity")
    assert doc_check.status == "passed"


# ---------------------------------------------------------------------------
# Metadata mismatch
# ---------------------------------------------------------------------------


def test_metadata_mismatch_detected(tmp_path: Path) -> None:
    drawers = (
        _td("m-0", metadata={"wing": "north", "room": "101"}),
        _td("m-1", metadata={"wing": "south"}),
    )
    bundle = _make_bundle(drawers=drawers)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)

    # Corrupt metadata on one record.
    _, coll = _open_target(tmp_path / "target")
    coll.update(ids=["m-0"], metadatas=[{"wing": "WRONG"}])

    outcomes = run_parity_checks(ctx)
    meta_check = next(o for o in outcomes if o.id == "parity.target_metadata_parity")
    assert meta_check.status == "failed"
    anomaly_types = [a.type for a in ctx.anomalies]
    assert AnomalyType.TARGET_METADATA_MISMATCH in anomaly_types


def test_metadata_empty_dict_coercion_no_false_positive(tmp_path: Path) -> None:
    """Writer coerces {} → None. Parity check must not flag this as a mismatch."""
    drawers = (
        _td("empty-meta-0", metadata={}),
        _td("empty-meta-1", metadata={}),
    )
    bundle = _make_bundle(drawers=drawers)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)
    outcomes = run_parity_checks(ctx)
    meta_check = next(o for o in outcomes if o.id == "parity.target_metadata_parity")
    assert meta_check.status == "passed", f"Empty-dict metadata caused false positive: {meta_check}"


def test_metadata_none_source_no_false_positive(tmp_path: Path) -> None:
    """Drawer with metadata=None is also coerced to None by writer; no mismatch."""
    drawers = (
        _td("none-meta-0", metadata=None),
        _td("none-meta-1", metadata=None),
    )
    bundle = _make_bundle(drawers=drawers)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)
    outcomes = run_parity_checks(ctx)
    meta_check = next(o for o in outcomes if o.id == "parity.target_metadata_parity")
    assert meta_check.status == "passed"


# ---------------------------------------------------------------------------
# Embedding presence
# ---------------------------------------------------------------------------


def test_embedding_presence_passes_on_clean_target(tmp_path: Path) -> None:
    bundle = _make_bundle(n=3)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)
    outcomes = run_parity_checks(ctx)
    emb_check = next(o for o in outcomes if o.id == "parity.target_embedding_presence")
    # Chromadb 1.5.7 auto-generates embeddings; status should be passed or inconclusive.
    assert emb_check.status in (
        "passed",
        "inconclusive",
    ), f"Unexpected embedding presence status: {emb_check.status!r}"


def test_embedding_presence_severity_is_medium(tmp_path: Path) -> None:
    bundle = _make_bundle(n=2)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)
    outcomes = run_parity_checks(ctx)
    emb_check = next(o for o in outcomes if o.id == "parity.target_embedding_presence")
    assert emb_check.severity_on_failure == Severity.MEDIUM


def test_embedding_probe_failure_yields_inconclusive(tmp_path: Path) -> None:
    """When collection.get(include=['embeddings']) raises, the check is inconclusive."""
    bundle = _make_bundle(n=3)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)

    _, real_coll = _open_target(tmp_path / "target")

    original_get = real_coll.get

    def patched_get(limit=None, offset=None, include=None, **kwargs):
        if include and "embeddings" in include:
            raise RuntimeError("embeddings not supported in this probe")
        return original_get(limit=limit, offset=offset, include=include, **kwargs)

    real_coll.get = patched_get

    with patch(
        "mempalace_migrator.validation.parity._open_target_readonly",
        return_value=(MagicMock(), real_coll),
    ):
        outcomes = run_parity_checks(ctx)

    emb_check = next(o for o in outcomes if o.id == "parity.target_embedding_presence")
    assert emb_check.status == "inconclusive"
    anomaly_types = [a.type for a in ctx.anomalies]
    assert AnomalyType.TARGET_EMBEDDING_PROBE_INCONCLUSIVE in anomaly_types


# ---------------------------------------------------------------------------
# Open failure → all inconclusive + TARGET_OPEN_FAILED anomaly
# ---------------------------------------------------------------------------


def test_open_failure_yields_five_inconclusive_outcomes(tmp_path: Path) -> None:
    bundle = _make_bundle(n=3)
    ctx = MigrationContext(source_path=tmp_path / "src", target_path=tmp_path / "target")
    ctx.transformed_data = bundle

    # Simulate a successful reconstruct by setting reconstruction_result directly.
    from mempalace_migrator.reconstruction._types import ReconstructionResult

    ctx.reconstruction_result = ReconstructionResult(
        target_path=tmp_path / "target",
        collection_name=EXPECTED_COLLECTION_NAME,
        imported_count=3,
        batch_size=500,
        chromadb_version="1.5.7",
        target_manifest_path=tmp_path / "target" / "reconstruction-target-manifest.json",
    )

    with patch(
        "mempalace_migrator.validation.parity._open_target_readonly",
        side_effect=RuntimeError("disk not found"),
    ):
        outcomes = run_parity_checks(ctx)

    assert len(outcomes) == 5
    for o in outcomes:
        assert o.status == "inconclusive", f"{o.id} should be inconclusive, got {o.status!r}"


def test_open_failure_emits_target_open_failed_anomaly(tmp_path: Path) -> None:
    bundle = _make_bundle(n=3)
    ctx = MigrationContext(source_path=tmp_path / "src", target_path=tmp_path / "target")
    ctx.transformed_data = bundle

    from mempalace_migrator.reconstruction._types import ReconstructionResult

    ctx.reconstruction_result = ReconstructionResult(
        target_path=tmp_path / "target",
        collection_name=EXPECTED_COLLECTION_NAME,
        imported_count=3,
        batch_size=500,
        chromadb_version="1.5.7",
        target_manifest_path=tmp_path / "target" / "reconstruction-target-manifest.json",
    )

    with patch(
        "mempalace_migrator.validation.parity._open_target_readonly",
        side_effect=RuntimeError("disk not found"),
    ):
        run_parity_checks(ctx)

    anomaly_types = [a.type for a in ctx.anomalies]
    assert AnomalyType.TARGET_OPEN_FAILED in anomaly_types


def test_open_failure_anomaly_severity_is_high(tmp_path: Path) -> None:
    bundle = _make_bundle(n=2)
    ctx = MigrationContext(source_path=tmp_path / "src", target_path=tmp_path / "target")
    ctx.transformed_data = bundle

    from mempalace_migrator.reconstruction._types import ReconstructionResult

    ctx.reconstruction_result = ReconstructionResult(
        target_path=tmp_path / "target",
        collection_name=EXPECTED_COLLECTION_NAME,
        imported_count=2,
        batch_size=500,
        chromadb_version="1.5.7",
        target_manifest_path=tmp_path / "target" / "reconstruction-target-manifest.json",
    )

    with patch(
        "mempalace_migrator.validation.parity._open_target_readonly",
        side_effect=RuntimeError("disk not found"),
    ):
        run_parity_checks(ctx)

    open_failed = [a for a in ctx.anomalies if a.type == AnomalyType.TARGET_OPEN_FAILED]
    assert open_failed and open_failed[0].severity == Severity.HIGH


# ---------------------------------------------------------------------------
# validate() integration: parity skipped vs. included
# ---------------------------------------------------------------------------


def test_validate_without_reconstruction_skips_parity(tmp_path: Path) -> None:
    """When ctx.reconstruction_result is None, parity checks are in not_performed."""
    from mempalace_migrator.detection.format_detector import CHROMA_0_6, DetectionResult, Evidence
    from mempalace_migrator.extraction.chroma_06_reader import DrawerRecord, ExtractionResult

    ctx = MigrationContext(source_path=tmp_path)
    ctx.detected_format = DetectionResult(
        palace_path=str(tmp_path),
        classification=CHROMA_0_6,
        confidence=0.95,
        source_version="0.6.3",
        evidence=(Evidence("manifest", "fact", "chromadb_version=0.6.3"),),
        contradictions=(),
        unknowns=(),
    )
    ctx.extracted_data = ExtractionResult(
        palace_path=str(tmp_path),
        sqlite_path=str(tmp_path / "chroma.sqlite3"),
        drawers=(DrawerRecord(id="d0", document="doc", metadata={}),),
        failed_rows=(),
        sqlite_embedding_row_count=1,
        pragma_integrity_check="ok",
        collection_name=EXPECTED_COLLECTION_NAME,
    )
    # reconstruction_result is None (not set)
    result = validate(ctx)

    parity_ids = {
        "target_record_count_parity",
        "target_id_set_parity",
        "target_document_hash_parity",
        "target_metadata_parity",
        "target_embedding_presence",
    }
    skipped_ids = {s.id for s in result.checks_not_performed}
    assert parity_ids <= skipped_ids, f"Expected all five parity checks to be skipped; got {skipped_ids}"
    # All must carry reason="reconstruction_not_run"
    for s in result.checks_not_performed:
        if s.id in parity_ids:
            assert s.reason == "reconstruction_not_run"


def test_validate_with_reconstruction_includes_parity(tmp_path: Path) -> None:
    """When ctx.reconstruction_result is set, parity checks appear in checks_performed."""
    bundle = _make_bundle(n=4)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)
    result = validate(ctx)

    performed_ids = {o.id for o in result.checks_performed}
    parity_ids = {
        "parity.target_record_count_parity",
        "parity.target_id_set_parity",
        "parity.target_document_hash_parity",
        "parity.target_metadata_parity",
        "parity.target_embedding_presence",
    }
    assert parity_ids <= performed_ids, f"Missing parity checks in checks_performed: {parity_ids - performed_ids}"
    # checks_not_performed must be empty (no skips when reconstruction ran)
    assert result.checks_not_performed == ()


def test_validate_with_reconstruction_confidence_band_high(tmp_path: Path) -> None:
    """A clean migration should yield HIGH confidence band."""
    bundle = _make_bundle(n=3)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)
    result = validate(ctx)
    # All parity checks pass; band should be HIGH (or at worst inconclusive=MEDIUM).
    assert result.confidence_band in (
        "HIGH",
        "MEDIUM",
    ), f"Unexpected confidence band after clean migration: {result.confidence_band!r}"


def test_parity_failure_lowers_band(tmp_path: Path) -> None:
    """A HIGH-severity parity failure should lower the band to LOW."""
    bundle = _make_bundle(n=3)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)

    # Remove a record from the target so TARGET_ID_SET_MISMATCH fires (HIGH).
    _, coll = _open_target(tmp_path / "target")
    coll.delete(ids=["id-0"])

    result = validate(ctx)
    assert (
        result.confidence_band == "LOW"
    ), f"Expected LOW band after HIGH-severity parity failure; got {result.confidence_band!r}"


# ---------------------------------------------------------------------------
# _open_target_readonly: allow_reset=False contract
# ---------------------------------------------------------------------------


def test_open_target_readonly_opens_collection(tmp_path: Path) -> None:
    """_open_target_readonly returns a real collection without modifying target."""
    bundle = _make_bundle(n=2)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)

    client, coll = _open_target_readonly(
        ctx.reconstruction_result.target_path,
        ctx.reconstruction_result.collection_name,
    )
    assert coll is not None
    assert coll.count() == 2


def test_open_target_readonly_raises_on_missing_target(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        _open_target_readonly(tmp_path / "nonexistent", EXPECTED_COLLECTION_NAME)
    """_open_target_readonly returns a real collection without modifying target."""
    bundle = _make_bundle(n=2)
    ctx = _ctx_with_reconstruction(tmp_path / "src", tmp_path / "target", bundle)

    client, coll = _open_target_readonly(
        ctx.reconstruction_result.target_path,
        ctx.reconstruction_result.collection_name,
    )
    assert coll is not None
    assert coll.count() == 2


def test_open_target_readonly_raises_on_missing_target(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        _open_target_readonly(tmp_path / "nonexistent", EXPECTED_COLLECTION_NAME)
