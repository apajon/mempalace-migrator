"""Build a structured report from a MigrationContext.

The report is JSON-serialisable. Schema version 3 adds:
  - stages: per-stage status (executed | aborted | skipped | not_run)
  - confidence_summary: detection + extraction bands, weakest-band overall
  - anomaly_summary.by_stage: anomaly counts keyed by stage
  - anomaly_summary.top_severity: highest severity actually observed
  - consistency invariant: REPORT_INCONSISTENT_FAILURE injected into the
    report (not into ctx.anomalies) when outcome=failure but no matching
    CRITICAL anomaly exists for failure.stage

Always emits:
  - explicitly_not_checked: mandatory disclaimer list
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mempalace_migrator.core.context import SEVERITIES, AnomalyType, MigrationContext, Severity
from mempalace_migrator.core.errors import MigratorError
from mempalace_migrator.detection.format_detector import SUPPORTED_VERSION_PAIRS

from mempalace_migrator import __version__ as _migrator_version

REPORT_SCHEMA_VERSION = 5
TOOL_VERSION: str = _migrator_version

EXPLICITLY_NOT_CHECKED: tuple[str, ...] = (
    "sqlite_corruption_below_pragma_level",
    "embedding_vector_equivalence_source_to_target",
    "search_result_semantic_equivalence",
    "concurrent_access_absence_during_extraction",
    "target_chromadb_default_embedding_function_match",
    "hnsw_segment_file_integrity",
    "manifest_authenticity",
)

# Stable top-level keys contract. Tested by test_report_top_level_keys_are_stable.
REPORT_TOP_LEVEL_KEYS: tuple[str, ...] = (
    "schema_version",
    "tool_version",
    "supported_version_pairs",
    "run_id",
    "started_at",
    "completed_at",
    "outcome",
    "failure",
    "input",
    "detection",
    "extraction",
    "extraction_stats",
    "transformation",
    "reconstruction",
    "validation",
    "stages",
    "confidence_summary",
    "anomalies",
    "anomaly_summary",
    "explicitly_not_checked",
)

# All pipeline stages in execution order.
_PIPELINE_STAGES: tuple[str, ...] = (
    "detect",
    "extract",
    "transform",
    "reconstruct",
    "validate",
)

# Parse-rate → confidence band thresholds (distinct from detection thresholds).
_PARSE_RATE_HIGH = 0.99
_PARSE_RATE_MEDIUM = 0.90

# Band rank for weakest-band rule (lower = worse). UNKNOWN is absent → propagates.
_BAND_RANK: dict[str, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_report(ctx: MigrationContext, *, failure: MigratorError | None = None) -> dict[str, Any]:
    # Serialise ctx anomalies first; the consistency check may append one more.
    anomaly_dicts: list[dict[str, Any]] = [a.to_dict() for a in ctx.anomalies]

    # Consistency invariant: outcome=failure must have >=1 CRITICAL anomaly
    # for failure.stage. If not, inject a meta-anomaly into the report only
    # (ctx is never mutated after the pipeline has finished).
    if failure is not None:
        has_critical_for_stage = any(
            a["severity"] == "critical" and a["stage"] == failure.stage for a in anomaly_dicts
        )
        if not has_critical_for_stage:
            anomaly_dicts = anomaly_dicts + [_make_inconsistent_failure_dict(failure)]

    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "supported_version_pairs": [{"source": s, "target": t} for s, t in SUPPORTED_VERSION_PAIRS],
        "run_id": ctx.run_id,
        "started_at": ctx.started_at,
        "completed_at": _utc_now_iso(),
        "outcome": "success" if failure is None else "failure",
        "failure": failure.to_dict() if failure is not None else None,
        "input": {
            "source_path": str(ctx.source_path),
            "target_path": str(ctx.target_path) if ctx.target_path else None,
        },
        "detection": _detection_section(ctx),
        "extraction": _extraction_section(ctx),
        "extraction_stats": _extraction_stats(ctx),
        "transformation": _transformation_section(ctx),
        "reconstruction": _reconstruction_section(ctx),
        "validation": _validation_section(ctx),
        "stages": _stages_section(ctx),
        "confidence_summary": _confidence_summary(ctx),
        "anomalies": anomaly_dicts,
        "anomaly_summary": _anomaly_summary_from_list(anomaly_dicts),
        "explicitly_not_checked": list(EXPLICITLY_NOT_CHECKED),
    }
    return report


# --- Section builders -----------------------------------------------------


def _validation_section(ctx: MigrationContext) -> dict[str, Any] | None:
    vr = ctx.validation_result
    if vr is None:
        return None
    return vr.to_dict()


def _detection_section(ctx: MigrationContext) -> dict[str, Any] | None:
    if ctx.detected_format is None:
        return None
    return ctx.detected_format.to_dict()


def _extraction_section(ctx: MigrationContext) -> dict[str, Any] | None:
    er = ctx.extracted_data
    if er is None:
        return None
    return {
        "palace_path": er.palace_path,
        "sqlite_path": er.sqlite_path,
        "collection_name": er.collection_name,
        "pragma_integrity_check": er.pragma_integrity_check,
        "failed_rows": [f.to_dict() for f in er.failed_rows],
    }


def _extraction_stats(ctx: MigrationContext) -> dict[str, Any] | None:
    er = ctx.extracted_data
    if er is None:
        return None
    total = er.total_count
    parsed = er.parsed_count
    failed = er.failed_count
    parse_rate = (parsed / total) if total else 0.0
    return {
        "total_rows": total,
        "parsed_rows": parsed,
        "failed_rows": failed,
        "parse_rate": round(parse_rate, 4),
    }


def _transformation_section(ctx: MigrationContext) -> dict[str, Any] | None:
    tb = ctx.transformed_data
    if tb is None:
        return None
    s = tb.summary
    return {
        "collection_name": tb.collection_name,
        "drawer_count": s.drawer_count,
        "dropped_count": s.dropped_count,
        "coerced_count": s.coerced_count,
        "sample_ids": list(s.sample_ids),
        "metadata_keys": list(s.metadata_keys),
        "wing_room_counts": [{"wing": w, "room": r, "count": c} for w, r, c in s.wing_room_counts],
        "length_profile": {
            "min": s.length_profile.min,
            "max": s.length_profile.max,
            "mean": s.length_profile.mean,
            "p50": s.length_profile.p50,
            "p95": s.length_profile.p95,
        },
    }


def _reconstruction_section(ctx: MigrationContext) -> dict[str, Any] | None:
    rr = ctx.reconstruction_result
    if rr is None:
        return None
    return {
        "target_path": str(rr.target_path),
        "collection_name": rr.collection_name,
        "imported_count": rr.imported_count,
        "batch_size": rr.batch_size,
        "chromadb_version": rr.chromadb_version,
        "target_manifest_path": str(rr.target_manifest_path),
    }


def _stages_section(ctx: MigrationContext) -> dict[str, Any]:
    """Dense map of every pipeline stage → execution status.

    Status rules (evaluated in priority order):
      aborted  — a CRITICAL anomaly exists for this stage.
      skipped  — ctx.stage_skip_reasons has an entry for this stage, OR a
                 NOT_IMPLEMENTED/LOW anomaly exists (legacy stub path).
      executed — the stage's result slot on ctx is non-None.
      not_run  — none of the above.
    """
    result_slots: dict[str, Any] = {
        "detect": ctx.detected_format,
        "extract": ctx.extracted_data,
        "transform": ctx.transformed_data,
        "reconstruct": ctx.reconstruction_result,
        "validate": ctx.validation_result,
    }

    critical_stages: set[str] = {a.stage for a in ctx.anomalies if a.severity == Severity.CRITICAL}
    stub_stages: set[str] = {
        a.stage for a in ctx.anomalies if a.type == AnomalyType.NOT_IMPLEMENTED and a.severity == Severity.LOW
    }

    stages: dict[str, Any] = {}
    for stage in _PIPELINE_STAGES:
        result_present = result_slots[stage] is not None
        skip_reason = ctx.stage_skip_reasons.get(stage)  # e.g. "no_target_path"
        if stage in critical_stages:
            status = "aborted"
        elif skip_reason is not None or stage in stub_stages:
            status = "skipped"
        elif result_present:
            status = "executed"
        else:
            status = "not_run"
        skipped_reason_out: str | None = None
        if status == "skipped":
            skipped_reason_out = skip_reason if skip_reason is not None else "stub"
        stages[stage] = {
            "status": status,
            "result_present": result_present,
            "skipped_reason": skipped_reason_out,
        }
    return stages


def _confidence_summary(ctx: MigrationContext) -> dict[str, Any]:
    """Aggregate confidence signals across stages.

    Rule: overall_band = weakest_band(detection, extraction).
    If any stage has no signal, its band is UNKNOWN and overall becomes UNKNOWN.
    Bands are never averaged or smoothed.
    """
    detection_entry: dict[str, Any] | None = None
    if ctx.detected_format is not None:
        dr = ctx.detected_format
        detection_entry = {
            "confidence": round(dr.confidence, 3),
            "band": dr.confidence_band,
        }

    extraction_entry: dict[str, Any] | None = None
    if ctx.extracted_data is not None:
        er = ctx.extracted_data
        total = er.total_count
        parse_rate = (er.parsed_count / total) if total else 0.0
        extraction_entry = {
            "parse_rate": round(parse_rate, 4),
            "band": _parse_rate_band(parse_rate),
        }

    validation_entry: dict[str, Any] | None = None
    if ctx.validation_result is not None:
        validation_entry = {
            "band": ctx.validation_result.confidence_band,
            "summary_counts": dict(ctx.validation_result.summary_counts),
        }

    # Collect observed bands; only present signals participate.
    known_bands: list[str] = []
    if detection_entry is not None:
        known_bands.append(detection_entry["band"])
    if extraction_entry is not None:
        known_bands.append(extraction_entry["band"])
    if validation_entry is not None:
        known_bands.append(validation_entry["band"])

    if not known_bands:
        overall = "UNKNOWN"
    elif any(b not in _BAND_RANK for b in known_bands):
        # Unexpected band value — surface it rather than silently misrank.
        overall = "UNKNOWN"
    else:
        overall = min(known_bands, key=lambda b: _BAND_RANK[b])

    return {
        "detection": detection_entry,
        "extraction": extraction_entry,
        "validation": validation_entry,
        "overall_band": overall,
        "rule": "overall = weakest_band(detection, extraction, validation)",
    }


def _parse_rate_band(parse_rate: float) -> str:
    if parse_rate >= _PARSE_RATE_HIGH:
        return "HIGH"
    if parse_rate >= _PARSE_RATE_MEDIUM:
        return "MEDIUM"
    return "LOW"


def _anomaly_summary_from_list(anomaly_dicts: list[dict[str, Any]]) -> dict[str, Any]:
    by_severity: dict[str, int] = {s: 0 for s in SEVERITIES}
    by_type: dict[str, int] = {}
    by_stage: dict[str, int] = {}

    for a in anomaly_dicts:
        sev = a["severity"]
        by_severity[sev] += 1
        by_type[a["type"]] = by_type.get(a["type"], 0) + 1
        stage = a["stage"]
        by_stage[stage] = by_stage.get(stage, 0) + 1

    top_severity = "none"
    for sev in reversed(SEVERITIES):  # critical → high → medium → low
        if by_severity.get(sev, 0) > 0:
            top_severity = sev
            break

    return {
        "total": len(anomaly_dicts),
        "by_severity": by_severity,
        "by_type": dict(sorted(by_type.items())),
        "by_stage": dict(sorted(by_stage.items())),
        "top_severity": top_severity,
    }


def _make_inconsistent_failure_dict(failure: MigratorError) -> dict[str, Any]:
    """Build a meta-anomaly dict for an inconsistent failure state.

    Injected into report["anomalies"] only. ctx.anomalies is never mutated
    after the pipeline has finished running.
    """
    return {
        "type": AnomalyType.REPORT_INCONSISTENT_FAILURE.value,
        "severity": Severity.HIGH.value,
        "stage": "report",
        "message": (
            f"outcome=failure but no CRITICAL anomaly found for " f"stage={failure.stage!r}; report may be incomplete"
        ),
        "location": {
            "stage": "report",
            "source": "report_builder",
            "identifier": None,
            "record_pk": None,
            "path": None,
            "extra": {"failure_stage": failure.stage, "failure_code": failure.code},
        },
        "evidence": [
            {
                "kind": "observation",
                "detail": "expected >=1 CRITICAL anomaly for the failing stage",
                "data": {
                    "failure_stage": failure.stage,
                    "failure_code": failure.code,
                },
            }
        ],
    }
