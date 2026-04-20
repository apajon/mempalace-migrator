"""Heuristic plausibility checks for M5 validation.

Every heuristic must declare its threshold as a module-level constant.
Every emitted anomaly must include the threshold in its evidence.data.

Severity cap: MEDIUM. Heuristics are faillible by construction; they
flag suspicious patterns, not definitive errors. They never produce
HIGH or CRITICAL anomalies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mempalace_migrator.core.context import AnomalyEvidence, AnomalyLocation, AnomalyType, MigrationContext, Severity

if TYPE_CHECKING:
    from mempalace_migrator.validation._types import CheckOutcome

_STAGE = "validate"

# Heuristic thresholds — documented, stable, explicit.
PARSE_RATE_PLAUSIBILITY_FLOOR: float = 0.50
"""Below this parse_rate the extraction result is suspicious enough to flag."""

DOMINANT_FAILURE_TYPE_THRESHOLD: float = 0.80
"""If one failure reason_type accounts for > this share of failed_rows,
flag it as a potentially systemic failure mode (not random noise)."""


def run_heuristic_checks(ctx: MigrationContext) -> list["CheckOutcome"]:
    """Run all heuristic checks and return their outcomes."""
    outcomes: list["CheckOutcome"] = []

    er = ctx.extracted_data
    if er is None:
        return outcomes

    outcomes.append(_check_parse_rate_plausible(ctx, er))
    outcomes.append(_check_empty_source(ctx, er))
    if er.failed_rows:
        outcomes.append(_check_dominant_failure_type(ctx, er))

    return outcomes


# --- individual checks ----------------------------------------------------


def _check_parse_rate_plausible(ctx: MigrationContext, er: Any) -> "CheckOutcome":
    from mempalace_migrator.validation._types import _make_failed, _make_passed

    check_id = "heuristic.parse_rate_plausible"
    total = er.total_count
    parse_rate = (er.parsed_count / total) if total else 0.0

    if parse_rate >= PARSE_RATE_PLAUSIBILITY_FLOOR:
        return _make_passed(
            check_id,
            "heuristic",
            Severity.MEDIUM,
            AnomalyEvidence(
                kind="observation",
                detail=(f"parse_rate={parse_rate:.4f} >= floor={PARSE_RATE_PLAUSIBILITY_FLOOR}"),
            ),
        )

    evidence = AnomalyEvidence(
        kind="observation",
        detail=(
            f"parse_rate={parse_rate:.4f} < plausibility floor {PARSE_RATE_PLAUSIBILITY_FLOOR}; "
            f"majority of rows could not be parsed"
        ),
        data={
            "parse_rate": round(parse_rate, 4),
            "threshold": PARSE_RATE_PLAUSIBILITY_FLOOR,
            "parsed": er.parsed_count,
            "total": total,
        },
    )
    ctx.add_anomaly(
        type=AnomalyType.VALIDATION_PARSE_RATE_IMPLAUSIBLE,
        severity=Severity.MEDIUM,
        message=(
            f"parse_rate={parse_rate:.4f} is below plausibility floor "
            f"{PARSE_RATE_PLAUSIBILITY_FLOOR}; extraction quality is suspect"
        ),
        location=AnomalyLocation(stage=_STAGE, source="extraction"),
        evidence=[evidence],
    )
    return _make_failed(check_id, "heuristic", Severity.MEDIUM, evidence)


def _check_empty_source(ctx: MigrationContext, er: Any) -> "CheckOutcome":
    from mempalace_migrator.validation._types import _make_failed, _make_passed

    check_id = "heuristic.empty_source"

    if er.total_count > 0:
        return _make_passed(
            check_id,
            "heuristic",
            Severity.MEDIUM,
            AnomalyEvidence(
                kind="count",
                detail=f"source has {er.total_count} rows; not empty",
            ),
        )

    evidence = AnomalyEvidence(
        kind="count",
        detail="source has 0 rows; extraction succeeded on an empty collection",
        data={"total_count": 0},
    )
    ctx.add_anomaly(
        type=AnomalyType.VALIDATION_EMPTY_SOURCE,
        severity=Severity.MEDIUM,
        message="source collection contains 0 rows; extraction produced nothing",
        location=AnomalyLocation(stage=_STAGE, source="extraction"),
        evidence=[evidence],
    )
    return _make_failed(check_id, "heuristic", Severity.MEDIUM, evidence)


def _check_dominant_failure_type(ctx: MigrationContext, er: Any) -> "CheckOutcome":
    from mempalace_migrator.validation._types import _make_failed, _make_passed

    check_id = "heuristic.dominant_failure_type"
    total_failed = len(er.failed_rows)

    counts: dict[str, int] = {}
    for row in er.failed_rows:
        counts[row.reason_type] = counts.get(row.reason_type, 0) + 1

    dominant_type = max(counts, key=lambda k: counts[k])
    dominant_share = counts[dominant_type] / total_failed

    if dominant_share <= DOMINANT_FAILURE_TYPE_THRESHOLD:
        return _make_passed(
            check_id,
            "heuristic",
            Severity.MEDIUM,
            AnomalyEvidence(
                kind="observation",
                detail=(
                    f"most common failure type '{dominant_type}' accounts for "
                    f"{dominant_share:.2%} <= threshold {DOMINANT_FAILURE_TYPE_THRESHOLD:.2%}"
                ),
            ),
        )

    evidence = AnomalyEvidence(
        kind="observation",
        detail=(
            f"failure type '{dominant_type}' accounts for {dominant_share:.2%} of "
            f"{total_failed} failed rows; likely a systemic failure mode"
        ),
        data={
            "dominant_type": dominant_type,
            "dominant_count": counts[dominant_type],
            "total_failed": total_failed,
            "dominant_share": round(dominant_share, 4),
            "threshold": DOMINANT_FAILURE_TYPE_THRESHOLD,
            "all_counts": dict(sorted(counts.items())),
        },
    )
    ctx.add_anomaly(
        type=AnomalyType.VALIDATION_DOMINANT_FAILURE_TYPE,
        severity=Severity.MEDIUM,
        message=(
            f"failure type '{dominant_type}' dominates ({dominant_share:.2%}) "
            f"failed_rows; possible systemic failure mode"
        ),
        location=AnomalyLocation(stage=_STAGE, source="extraction"),
        evidence=[evidence],
    )
    return _make_failed(check_id, "heuristic", Severity.MEDIUM, evidence)
