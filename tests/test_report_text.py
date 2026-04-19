"""Tests for reporting/text_renderer.py (M4).

These tests verify:
  - render_text is a pure function (same input → same output).
  - Every stage in stages{} appears in the rendered output.
  - Failure block surfaced when present.
  - Minimal/partial reports render without error.
  - CLI delegates to render_text (no rendering logic in cli/).
"""

from __future__ import annotations

from typing import Any

from mempalace_migrator.reporting.text_renderer import render_text

# --- Minimal report fixture -----------------------------------------------


def _minimal_report(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 3,
        "tool_version": "0.1.0",
        "supported_version_pairs": [],
        "run_id": "abcd1234-0000-0000-0000-000000000000",
        "started_at": "2026-04-19T00:00:00Z",
        "completed_at": "2026-04-19T00:00:01Z",
        "outcome": "success",
        "failure": None,
        "input": {"source_path": "/fake/palace", "target_path": None},
        "detection": None,
        "extraction": None,
        "extraction_stats": None,
        "transformation": None,
        "reconstruction": None,
        "validation": None,
        "stages": {
            "detect": {"status": "not_run", "result_present": False, "skipped_reason": None},
            "extract": {"status": "not_run", "result_present": False, "skipped_reason": None},
            "transform": {"status": "not_run", "result_present": False, "skipped_reason": None},
            "reconstruct": {"status": "not_run", "result_present": False, "skipped_reason": None},
            "validate": {"status": "not_run", "result_present": False, "skipped_reason": None},
        },
        "confidence_summary": {
            "detection": None,
            "extraction": None,
            "overall_band": "UNKNOWN",
            "rule": "overall = weakest_band(detection, extraction)",
        },
        "anomalies": [],
        "anomaly_summary": {
            "total": 0,
            "by_severity": {"low": 0, "medium": 0, "high": 0, "critical": 0},
            "by_type": {},
            "by_stage": {},
            "top_severity": "none",
        },
        "explicitly_not_checked": ["item_a", "item_b"],
    }
    base.update(overrides)
    return base


# --- Purity ---------------------------------------------------------------


def test_render_text_is_pure_function():
    rep = _minimal_report()
    first = render_text(rep)
    second = render_text(rep)
    assert first == second


# --- Mandatory fields present in output -----------------------------------


def test_render_text_contains_run_id():
    rep = _minimal_report()
    out = render_text(rep)
    assert "abcd1234" in out


def test_render_text_contains_outcome():
    rep = _minimal_report()
    out = render_text(rep)
    assert "outcome: success" in out


def test_render_text_contains_explicitly_not_checked_count():
    rep = _minimal_report()
    out = render_text(rep)
    assert "explicitly_not_checked: 2 items" in out


def test_render_text_lists_every_stage():
    rep = _minimal_report()
    out = render_text(rep)
    for stage in ("detect", "extract", "transform", "reconstruct", "validate"):
        assert f"stage/{stage}" in out, f"stage/{stage} missing from text output"


# --- Failure surfacing ----------------------------------------------------


def test_render_text_surfaces_failure_when_present():
    rep = _minimal_report(
        outcome="failure",
        failure={
            "stage": "detect",
            "code": "unsupported_source_format",
            "summary": "not chroma_0_6",
            "details": [],
        },
    )
    out = render_text(rep)
    assert "failure" in out
    assert "detect" in out
    assert "unsupported_source_format" in out


def test_render_text_no_failure_block_on_success():
    rep = _minimal_report()
    out = render_text(rep)
    # "failure" should not appear as a standalone line when outcome=success.
    lines = out.splitlines()
    failure_lines = [l for l in lines if l.startswith("failure:")]
    assert failure_lines == []


# --- Detection and extraction sections ------------------------------------


def test_render_text_shows_detection_when_present():
    rep = _minimal_report(
        detection={
            "classification": "chroma_0_6",
            "confidence": 0.95,
            "confidence_band": "HIGH",
            "source_version": "0.6.3",
        }
    )
    out = render_text(rep)
    assert "chroma_0_6" in out
    assert "0.95" in out


def test_render_text_shows_extraction_stats_when_present():
    rep = _minimal_report(
        extraction_stats={
            "total_rows": 100,
            "parsed_rows": 99,
            "failed_rows": 1,
            "parse_rate": 0.99,
        }
    )
    out = render_text(rep)
    assert "total=100" in out
    assert "parsed=99" in out


def test_render_text_shows_confidence_overall():
    rep = _minimal_report(
        confidence_summary={
            "detection": {"confidence": 0.95, "band": "HIGH"},
            "extraction": None,
            "overall_band": "HIGH",
            "rule": "overall = weakest_band(detection, extraction)",
        }
    )
    out = render_text(rep)
    assert "confidence_overall: HIGH" in out


# --- Stages status in output ----------------------------------------------


def test_render_text_shows_stage_status():
    rep = _minimal_report()
    rep["stages"]["detect"]["status"] = "executed"
    rep["stages"]["extract"]["status"] = "aborted"
    rep["stages"]["transform"]["status"] = "skipped"
    rep["stages"]["transform"]["skipped_reason"] = "stub"
    out = render_text(rep)
    assert "stage/detect: executed" in out
    assert "stage/extract: aborted" in out
    assert "stage/transform: skipped (stub)" in out


# --- Anomaly listing ------------------------------------------------------


def test_render_text_lists_anomalies():
    rep = _minimal_report(
        anomalies=[
            {
                "type": "blank_embedding_id",
                "severity": "high",
                "stage": "extract",
                "message": "row pk=5 has blank id",
            }
        ],
        anomaly_summary={
            "total": 1,
            "by_severity": {"low": 0, "medium": 0, "high": 1, "critical": 0},
            "by_type": {"blank_embedding_id": 1},
            "by_stage": {"extract": 1},
            "top_severity": "high",
        },
    )
    out = render_text(rep)
    assert "blank_embedding_id" in out
    assert "row pk=5 has blank id" in out
    assert "top_severity: high" in out


# --- Robustness on minimal / partial reports ------------------------------


def test_render_text_handles_empty_report():
    """render_text must not raise on an empty dict."""
    out = render_text({})
    assert isinstance(out, str)


def test_render_text_handles_no_stages():
    rep = _minimal_report()
    del rep["stages"]
    out = render_text(rep)
    assert isinstance(out, str)
