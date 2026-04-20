"""Internal consistency checks for M5 validation.

Cross-check sub-structures against each other (not against a target).
These checks verify M3 guarantees from outside: if extraction says it
recorded every failure in ctx.anomalies, this module verifies that claim.

Severity cap: HIGH. Consistency failures reveal contradictions inside
the system's own state and are more serious than heuristic flags.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mempalace_migrator.core.context import (AnomalyEvidence, AnomalyLocation,
                                             AnomalyType, MigrationContext,
                                             Severity)

if TYPE_CHECKING:
    from mempalace_migrator.validation._types import CheckOutcome

_STAGE = "validate"


def run_consistency_checks(ctx: MigrationContext) -> list["CheckOutcome"]:
    """Run all consistency checks and return their outcomes."""
    outcomes: list["CheckOutcome"] = []

    er = ctx.extracted_data
    if er is None:
        return outcomes

    outcomes.append(_check_unique_drawer_ids(ctx, er))
    outcomes.append(_check_ids_not_in_both_parsed_and_failed(ctx, er))
    outcomes.append(_check_failed_rows_have_anomalies(ctx, er))
    outcomes.append(_check_stage_result_coherence(ctx))

    return outcomes


# --- individual checks ----------------------------------------------------


def _check_unique_drawer_ids(ctx: MigrationContext, er: Any) -> "CheckOutcome":
    from mempalace_migrator.validation._types import _make_failed, _make_passed

    check_id = "consistency.unique_drawer_ids"

    seen: dict[str, int] = {}
    for drawer in er.drawers:
        seen[drawer.id] = seen.get(drawer.id, 0) + 1

    duplicates = {k: v for k, v in seen.items() if v > 1}

    if not duplicates:
        return _make_passed(
            check_id,
            "consistency",
            Severity.HIGH,
            AnomalyEvidence(
                kind="count",
                detail=f"{len(er.drawers)} drawers have unique IDs",
            ),
        )

    evidence = AnomalyEvidence(
        kind="observation",
        detail=f"{len(duplicates)} duplicate IDs found in parsed drawers",
        data={"duplicate_ids": {k: v for k, v in list(duplicates.items())[:10]}},
    )
    ctx.add_anomaly(
        type=AnomalyType.VALIDATION_DUPLICATE_ID_MISSED_BY_EXTRACTION,
        severity=Severity.HIGH,
        message=f"{len(duplicates)} duplicate drawer IDs survived extraction",
        location=AnomalyLocation(stage=_STAGE, source="extraction"),
        evidence=[evidence],
    )
    return _make_failed(check_id, "consistency", Severity.HIGH, evidence)


def _check_ids_not_in_both_parsed_and_failed(ctx: MigrationContext, er: Any) -> "CheckOutcome":
    from mempalace_migrator.validation._types import (_make_failed,
                                                      _make_inconclusive,
                                                      _make_passed)

    check_id = "consistency.id_not_in_both_parsed_and_failed"

    parsed_ids = {d.id for d in er.drawers}
    failed_ids = {f.embedding_id for f in er.failed_rows if f.embedding_id is not None}

    overlap = parsed_ids & failed_ids

    if not overlap:
        return _make_passed(
            check_id,
            "consistency",
            Severity.HIGH,
            AnomalyEvidence(
                kind="observation",
                detail="no ID appears in both parsed drawers and failed_rows",
            ),
        )

    evidence = AnomalyEvidence(
        kind="observation",
        detail=f"{len(overlap)} IDs appear in both parsed drawers and failed_rows",
        data={"overlapping_ids": sorted(overlap)[:10]},
    )
    ctx.add_anomaly(
        type=AnomalyType.VALIDATION_ID_PARSED_AND_FAILED,
        severity=Severity.HIGH,
        message=f"{len(overlap)} embedding IDs appear as both parsed and failed",
        location=AnomalyLocation(stage=_STAGE, source="extraction"),
        evidence=[evidence],
    )
    return _make_failed(check_id, "consistency", Severity.HIGH, evidence)


def _check_failed_rows_have_anomalies(ctx: MigrationContext, er: Any) -> "CheckOutcome":
    """Verify the M3 guarantee: every FailedRow must have a matching anomaly.

    Matching is by embedding_pk or embedding_id against anomaly location.
    Rows where both are None are skipped (inconclusive for those rows only);
    if ALL rows are unidentifiable, the whole check is inconclusive.
    """
    from mempalace_migrator.validation._types import (_make_failed,
                                                      _make_inconclusive,
                                                      _make_passed)

    check_id = "consistency.failed_row_has_anomaly"

    if not er.failed_rows:
        return _make_passed(
            check_id,
            "consistency",
            Severity.MEDIUM,
            AnomalyEvidence(kind="count", detail="no failed_rows; check trivially passed"),
        )

    # Build lookup sets from anomalies at stage=extract.
    extract_anomalies = [a for a in ctx.anomalies if a.stage == "extract"]
    anomaly_pks: set[int] = {
        a.location.record_pk for a in extract_anomalies if a.location.record_pk is not None
    }
    anomaly_ids: set[str] = {
        a.location.identifier for a in extract_anomalies if a.location.identifier is not None
    }

    unmatched: list[dict[str, Any]] = []
    skipped_unidentifiable = 0

    for row in er.failed_rows:
        if row.embedding_pk is None and row.embedding_id is None:
            skipped_unidentifiable += 1
            continue
        matched = (
            (row.embedding_pk is not None and row.embedding_pk in anomaly_pks)
            or (row.embedding_id is not None and row.embedding_id in anomaly_ids)
        )
        if not matched:
            unmatched.append(
                {
                    "embedding_pk": row.embedding_pk,
                    "embedding_id": row.embedding_id,
                    "reason_type": row.reason_type,
                }
            )

    identifiable = len(er.failed_rows) - skipped_unidentifiable

    if identifiable == 0:
        # All rows are unidentifiable — can't verify M3 guarantee.
        return _make_inconclusive(
            check_id,
            "consistency",
            Severity.MEDIUM,
            AnomalyEvidence(
                kind="observation",
                detail=f"all {len(er.failed_rows)} failed_rows have no pk/id; M3 guarantee unverifiable",
            ),
        )

    if not unmatched:
        return _make_passed(
            check_id,
            "consistency",
            Severity.MEDIUM,
            AnomalyEvidence(
                kind="count",
                detail=(
                    f"{identifiable} identifiable failed_rows all have matching extract anomalies"
                    + (
                        f" ({skipped_unidentifiable} unidentifiable skipped)"
                        if skipped_unidentifiable
                        else ""
                    )
                ),
            ),
        )

    evidence = AnomalyEvidence(
        kind="observation",
        detail=f"{len(unmatched)} failed_rows have no matching anomaly in ctx.anomalies",
        data={"unmatched_rows": unmatched[:10], "total_unmatched": len(unmatched)},
    )
    ctx.add_anomaly(
        type=AnomalyType.VALIDATION_ANOMALY_MISSING_FOR_FAILED_ROW,
        severity=Severity.MEDIUM,
        message=f"{len(unmatched)} failed_rows lack a corresponding extract anomaly",
        location=AnomalyLocation(stage=_STAGE, source="extraction"),
        evidence=[evidence],
    )
    return _make_failed(check_id, "consistency", Severity.MEDIUM, evidence)


def _check_stage_result_coherence(ctx: MigrationContext) -> "CheckOutcome":
    """If detection failed (detected_format is None), extraction must also be absent."""
    from mempalace_migrator.validation._types import _make_failed, _make_passed

    check_id = "consistency.stage_result_coherence"

    if ctx.detected_format is None and ctx.extracted_data is not None:
        evidence = AnomalyEvidence(
            kind="observation",
            detail="ctx.extracted_data is set but ctx.detected_format is None",
        )
        ctx.add_anomaly(
            type=AnomalyType.VALIDATION_STAGE_RESULT_INCONSISTENT,
            severity=Severity.HIGH,
            message="extracted_data present without detected_format; pipeline stage ordering violated",
            location=AnomalyLocation(stage=_STAGE, source="pipeline"),
            evidence=[evidence],
        )
        return _make_failed(check_id, "consistency", Severity.HIGH, evidence)

    return _make_passed(
        check_id,
        "consistency",
        Severity.HIGH,
        AnomalyEvidence(
            kind="observation",
            detail="stage result slots are mutually coherent",
        ),
    )
        check_id,
        "consistency",
        Severity.HIGH,
        AnomalyEvidence(
            kind="observation",
            detail="stage result slots are mutually coherent",
        ),
    )
