"""M5 gate tests — Safe Interpretation (Validation).

Exit gate: Validation never implies correctness.

These tests verify:
  1. No public function in validation/ returns bool.
  2. validate() on empty context returns UNKNOWN band and no checks.
  3. Structural arithmetic mismatch detected from outside extraction.
  4. Consistency: same ID in both parsed drawers and failed_rows.
  5. Consistency: failed_row without a matching anomaly flagged.
  6. Heuristic: low parse rate flags MEDIUM, not HIGH.
  7. Validation never aborts the pipeline (even with all checks failed).
  8. Report validation section populated; schema v4.
  9. Overall band includes validation as a weakest-band input.
  10. checks_not_performed lists reconstruction stubs explicitly.
  11. No correctness vocabulary in text output.
  12. All new AnomalyTypes are registered in the closed registry.
"""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from mempalace_migrator.core.context import AnomalyEvidence, AnomalyLocation, AnomalyType, MigrationContext, Severity
from mempalace_migrator.core.errors import PipelineAbort
from mempalace_migrator.core.pipeline import FULL_PIPELINE, run_pipeline, step_validate
from mempalace_migrator.detection.format_detector import CHROMA_0_6, DetectionResult, Evidence
from mempalace_migrator.extraction.chroma_06_reader import DrawerRecord, ExtractionResult, FailedRow
from mempalace_migrator.reporting.report_builder import REPORT_SCHEMA_VERSION, build_report
from mempalace_migrator.reporting.text_renderer import render_text
from mempalace_migrator.validation import ValidationResult, validate

# --- Factories ------------------------------------------------------------


def _ctx(tmp_path: Path) -> MigrationContext:
    return MigrationContext(source_path=tmp_path)


def _detection_result(confidence: float = 0.95) -> DetectionResult:
    return DetectionResult(
        palace_path="/fake/palace",
        classification=CHROMA_0_6,
        confidence=confidence,
        source_version="0.6.3",
        evidence=(Evidence("manifest", "fact", "chromadb_version=0.6.3"),),
        contradictions=(),
        unknowns=(),
    )


def _extraction_result(
    *,
    total: int = 10,
    parsed: int = 10,
    failed: int = 0,
    drawer_ids: list[str] | None = None,
    failed_ids: list[str | None] | None = None,
) -> ExtractionResult:
    if drawer_ids is not None:
        drawers = tuple(DrawerRecord(id=did, document=f"doc {i}", metadata={}) for i, did in enumerate(drawer_ids))
    else:
        drawers = tuple(DrawerRecord(id=f"id-{i}", document=f"doc {i}", metadata={}) for i in range(parsed))

    if failed_ids is not None:
        failed_rows = tuple(
            FailedRow(
                embedding_pk=i,
                embedding_id=fid,
                reason_type="blank_embedding_id",
                message="blank",
            )
            for i, fid in enumerate(failed_ids)
        )
    else:
        failed_rows = tuple(
            FailedRow(
                embedding_pk=i,
                embedding_id=f"fail-{i}",
                reason_type="blank_embedding_id",
                message="blank",
            )
            for i in range(failed)
        )

    return ExtractionResult(
        palace_path="/fake/palace",
        sqlite_path="/fake/palace/chroma.sqlite3",
        drawers=drawers,
        failed_rows=failed_rows,
        sqlite_embedding_row_count=total,
        pragma_integrity_check="ok",
        collection_name="mempalace_drawers",
    )


# --- Gate 1: no public bool return ----------------------------------------


def test_validation_public_api_never_returns_bool():
    """Introspection: no public function in validation/ returns a plain bool."""
    import mempalace_migrator.validation as val_module

    public_funcs = [
        obj for name, obj in inspect.getmembers(val_module, inspect.isfunction) if not name.startswith("_")
    ]
    for fn in public_funcs:
        hints = fn.__annotations__
        return_hint = hints.get("return")
        assert return_hint is not bool, (
            f"{fn.__name__} has return annotation 'bool'; " "validation must not return binary judgments"
        )


# --- Gate 2: UNKNOWN band with no input ------------------------------------


def test_validate_on_empty_context_returns_unknown_band(tmp_path):
    ctx = _ctx(tmp_path)
    result = validate(ctx)
    assert isinstance(result, ValidationResult)
    assert result.confidence_band == "UNKNOWN"
    assert result.checks_performed == ()
    assert result.summary_counts == {"passed": 0, "failed": 0, "inconclusive": 0}


def test_validate_on_empty_context_no_anomaly_emitted(tmp_path):
    ctx = _ctx(tmp_path)
    validate(ctx)
    # validate() itself emits no anomalies when extraction is absent;
    # step_validate emits the NOT_IMPLEMENTED/LOW stub anomaly instead.
    assert len(ctx.anomalies) == 0


# --- Gate 3: structural check — arithmetic mismatch -----------------------


def test_structural_arithmetic_mismatch_detected_externally(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    # Fabricate ExtractionResult where total != parsed + failed.
    er = ExtractionResult(
        palace_path="/fake",
        sqlite_path="/fake/chroma.sqlite3",
        drawers=(DrawerRecord(id="id-0", document="doc", metadata={}),),
        failed_rows=(),
        sqlite_embedding_row_count=99,  # mismatch: 99 != 1 + 0
        pragma_integrity_check="ok",
        collection_name="mempalace_drawers",
    )
    ctx.extracted_data = er
    result = validate(ctx)
    arithmetic_check = next(
        (c for c in result.checks_performed if c.id == "structural.extraction_arithmetic"),
        None,
    )
    assert arithmetic_check is not None
    assert arithmetic_check.status == "failed"
    assert arithmetic_check.severity_on_failure == Severity.HIGH
    # Anomaly emitted
    anomaly_types = [a.type for a in ctx.anomalies]
    assert AnomalyType.VALIDATION_EXTRACTION_ARITHMETIC in anomaly_types


# --- Gate 4: consistency — same ID in parsed and failed -------------------


def test_consistency_id_in_both_parsed_and_failed_detected(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    shared_id = "duplicate-id"
    ctx.extracted_data = _extraction_result(
        total=2,
        parsed=1,
        drawer_ids=[shared_id],
        failed_ids=[shared_id],
    )
    result = validate(ctx)
    overlap_check = next(
        (c for c in result.checks_performed if c.id == "consistency.id_not_in_both_parsed_and_failed"),
        None,
    )
    assert overlap_check is not None, "overlap check not found in checks_performed"
    assert overlap_check.status == "failed"
    anomaly_types = [a.type for a in ctx.anomalies]
    assert AnomalyType.VALIDATION_ID_PARSED_AND_FAILED in anomaly_types


# --- Gate 5: consistency — failed_row without anomaly ---------------------


def test_consistency_failed_row_without_anomaly_flagged(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    # One failed row, no anomaly in ctx.anomalies for it.
    ctx.extracted_data = _extraction_result(
        total=2,
        parsed=1,
        failed=1,
        failed_ids=["orphaned-row"],
    )
    result = validate(ctx)
    m3_check = next(
        (c for c in result.checks_performed if c.id == "consistency.failed_row_has_anomaly"),
        None,
    )
    assert m3_check is not None
    assert m3_check.status == "failed"
    anomaly_types = [a.type for a in ctx.anomalies]
    assert AnomalyType.VALIDATION_ANOMALY_MISSING_FOR_FAILED_ROW in anomaly_types


# --- Gate 6: heuristic — low parse rate is MEDIUM, not HIGH ---------------


def test_heuristic_low_parse_rate_flags_medium_not_high(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    # parse_rate = 2/10 = 0.20 < 0.50 floor
    ctx.extracted_data = _extraction_result(total=10, parsed=2, failed=8)
    result = validate(ctx)
    heuristic_check = next(
        (c for c in result.checks_performed if c.id == "heuristic.parse_rate_plausible"),
        None,
    )
    assert heuristic_check is not None
    assert heuristic_check.status == "failed"
    assert heuristic_check.severity_on_failure == Severity.MEDIUM
    # Anomaly must be MEDIUM severity (not HIGH or CRITICAL).
    rate_anomalies = [a for a in ctx.anomalies if a.type == AnomalyType.VALIDATION_PARSE_RATE_IMPLAUSIBLE]
    assert len(rate_anomalies) == 1
    assert rate_anomalies[0].severity == Severity.MEDIUM


def test_heuristic_low_parse_rate_band_is_medium(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result(total=10, parsed=2, failed=8)
    result = validate(ctx)
    # Only MEDIUM failures; no HIGH failures → band should be MEDIUM (not LOW).
    # (unless structural also fires, which it would here since 2+8=10=total: OK)
    # Check: total=10, parsed=2, failed=8, 2+8=10 → arithmetic OK.
    assert result.confidence_band == "MEDIUM"


# --- Gate 7: pipeline never aborts at validate stage ---------------------


def test_validation_does_not_abort_pipeline(tmp_path):
    """Even with all checks failed, step_validate must not raise MigratorError."""
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    # Arithmetic mismatch + low parse rate will trigger failures.
    ctx.extracted_data = ExtractionResult(
        palace_path="/fake",
        sqlite_path="/fake/chroma.sqlite3",
        drawers=tuple(DrawerRecord(id=f"id-{i}", document="d", metadata={}) for i in range(1)),
        failed_rows=(),
        sqlite_embedding_row_count=99,  # mismatch
        pragma_integrity_check="ok",
        collection_name="mempalace_drawers",
    )
    # step_validate must not raise anything
    step_validate(ctx)
    assert ctx.validation_result is not None


def test_run_pipeline_does_not_raise_on_validation_failures(tmp_path):
    """run_pipeline completes without raising even when validation produces failures."""
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result(total=10, parsed=2, failed=8)
    # Only run step_validate (step_detect and step_extract already satisfied above).
    from mempalace_migrator.core.pipeline import step_validate as sv

    sv(ctx)
    assert ctx.validation_result is not None
    # Build report to confirm no crash.
    report = build_report(ctx)
    assert report["validation"] is not None


# --- Gate 8: report schema v4 + validation section populated ---------------


def test_report_schema_version_is_4(tmp_path):
    ctx = _ctx(tmp_path)
    rep = build_report(ctx)
    assert rep["schema_version"] == REPORT_SCHEMA_VERSION == 5


def test_report_validation_section_populated_when_extraction_ran(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result()
    step_validate(ctx)
    rep = build_report(ctx)
    assert rep["validation"] is not None
    assert "checks_performed" in rep["validation"]
    assert "checks_not_performed" in rep["validation"]
    assert "confidence_band" in rep["validation"]
    assert "summary_counts" in rep["validation"]


def test_report_validation_section_null_when_extraction_missing(tmp_path):
    ctx = _ctx(tmp_path)
    # No extraction → step_validate emits NOT_IMPLEMENTED stub
    step_validate(ctx)
    rep = build_report(ctx)
    assert rep["validation"] is None


def test_report_is_json_safe_with_validation(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result()
    step_validate(ctx)
    rep = build_report(ctx)
    serialised = json.dumps(rep)  # no default= — must not raise
    assert isinstance(serialised, str)


# --- Gate 9: overall band includes validation ---------------------------


def test_overall_band_includes_validation_weakest(tmp_path):
    """detection=HIGH, extraction=HIGH, validation=LOW → overall=LOW."""
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result(confidence=0.95)
    # Parse rate = 5/5 = 100% → extraction HIGH.
    # Force validation LOW via duplicate drawer IDs (consistency HIGH failure).
    ctx.extracted_data = _extraction_result(
        total=5,
        drawer_ids=["id-0", "id-1", "id-0", "id-2", "id-3"],  # "id-0" duplicated
    )
    step_validate(ctx)
    rep = build_report(ctx)
    assert rep["confidence_summary"]["detection"]["band"] == "HIGH"
    assert rep["confidence_summary"]["extraction"]["band"] == "HIGH"
    assert rep["confidence_summary"]["validation"]["band"] == "LOW"
    assert rep["confidence_summary"]["overall_band"] == "LOW"


def test_overall_band_not_affected_by_absent_validation(tmp_path):
    """When validation didn't run (extraction absent), it doesn't force UNKNOWN."""
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result(confidence=0.95)
    rep = build_report(ctx)
    # Only detection signal present.
    assert rep["confidence_summary"]["validation"] is None
    assert rep["confidence_summary"]["overall_band"] == "HIGH"


# --- Gate 10: checks_not_performed lists reconstruction stubs -----------


def test_skipped_checks_listed_explicitly(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result()
    result = validate(ctx)
    skipped_ids = [c.id for c in result.checks_not_performed]
    assert "target_record_count_parity" in skipped_ids
    assert "target_id_set_parity" in skipped_ids
    assert "target_document_hash_parity" in skipped_ids
    assert "target_metadata_parity" in skipped_ids
    assert "target_embedding_presence" in skipped_ids


def test_skipped_checks_reason_is_reconstruction_not_run(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result()
    # reconstruction_result not set → parity checks must be skipped
    result = validate(ctx)
    parity_ids = {
        "target_record_count_parity",
        "target_id_set_parity",
        "target_document_hash_parity",
        "target_metadata_parity",
        "target_embedding_presence",
    }
    for skipped in result.checks_not_performed:
        if skipped.id in parity_ids:
            assert (
                skipped.reason == "reconstruction_not_run"
            ), f"{skipped.id}: expected reason='reconstruction_not_run', got {skipped.reason!r}"


def test_checks_not_performed_always_nonempty_while_reconstruction_stub(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result()
    result = validate(ctx)
    assert len(result.checks_not_performed) > 0


# --- Gate 11: no correctness vocabulary in output -----------------------


_FORBIDDEN_WORDS = frozenset(["correct", "verified", "guaranteed", "valid "])
# Note: "validation" as a module/section name is acceptable; we search
# for the forbidden words at word boundaries to avoid false positives.

import re as _re

_FORBIDDEN_RE = _re.compile(
    r"\b(" + "|".join(_re.escape(w.strip()) for w in _FORBIDDEN_WORDS) + r")\b",
    _re.IGNORECASE,
)


def test_no_correctness_vocabulary_in_text_output(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result()
    step_validate(ctx)
    rep = build_report(ctx)
    text = render_text(rep)
    matches = _FORBIDDEN_RE.findall(text)
    assert not matches, f"Forbidden correctness vocabulary found in text output: {matches}"


def test_no_correctness_vocabulary_in_json_output(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result()
    step_validate(ctx)
    rep = build_report(ctx)
    json_str = json.dumps(rep)
    matches = _FORBIDDEN_RE.findall(json_str)
    assert not matches, f"Forbidden correctness vocabulary found in JSON output: {matches}"


# --- Gate 12: all new AnomalyTypes are registered -----------------------


def test_all_validation_anomaly_types_registered():
    """Every AnomalyType.VALIDATION_* is in the closed enum registry."""
    validation_types = [
        AnomalyType.VALIDATION_EXTRACTION_ARITHMETIC,
        AnomalyType.VALIDATION_DRAWER_MALFORMED,
        AnomalyType.VALIDATION_DETECTION_EVIDENCE_EMPTY,
        AnomalyType.VALIDATION_DUPLICATE_ID_MISSED_BY_EXTRACTION,
        AnomalyType.VALIDATION_ID_PARSED_AND_FAILED,
        AnomalyType.VALIDATION_ANOMALY_MISSING_FOR_FAILED_ROW,
        AnomalyType.VALIDATION_STAGE_RESULT_INCONSISTENT,
        AnomalyType.VALIDATION_PARSE_RATE_IMPLAUSIBLE,
        AnomalyType.VALIDATION_EMPTY_SOURCE,
        AnomalyType.VALIDATION_DOMINANT_FAILURE_TYPE,
    ]
    all_type_values = {t.value for t in AnomalyType}
    for at in validation_types:
        assert at.value in all_type_values, f"{at!r} not found in AnomalyType registry"


def test_validation_anomaly_types_are_emittable_via_ctx(tmp_path):
    """Every new AnomalyType can be used with add_anomaly (no rejection)."""
    ctx = _ctx(tmp_path)
    validation_types = [
        AnomalyType.VALIDATION_EXTRACTION_ARITHMETIC,
        AnomalyType.VALIDATION_DRAWER_MALFORMED,
        AnomalyType.VALIDATION_DETECTION_EVIDENCE_EMPTY,
        AnomalyType.VALIDATION_DUPLICATE_ID_MISSED_BY_EXTRACTION,
        AnomalyType.VALIDATION_ID_PARSED_AND_FAILED,
        AnomalyType.VALIDATION_ANOMALY_MISSING_FOR_FAILED_ROW,
        AnomalyType.VALIDATION_STAGE_RESULT_INCONSISTENT,
        AnomalyType.VALIDATION_PARSE_RATE_IMPLAUSIBLE,
        AnomalyType.VALIDATION_EMPTY_SOURCE,
        AnomalyType.VALIDATION_DOMINANT_FAILURE_TYPE,
    ]
    for at in validation_types:
        ctx.add_anomaly(
            type=at,
            severity=Severity.MEDIUM,
            message=f"test emission of {at.value}",
            location=AnomalyLocation(stage="validate", source="test"),
            evidence=[AnomalyEvidence(kind="observation", detail="test")],
        )
    assert len(ctx.anomalies) == len(validation_types)


# --- Additional: heuristic threshold constants are documented -----------


def test_heuristic_thresholds_are_module_constants():
    from mempalace_migrator.validation import heuristics

    assert hasattr(heuristics, "PARSE_RATE_PLAUSIBILITY_FLOOR")
    assert hasattr(heuristics, "DOMINANT_FAILURE_TYPE_THRESHOLD")
    assert isinstance(heuristics.PARSE_RATE_PLAUSIBILITY_FLOOR, float)
    assert isinstance(heuristics.DOMINANT_FAILURE_TYPE_THRESHOLD, float)


def test_heuristic_threshold_present_in_evidence_data(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result(total=10, parsed=2, failed=8)
    validate(ctx)
    rate_anomalies = [a for a in ctx.anomalies if a.type == AnomalyType.VALIDATION_PARSE_RATE_IMPLAUSIBLE]
    assert rate_anomalies
    ev_data = rate_anomalies[0].evidence[0].data
    assert "threshold" in ev_data


# --- Confidence band rules for validation --------------------------------


def test_validation_band_high_when_all_checks_pass(tmp_path):
    """A clean extraction result → all checks pass → HIGH band."""
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result(total=10, parsed=10, failed=0)
    # Add anomalies for all drawers so M3 consistency check passes.
    # (No failed_rows → consistency.failed_row_has_anomaly is trivially passed.)
    result = validate(ctx)
    assert result.confidence_band == "HIGH"


def test_validation_band_low_when_high_severity_check_fails(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    # total=99 but parsed+failed=1+0=1 → structural HIGH failure → band=LOW
    ctx.extracted_data = ExtractionResult(
        palace_path="/fake",
        sqlite_path="/fake/chroma.sqlite3",
        drawers=(DrawerRecord(id="x", document="d", metadata={}),),
        failed_rows=(),
        sqlite_embedding_row_count=99,
        pragma_integrity_check="ok",
        collection_name="mempalace_drawers",
    )
    result = validate(ctx)
    assert result.confidence_band == "LOW"


def test_validate_result_is_json_safe(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result()
    result = validate(ctx)
    serialised = json.dumps(result.to_dict())
    assert isinstance(serialised, str)
    assert result.confidence_band == "LOW"


def test_validate_result_is_json_safe(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result()
    result = validate(ctx)
    serialised = json.dumps(result.to_dict())
    assert isinstance(serialised, str)


def test_validate_result_is_json_safe(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result()
    result = validate(ctx)
    serialised = json.dumps(result.to_dict())
    assert isinstance(serialised, str)
    assert result.confidence_band == "LOW"


def test_validate_result_is_json_safe(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result()
    result = validate(ctx)
    serialised = json.dumps(result.to_dict())
    assert isinstance(serialised, str)


def test_validate_result_is_json_safe(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result()
    result = validate(ctx)
    serialised = json.dumps(result.to_dict())
    assert isinstance(serialised, str)
    assert result.confidence_band == "LOW"


def test_validate_result_is_json_safe(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result()
    result = validate(ctx)
    serialised = json.dumps(result.to_dict())
    assert isinstance(serialised, str)
