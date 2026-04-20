"""Shared result types for the M5 validation layer.

Kept in a separate private module so structural/consistency/heuristics
can import them without creating circular dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from mempalace_migrator.core.context import AnomalyEvidence, Severity

CheckFamily = Literal["structural", "consistency", "heuristic"]
CheckStatus = Literal["passed", "failed", "inconclusive"]
SkippedReason = Literal["stage_not_implemented", "input_missing", "out_of_scope_m5"]


@dataclass(frozen=True)
class CheckOutcome:
    """Result of a single validation check."""

    id: str
    family: CheckFamily
    status: CheckStatus
    severity_on_failure: Severity
    evidence: tuple[AnomalyEvidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "family": self.family,
            "status": self.status,
            "severity_on_failure": self.severity_on_failure.value,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class CheckSkipped:
    """A check that was not performed, with an explicit reason."""

    id: str
    reason: SkippedReason

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "reason": self.reason}


@dataclass(frozen=True)
class ValidationResult:
    """Structured output of the validation stage.

    Does NOT encode a binary valid/invalid judgment. It exposes:
    - which checks were performed and their trichotomous outcome,
    - which checks were explicitly not performed and why,
    - a confidence_band derived from the worst outcome observed,
    - a count summary.

    ``confidence_band`` uses the same vocabulary as detection and
    extraction bands: HIGH / MEDIUM / LOW / UNKNOWN.
    """

    checks_performed: tuple[CheckOutcome, ...]
    checks_not_performed: tuple[CheckSkipped, ...]
    confidence_band: str  # "HIGH" | "MEDIUM" | "LOW" | "UNKNOWN"
    summary_counts: dict[str, int]  # keys: passed, failed, inconclusive

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks_performed": [c.to_dict() for c in self.checks_performed],
            "checks_not_performed": [c.to_dict() for c in self.checks_not_performed],
            "confidence_band": self.confidence_band,
            "summary_counts": dict(self.summary_counts),
        }


# --- Factory helpers (used by check modules) ---


def _make_passed(
    check_id: str,
    family: CheckFamily,
    severity_on_failure: Severity,
    evidence: AnomalyEvidence,
) -> CheckOutcome:
    return CheckOutcome(
        id=check_id,
        family=family,
        status="passed",
        severity_on_failure=severity_on_failure,
        evidence=(evidence,),
    )


def _make_failed(
    check_id: str,
    family: CheckFamily,
    severity_on_failure: Severity,
    evidence: AnomalyEvidence,
) -> CheckOutcome:
    return CheckOutcome(
        id=check_id,
        family=family,
        status="failed",
        severity_on_failure=severity_on_failure,
        evidence=(evidence,),
    )


def _make_inconclusive(
    check_id: str,
    family: CheckFamily,
    severity_on_failure: Severity,
    evidence: AnomalyEvidence,
) -> CheckOutcome:
    return CheckOutcome(
        id=check_id,
        family=family,
        status="inconclusive",
        severity_on_failure=severity_on_failure,
        evidence=(evidence,),
    )
