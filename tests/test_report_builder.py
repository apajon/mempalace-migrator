"""Tests for reporting/report_builder.py (M4).

These tests verify:
  - Stable top-level key contract (schema version 3).
  - Correct anomaly_summary: by_severity, by_type, by_stage, top_severity.
  - stages section: executed / aborted / skipped / not_run correctness.
  - confidence_summary: weakest-band rule, UNKNOWN propagation.
  - Failure visibility: outcome + failure block populated correctly.
  - Consistency invariant: REPORT_INCONSISTENT_FAILURE injected when
    outcome=failure but no CRITICAL anomaly matches failure.stage.
  - Strict JSON serializability (no default= fallback).
  - explicitly_not_checked unchanged.

No real SQLite is required. DetectionResult and ExtractionResult are
constructed directly with minimal valid values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mempalace_migrator.core.context import AnomalyEvidence, AnomalyLocation, AnomalyType, MigrationContext, Severity
from mempalace_migrator.core.errors import PipelineAbort
from mempalace_migrator.detection.format_detector import CHROMA_0_6, DetectionResult, Evidence
from mempalace_migrator.extraction.chroma_06_reader import DrawerRecord, ExtractionResult, FailedRow
from mempalace_migrator.reporting.report_builder import (
    EXPLICITLY_NOT_CHECKED,
    REPORT_SCHEMA_VERSION,
    REPORT_TOP_LEVEL_KEYS,
    build_report,
)

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


def _extraction_result(total: int = 10, parsed: int = 10, failed: int = 0) -> ExtractionResult:
    drawers = tuple(DrawerRecord(id=f"id-{i}", document=f"doc {i}", metadata={}) for i in range(parsed))
    failed_rows = tuple(
        FailedRow(
            embedding_pk=i,
            embedding_id=None,
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


def _abort(stage: str = "detect") -> PipelineAbort:
    return PipelineAbort(
        stage=stage,
        code="unsupported_source_format",
        summary="source is not chroma_0_6",
    )


# --- Contract / schema ----------------------------------------------------


def test_report_schema_version_is_3(tmp_path):
    ctx = _ctx(tmp_path)
    rep = build_report(ctx)
    assert rep["schema_version"] == REPORT_SCHEMA_VERSION == 3


def test_report_top_level_keys_are_stable(tmp_path):
    ctx = _ctx(tmp_path)
    rep = build_report(ctx)
    assert set(rep.keys()) == set(REPORT_TOP_LEVEL_KEYS)


def test_explicitly_not_checked_unchanged(tmp_path):
    ctx = _ctx(tmp_path)
    rep = build_report(ctx)
    assert rep["explicitly_not_checked"] == list(EXPLICITLY_NOT_CHECKED)
    assert len(rep["explicitly_not_checked"]) == 7


def test_report_is_json_safe_strict(tmp_path):
    """json.dumps without a default= fallback must not raise."""
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result()
    rep = build_report(ctx)
    # No default= — any non-JSON-safe value will raise TypeError here.
    serialised = json.dumps(rep)
    assert isinstance(serialised, str)


# --- Outcome / failure ----------------------------------------------------


def test_report_outcome_success_on_no_failure(tmp_path):
    ctx = _ctx(tmp_path)
    rep = build_report(ctx, failure=None)
    assert rep["outcome"] == "success"
    assert rep["failure"] is None


def test_report_outcome_failure_when_failure_provided(tmp_path):
    ctx = _ctx(tmp_path)
    # Record a matching CRITICAL anomaly so consistency invariant is satisfied.
    ctx.add_anomaly(
        type=AnomalyType.UNSUPPORTED_SOURCE_FORMAT,
        severity=Severity.CRITICAL,
        message="not chroma_0_6",
        location=AnomalyLocation(stage="detect", source="detection"),
        evidence=[AnomalyEvidence(kind="observation", detail="x")],
    )
    abort = _abort("detect")
    rep = build_report(ctx, failure=abort)
    assert rep["outcome"] == "failure"
    assert rep["failure"] is not None
    assert rep["failure"]["stage"] == "detect"
    assert rep["failure"]["code"] == "unsupported_source_format"


# --- Anomaly summary ------------------------------------------------------


def test_anomaly_summary_empty_on_no_anomalies(tmp_path):
    ctx = _ctx(tmp_path)
    rep = build_report(ctx)
    s = rep["anomaly_summary"]
    assert s["total"] == 0
    assert s["top_severity"] == "none"
    assert s["by_stage"] == {}
    assert s["by_type"] == {}
    assert s["by_severity"] == {"low": 0, "medium": 0, "high": 0, "critical": 0}


def test_anomaly_summary_by_stage_and_top_severity(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.add_anomaly(
        type=AnomalyType.BLANK_EMBEDDING_ID,
        severity=Severity.HIGH,
        message="blank id",
        location=AnomalyLocation(stage="extract", source="embeddings"),
        evidence=[AnomalyEvidence(kind="observation", detail="null id")],
    )
    ctx.add_anomaly(
        type=AnomalyType.NOT_IMPLEMENTED,
        severity=Severity.LOW,
        message="stub",
        location=AnomalyLocation(stage="transform", source="pipeline"),
        evidence=[AnomalyEvidence(kind="observation", detail="stub")],
    )
    rep = build_report(ctx)
    s = rep["anomaly_summary"]
    assert s["total"] == 2
    assert s["by_stage"] == {"extract": 1, "transform": 1}
    assert s["top_severity"] == "high"
    assert s["by_severity"]["high"] == 1
    assert s["by_severity"]["low"] == 1


def test_anomaly_summary_top_severity_critical(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.add_anomaly(
        type=AnomalyType.UNSUPPORTED_SOURCE_FORMAT,
        severity=Severity.CRITICAL,
        message="x",
        location=AnomalyLocation(stage="detect"),
        evidence=[AnomalyEvidence(kind="observation", detail="x")],
    )
    ctx.add_anomaly(
        type=AnomalyType.BLANK_EMBEDDING_ID,
        severity=Severity.LOW,
        message="y",
        location=AnomalyLocation(stage="extract"),
        evidence=[AnomalyEvidence(kind="observation", detail="y")],
    )
    rep = build_report(ctx)
    assert rep["anomaly_summary"]["top_severity"] == "critical"


# --- Stages section -------------------------------------------------------


def test_stages_section_all_not_run_by_default(tmp_path):
    ctx = _ctx(tmp_path)
    rep = build_report(ctx)
    stages = rep["stages"]
    # All 5 stages present
    assert set(stages.keys()) == {"detect", "extract", "transform", "reconstruct", "validate"}
    for name, info in stages.items():
        assert info["status"] == "not_run", f"{name} should be not_run"
        assert info["result_present"] is False


def test_stages_section_executed_when_result_present(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result()
    ctx.extracted_data = _extraction_result()
    rep = build_report(ctx)
    stages = rep["stages"]
    assert stages["detect"]["status"] == "executed"
    assert stages["detect"]["result_present"] is True
    assert stages["extract"]["status"] == "executed"
    assert stages["extract"]["result_present"] is True
    assert stages["transform"]["status"] == "not_run"


def test_stages_section_aborted_on_critical_anomaly(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.add_anomaly(
        type=AnomalyType.UNSUPPORTED_SOURCE_FORMAT,
        severity=Severity.CRITICAL,
        message="abort",
        location=AnomalyLocation(stage="detect"),
        evidence=[AnomalyEvidence(kind="observation", detail="abort")],
    )
    rep = build_report(ctx)
    assert rep["stages"]["detect"]["status"] == "aborted"
    assert rep["stages"]["detect"]["result_present"] is False


def test_stages_section_skipped_on_stub_anomaly(tmp_path):
    ctx = _ctx(tmp_path)
    # The pipeline stub steps emit NOT_IMPLEMENTED/LOW anomalies.
    for stage in ("transform", "reconstruct", "validate"):
        ctx.add_anomaly(
            type=AnomalyType.NOT_IMPLEMENTED,
            severity=Severity.LOW,
            message=f"{stage} stub",
            location=AnomalyLocation(stage=stage, source="pipeline"),
            evidence=[AnomalyEvidence(kind="observation", detail="stub")],
        )
    rep = build_report(ctx)
    for stage in ("transform", "reconstruct", "validate"):
        assert rep["stages"][stage]["status"] == "skipped"
        assert rep["stages"][stage]["skipped_reason"] == "stub"


def test_stages_section_aborted_takes_priority_over_skipped(tmp_path):
    """A stage with both a CRITICAL anomaly and a NOT_IMPLEMENTED anomaly
    should be marked aborted, not skipped."""
    ctx = _ctx(tmp_path)
    ctx.add_anomaly(
        type=AnomalyType.UNSUPPORTED_SOURCE_FORMAT,
        severity=Severity.CRITICAL,
        message="abort",
        location=AnomalyLocation(stage="detect"),
        evidence=[AnomalyEvidence(kind="observation", detail="abort")],
    )
    ctx.add_anomaly(
        type=AnomalyType.NOT_IMPLEMENTED,
        severity=Severity.LOW,
        message="detect stub",
        location=AnomalyLocation(stage="detect"),
        evidence=[AnomalyEvidence(kind="observation", detail="stub")],
    )
    rep = build_report(ctx)
    assert rep["stages"]["detect"]["status"] == "aborted"


# --- Confidence summary ---------------------------------------------------


def test_confidence_summary_unknown_when_nothing_ran(tmp_path):
    ctx = _ctx(tmp_path)
    rep = build_report(ctx)
    conf = rep["confidence_summary"]
    assert conf["detection"] is None
    assert conf["extraction"] is None
    assert conf["overall_band"] == "UNKNOWN"


def test_confidence_summary_high_when_both_signals_high(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result(confidence=0.95)
    ctx.extracted_data = _extraction_result(total=100, parsed=100)
    rep = build_report(ctx)
    conf = rep["confidence_summary"]
    assert conf["detection"]["band"] == "HIGH"
    assert conf["extraction"]["band"] == "HIGH"
    assert conf["overall_band"] == "HIGH"


@pytest.mark.parametrize(
    "detection_conf,parse_rate,expected_overall",
    [
        (0.95, 1.0, "HIGH"),  # both high
        (0.70, 1.0, "MEDIUM"),  # detection medium, extraction high → medium
        (0.95, 0.90, "MEDIUM"),  # detection high, extraction medium → medium
        (0.50, 1.0, "LOW"),  # detection low, extraction high → low
        (0.95, 0.80, "LOW"),  # detection high, extraction low → low
        (0.50, 0.80, "LOW"),  # both low
    ],
)
def test_confidence_summary_weakest_band_rule(tmp_path, detection_conf, parse_rate, expected_overall):
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result(confidence=detection_conf)
    parsed = round(parse_rate * 100)
    ctx.extracted_data = _extraction_result(total=100, parsed=parsed)
    rep = build_report(ctx)
    assert rep["confidence_summary"]["overall_band"] == expected_overall


def test_confidence_summary_unknown_when_only_detection_ran(tmp_path):
    """When extraction has not run, only one band is available.
    overall should still reflect that band (not UNKNOWN)."""
    ctx = _ctx(tmp_path)
    ctx.detected_format = _detection_result(confidence=0.95)
    # No extracted_data.
    rep = build_report(ctx)
    conf = rep["confidence_summary"]
    assert conf["extraction"] is None
    # Only detection band available → overall equals detection band.
    assert conf["overall_band"] == conf["detection"]["band"] == "HIGH"


def test_confidence_summary_rule_field_is_stable(tmp_path):
    ctx = _ctx(tmp_path)
    rep = build_report(ctx)
    assert rep["confidence_summary"]["rule"] == "overall = weakest_band(detection, extraction)"


# --- Consistency invariant ------------------------------------------------


def test_failure_inconsistency_self_recorded(tmp_path):
    """outcome=failure with NO matching CRITICAL anomaly → meta-anomaly injected."""
    ctx = _ctx(tmp_path)
    # No anomalies recorded at all.
    abort = _abort("detect")
    rep = build_report(ctx, failure=abort)
    assert rep["outcome"] == "failure"
    meta = next(
        (a for a in rep["anomalies"] if a["type"] == "report_inconsistent_failure"),
        None,
    )
    assert meta is not None, "REPORT_INCONSISTENT_FAILURE anomaly expected in report"
    assert meta["severity"] == "high"
    assert meta["stage"] == "report"


def test_failure_inconsistency_not_injected_when_critical_present(tmp_path):
    """When a matching CRITICAL anomaly exists, no meta-anomaly is injected."""
    ctx = _ctx(tmp_path)
    ctx.add_anomaly(
        type=AnomalyType.UNSUPPORTED_SOURCE_FORMAT,
        severity=Severity.CRITICAL,
        message="not chroma_0_6",
        location=AnomalyLocation(stage="detect"),
        evidence=[AnomalyEvidence(kind="observation", detail="x")],
    )
    abort = _abort("detect")
    rep = build_report(ctx, failure=abort)
    meta_count = sum(1 for a in rep["anomalies"] if a["type"] == "report_inconsistent_failure")
    assert meta_count == 0


def test_failure_inconsistency_does_not_mutate_ctx(tmp_path):
    """ctx.anomalies must not be modified by the consistency invariant."""
    ctx = _ctx(tmp_path)
    abort = _abort("detect")
    before = len(ctx.anomalies)
    build_report(ctx, failure=abort)
    assert len(ctx.anomalies) == before  # ctx unchanged


def test_failure_inconsistency_anomaly_counted_in_summary(tmp_path):
    """The injected meta-anomaly must appear in anomaly_summary totals."""
    ctx = _ctx(tmp_path)
    abort = _abort("detect")
    rep = build_report(ctx, failure=abort)
    assert rep["anomaly_summary"]["total"] == 1
    assert rep["anomaly_summary"]["by_stage"].get("report", 0) == 1
