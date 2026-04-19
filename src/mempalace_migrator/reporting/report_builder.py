"""Build a structured report from a MigrationContext.

The report is JSON-serialisable. It includes:
  - extraction_stats (total/parsed/failed)
  - anomaly_summary (counts by severity)
  - detection_evidence (full list)
  - explicitly_not_checked (mandatory disclaimer list)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mempalace_migrator.core.context import SEVERITIES, MigrationContext
from mempalace_migrator.core.errors import MigratorError
from mempalace_migrator.detection.format_detector import \
    SUPPORTED_VERSION_PAIRS

REPORT_SCHEMA_VERSION = 2
TOOL_VERSION = "0.1.0"

EXPLICITLY_NOT_CHECKED: tuple[str, ...] = (
    "sqlite_corruption_below_pragma_level",
    "embedding_vector_equivalence_source_to_target",
    "search_result_semantic_equivalence",
    "concurrent_access_absence_during_extraction",
    "target_chromadb_default_embedding_function_match",
    "hnsw_segment_file_integrity",
    "manifest_authenticity",
)


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_report(
    ctx: MigrationContext, *, failure: MigratorError | None = None
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "supported_version_pairs": [
            {"source": s, "target": t} for s, t in SUPPORTED_VERSION_PAIRS
        ],
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
        "transformation": None,
        "reconstruction": None,
        "validation": None,
        "anomalies": [a.to_dict() for a in ctx.anomalies],
        "anomaly_summary": _anomaly_summary(ctx),
        "explicitly_not_checked": list(EXPLICITLY_NOT_CHECKED),
    }


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


def _anomaly_summary(ctx: MigrationContext) -> dict[str, Any]:
    by_severity: dict[str, int] = {s: 0 for s in SEVERITIES}
    by_type: dict[str, int] = {}
    for a in ctx.anomalies:
        by_severity[a.severity] += 1
        by_type[a.type] = by_type.get(a.type, 0) + 1
    return {
        "total": len(ctx.anomalies),
        "by_severity": by_severity,
        "by_type": dict(sorted(by_type.items())),
    }
