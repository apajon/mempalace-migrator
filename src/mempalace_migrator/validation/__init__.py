"""Validation layer (M5 — Safe Interpretation).

Goal: avoid false correctness claims. Validation observes, never judges.

Three families of checks are run against ``MigrationContext`` after
extraction:

  * **Structural** — shape of objects produced by upstream stages.
  * **Consistency** — cross-checks between sub-structures (e.g. no ID
    can appear in both ``drawers`` and ``failed_rows``).
  * **Heuristic** — plausibility signals with explicit thresholds.

The public API is a single function ``validate(ctx) -> ValidationResult``.
``ValidationResult`` carries:

  * ``checks_performed`` — trichotomous outcomes (passed/failed/inconclusive).
  * ``checks_not_performed`` — explicit list of skipped checks with reasons.
  * ``confidence_band``  — HIGH/MEDIUM/LOW/UNKNOWN derived from worst outcome.
  * ``summary_counts``   — counts by status.

The following checks are explicitly *not* performed here (reconstruction stub):

  * ``target_record_count_parity``
  * ``target_id_set_parity``

These remain in ``EXPLICITLY_NOT_CHECKED`` in report_builder until the
reconstruction stage is implemented.
"""

from __future__ import annotations

from mempalace_migrator.core.context import AnomalyEvidence, AnomalyLocation, AnomalyType, MigrationContext, Severity
from mempalace_migrator.validation._types import CheckOutcome, CheckSkipped, ValidationResult
from mempalace_migrator.validation.consistency import run_consistency_checks
from mempalace_migrator.validation.heuristics import run_heuristic_checks
from mempalace_migrator.validation.structural import run_structural_checks

__all__ = ["validate", "ValidationResult", "CheckOutcome", "CheckSkipped"]

# Checks not performed because reconstruction is a stub.
_SKIPPED_RECONSTRUCTION: tuple[CheckSkipped, ...] = (
    CheckSkipped(id="target_record_count_parity", reason="stage_not_implemented"),
    CheckSkipped(id="target_id_set_parity", reason="stage_not_implemented"),
)

# Band ranks for weakest-band rule (lower = worse). UNKNOWN is absent → forces UNKNOWN.
_BAND_RANK: dict[str, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

_STAGE = "validate"


def validate(ctx: MigrationContext) -> ValidationResult:
    """Run all validation checks against the current context.

    If ``ctx.extracted_data`` is None (extraction did not run), returns
    a ValidationResult with no checks performed and UNKNOWN band. No
    anomaly is emitted in this case — the pipeline step handles that.

    Never raises ``MigratorError``. Validation cannot abort the pipeline.
    """
    if ctx.extracted_data is None:
        return ValidationResult(
            checks_performed=(),
            checks_not_performed=_SKIPPED_RECONSTRUCTION
            + (CheckSkipped(id="all_extraction_checks", reason="input_missing"),),
            confidence_band="UNKNOWN",
            summary_counts={"passed": 0, "failed": 0, "inconclusive": 0},
        )

    outcomes: list[CheckOutcome] = []
    outcomes.extend(run_structural_checks(ctx))
    outcomes.extend(run_consistency_checks(ctx))
    outcomes.extend(run_heuristic_checks(ctx))

    band = _compute_band(outcomes)
    counts = _summary_counts(outcomes)

    return ValidationResult(
        checks_performed=tuple(outcomes),
        checks_not_performed=_SKIPPED_RECONSTRUCTION,
        confidence_band=band,
        summary_counts=counts,
    )


def _compute_band(outcomes: list[CheckOutcome]) -> str:
    """Derive confidence_band from the worst outcome observed.

    Rules (evaluated in priority order):
      * No outcomes at all           → UNKNOWN
      * Any failed HIGH              → LOW
      * Any failed MEDIUM or inconclusive → MEDIUM
      * Otherwise                    → HIGH
    """
    if not outcomes:
        return "UNKNOWN"

    has_failed_high = any(o.status == "failed" and o.severity_on_failure == Severity.HIGH for o in outcomes)
    if has_failed_high:
        return "LOW"

    has_failed_medium_or_inconclusive = any(
        (o.status == "failed" and o.severity_on_failure == Severity.MEDIUM) or o.status == "inconclusive"
        for o in outcomes
    )
    if has_failed_medium_or_inconclusive:
        return "MEDIUM"

    return "HIGH"


def _summary_counts(outcomes: list[CheckOutcome]) -> dict[str, int]:
    counts: dict[str, int] = {"passed": 0, "failed": 0, "inconclusive": 0}
    for o in outcomes:
        counts[o.status] += 1
    return counts
