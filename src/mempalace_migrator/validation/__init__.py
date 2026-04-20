"""Validation layer (M5 — Safe Interpretation; M11 — Target Parity).

Goal: avoid false correctness claims. Validation observes, never judges.

Four families of checks are run against ``MigrationContext`` after extraction:

  * **Structural** — shape of objects produced by upstream stages.
  * **Consistency** — cross-checks between sub-structures (e.g. no ID
    can appear in both ``drawers`` and ``failed_rows``).
  * **Heuristic** — plausibility signals with explicit thresholds.
  * **Parity** — read-only comparison of the freshly-built target palace
    against the transformed bundle (only when reconstruction ran).

The public API is a single function ``validate(ctx) -> ValidationResult``.
``ValidationResult`` carries:

  * ``checks_performed`` — trichotomous outcomes (passed/failed/inconclusive).
  * ``checks_not_performed`` — explicit list of skipped checks with reasons.
  * ``confidence_band``  — HIGH/MEDIUM/LOW/UNKNOWN derived from worst outcome.
  * ``summary_counts``   — counts by status.

When ``ctx.reconstruction_result`` is None the five parity checks are
listed in ``checks_not_performed`` with reason ``"reconstruction_not_run"``.
"""

from __future__ import annotations

from mempalace_migrator.core.context import (AnomalyEvidence, AnomalyLocation,
                                             AnomalyType, MigrationContext,
                                             Severity)
from mempalace_migrator.validation._types import (CheckOutcome, CheckSkipped,
                                                  ValidationResult)
from mempalace_migrator.validation.consistency import run_consistency_checks
from mempalace_migrator.validation.heuristics import run_heuristic_checks
from mempalace_migrator.validation.parity import run_parity_checks
from mempalace_migrator.validation.structural import run_structural_checks

__all__ = ["validate", "ValidationResult", "CheckOutcome", "CheckSkipped"]


def _skipped_when_no_reconstruction() -> tuple[CheckSkipped, ...]:
    """Return the five parity skips emitted when reconstruction did not run."""
    return (
        CheckSkipped(id="target_record_count_parity",  reason="reconstruction_not_run"),
        CheckSkipped(id="target_id_set_parity",         reason="reconstruction_not_run"),
        CheckSkipped(id="target_document_hash_parity",  reason="reconstruction_not_run"),
        CheckSkipped(id="target_metadata_parity",       reason="reconstruction_not_run"),
        CheckSkipped(id="target_embedding_presence",    reason="reconstruction_not_run"),
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
            checks_not_performed=_skipped_when_no_reconstruction()
            + (CheckSkipped(id="all_extraction_checks", reason="input_missing"),),
            confidence_band="UNKNOWN",
            summary_counts={"passed": 0, "failed": 0, "inconclusive": 0},
        )

    outcomes: list[CheckOutcome] = []
    outcomes.extend(run_structural_checks(ctx))
    outcomes.extend(run_consistency_checks(ctx))
    outcomes.extend(run_heuristic_checks(ctx))

    if ctx.reconstruction_result is None:
        not_performed: tuple[CheckSkipped, ...] = _skipped_when_no_reconstruction()
    else:
        outcomes.extend(run_parity_checks(ctx))  # may emit anomalies; never raises
        not_performed = ()

    band = _compute_band(outcomes)
    counts = _summary_counts(outcomes)

    return ValidationResult(
        checks_performed=tuple(outcomes),
        checks_not_performed=not_performed,
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
        return "MEDIUM"

    return "HIGH"


def _summary_counts(outcomes: list[CheckOutcome]) -> dict[str, int]:
    counts: dict[str, int] = {"passed": 0, "failed": 0, "inconclusive": 0}
    for o in outcomes:
        counts[o.status] += 1
    return counts
