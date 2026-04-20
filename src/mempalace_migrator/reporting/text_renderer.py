"""Render a report dict as a human-readable text summary.

Pure function: no I/O, no MigrationContext, no formatting choices
that depend on the caller. Input is always the dict produced by
build_report(); output is a deterministic multi-line string.

The CLI delegates here entirely; no rendering logic lives in cli/.
"""

from __future__ import annotations

from typing import Any


def render_text(report: dict[str, Any]) -> str:
    """Return a human-readable multi-line summary of *report*.

    Consumes only the report dict. Does not raise on missing keys;
    absent sections are silently skipped so partial reports (e.g. from
    an early-abort run) still render usefully.
    """
    lines: list[str] = []

    lines.append(f"run_id: {report.get('run_id', '?')}")
    lines.append(f"outcome: {report.get('outcome', '?')}")

    failure = report.get("failure")
    if failure:
        lines.append(f"failure: [{failure.get('stage')}] {failure.get('code')} — " f"{failure.get('summary')}")

    detection = report.get("detection")
    if detection:
        lines.append(
            f"detection: {detection.get('classification')} "
            f"(confidence={detection.get('confidence')}, "
            f"band={detection.get('confidence_band')}, "
            f"source_version={detection.get('source_version')})"
        )

    stats = report.get("extraction_stats")
    if stats:
        lines.append(
            f"extraction_stats: total={stats.get('total_rows')} "
            f"parsed={stats.get('parsed_rows')} "
            f"failed={stats.get('failed_rows')} "
            f"rate={stats.get('parse_rate')}"
        )

    transformation = report.get("transformation")
    if transformation:
        lines.append(
            f"transformation: drawers={transformation.get('drawer_count')} "
            f"dropped={transformation.get('dropped_count')} "
            f"coerced={transformation.get('coerced_count')} "
            f"metadata_keys={len(transformation.get('metadata_keys', []))}"
        )

    reconstruction = report.get("reconstruction")
    if reconstruction:
        lines.append(
            f"reconstruction: imported={reconstruction.get('imported_count')} "
            f"collection={reconstruction.get('collection_name')!r} "
            f"chromadb={reconstruction.get('chromadb_version')} "
            f"target={reconstruction.get('target_path')}"
        )

    conf = report.get("confidence_summary")
    if conf:
        lines.append(f"confidence_overall: {conf.get('overall_band')}")

    validation = report.get("validation")
    if validation:
        vband = validation.get("confidence_band", "?")
        counts = validation.get("summary_counts", {})
        lines.append(
            f"validation: band={vband} "
            f"passed={counts.get('passed', 0)} "
            f"failed={counts.get('failed', 0)} "
            f"inconclusive={counts.get('inconclusive', 0)}"
        )
        for check in validation.get("checks_performed", []):
            if check.get("status") != "passed":
                lines.append(
                    f"  check/{check.get('id')}: {check.get('status')} "
                    f"(severity_on_failure={check.get('severity_on_failure')})"
                )
        skipped = validation.get("checks_not_performed", [])
        if skipped:
            lines.append(f"  checks_not_performed: {len(skipped)} ({', '.join(c.get('id','?') for c in skipped)})")

    stages = report.get("stages") or {}
    for stage, info in stages.items():
        status = info.get("status", "?")
        reason = info.get("skipped_reason")
        suffix = f" ({reason})" if reason else ""
        lines.append(f"  stage/{stage}: {status}{suffix}")

    summary = report.get("anomaly_summary") or {}
    if summary:
        by_sev = summary.get("by_severity", {})
        lines.append(
            f"anomalies: total={summary.get('total', 0)} "
            f"critical={by_sev.get('critical', 0)} "
            f"high={by_sev.get('high', 0)} "
            f"medium={by_sev.get('medium', 0)} "
            f"low={by_sev.get('low', 0)}"
        )
        top = summary.get("top_severity")
        if top and top != "none":
            lines.append(f"  top_severity: {top}")

    for a in report.get("anomalies", []):
        lines.append(f"  - [{a.get('severity')}/{a.get('stage')}/{a.get('type')}] " f"{a.get('message')}")

    enc = report.get("explicitly_not_checked", [])
    lines.append(f"explicitly_not_checked: {len(enc)} items")

    return "\n".join(lines)
