"""Structural checks for M5 validation.

Verify the *shape* of objects produced by upstream stages without
any interpretation of their contents. A structural failure means a
contract was broken by the stage that produced the object.

Severity cap: HIGH. Structural failures are serious but do not abort
the pipeline; they indicate upstream data quality problems.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mempalace_migrator.core.context import AnomalyEvidence, AnomalyLocation, AnomalyType, MigrationContext, Severity

if TYPE_CHECKING:
    from mempalace_migrator.validation._types import CheckOutcome

_STAGE = "validate"


def run_structural_checks(ctx: MigrationContext) -> list["CheckOutcome"]:
    """Run all structural checks and return their outcomes.

    Anomalies are emitted into ctx for every failed check.
    """
    from mempalace_migrator.validation._types import CheckOutcome, _make_failed, _make_passed

    outcomes: list[CheckOutcome] = []

    # --- ExtractionResult structural checks ---
    er = ctx.extracted_data
    if er is not None:
        outcomes.append(_check_extraction_arithmetic(ctx, er))
        for outcome in _check_drawer_shapes(ctx, er):
            outcomes.append(outcome)

    # --- DetectionResult structural checks ---
    dr = ctx.detected_format
    if dr is not None:
        outcomes.append(_check_detection_evidence_nonempty(ctx, dr))

    return outcomes


# --- individual checks ----------------------------------------------------


def _check_extraction_arithmetic(ctx: MigrationContext, er: Any) -> "CheckOutcome":
    from mempalace_migrator.validation._types import _make_failed, _make_passed

    check_id = "structural.extraction_arithmetic"
    expected = er.parsed_count + er.failed_count
    total = er.total_count

    if expected == total:
        return _make_passed(
            check_id,
            "structural",
            Severity.HIGH,
            AnomalyEvidence(
                kind="count",
                detail=f"total={total} == parsed={er.parsed_count} + failed={er.failed_count}",
            ),
        )

    # Arithmetic mismatch detected from outside extraction.
    evidence = AnomalyEvidence(
        kind="count",
        detail=(f"total={total} != parsed={er.parsed_count} + failed={er.failed_count}; " f"delta={total - expected}"),
        data={
            "total": total,
            "parsed": er.parsed_count,
            "failed": er.failed_count,
            "delta": total - expected,
        },
    )
    ctx.add_anomaly(
        type=AnomalyType.VALIDATION_EXTRACTION_ARITHMETIC,
        severity=Severity.HIGH,
        message=(
            f"extraction arithmetic mismatch observed by validation: "
            f"total={total} != parsed={er.parsed_count} + failed={er.failed_count}"
        ),
        location=AnomalyLocation(stage=_STAGE, source="extraction"),
        evidence=[evidence],
    )
    return _make_failed(check_id, "structural", Severity.HIGH, evidence)


def _check_drawer_shapes(ctx: MigrationContext, er: Any) -> "list[CheckOutcome]":
    from mempalace_migrator.validation._types import _make_failed, _make_passed

    check_id = "structural.drawer_shapes"
    malformed: list[dict[str, Any]] = []

    for drawer in er.drawers:
        issues: list[str] = []
        if not isinstance(drawer.id, str) or not drawer.id:
            issues.append("id is not a non-empty str")
        if not isinstance(drawer.document, str):
            issues.append(f"document is {type(drawer.document).__name__}, expected str")
        if not isinstance(drawer.metadata, dict):
            issues.append(f"metadata is {type(drawer.metadata).__name__}, expected dict")
        if issues:
            malformed.append({"id": drawer.id, "issues": issues})

    if not malformed:
        return [
            _make_passed(
                check_id,
                "structural",
                Severity.HIGH,
                AnomalyEvidence(
                    kind="count",
                    detail=f"all {len(er.drawers)} drawers have expected shape",
                ),
            )
        ]

    outcomes: list[CheckOutcome] = []
    for entry in malformed:
        evidence = AnomalyEvidence(
            kind="observation",
            detail=f"drawer id={entry['id']!r} shape issues: {'; '.join(entry['issues'])}",
            data={"drawer_id": entry["id"], "issues": entry["issues"]},
        )
        ctx.add_anomaly(
            type=AnomalyType.VALIDATION_DRAWER_MALFORMED,
            severity=Severity.HIGH,
            message=f"drawer {entry['id']!r} has unexpected shape: {'; '.join(entry['issues'])}",
            location=AnomalyLocation(
                stage=_STAGE,
                source="extraction",
                identifier=str(entry["id"]),
            ),
            evidence=[evidence],
        )
        outcomes.append(_make_failed(check_id, "structural", Severity.HIGH, evidence))

    return outcomes


def _check_detection_evidence_nonempty(ctx: MigrationContext, dr: Any) -> "CheckOutcome":
    from mempalace_migrator.validation._types import _make_failed, _make_passed

    check_id = "structural.detection_evidence_nonempty"

    if dr.evidence:
        return _make_passed(
            check_id,
            "structural",
            Severity.HIGH,
            AnomalyEvidence(
                kind="count",
                detail=f"detection evidence list has {len(dr.evidence)} entries",
            ),
        )

    evidence = AnomalyEvidence(
        kind="observation",
        detail="DetectionResult.evidence is empty; detection produced no supporting facts",
    )
    ctx.add_anomaly(
        type=AnomalyType.VALIDATION_DETECTION_EVIDENCE_EMPTY,
        severity=Severity.HIGH,
        message="detection evidence list is empty; detection claims have no support",
        location=AnomalyLocation(stage=_STAGE, source="detection"),
        evidence=[evidence],
    )
    return _make_failed(check_id, "structural", Severity.HIGH, evidence)
