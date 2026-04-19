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

    conf = report.get("confidence_summary")
    if conf:
        lines.append(f"confidence_overall: {conf.get('overall_band')}")

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
