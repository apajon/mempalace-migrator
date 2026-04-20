"""Exception hierarchy.

Every exception carries a stage and a stable code so the report builder
can render it without string-parsing.
"""

from __future__ import annotations


class MigratorError(Exception):
    """Base for all migrator errors raised by pipeline steps."""

    def __init__(
        self,
        *,
        stage: str,
        code: str,
        summary: str,
        details: list[str] | None = None,
    ) -> None:
        super().__init__(summary)
        self.stage = stage
        self.code = code
        self.summary = summary
        self.details = details or []

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "code": self.code,
            "summary": self.summary,
            "details": list(self.details),
        }


class DetectionError(MigratorError):
    """Raised by the detection layer when the palace cannot be inspected."""


class ExtractionError(MigratorError):
    """Raised by the extraction layer on any structural or data anomaly."""


class TransformError(MigratorError):
    """Raised by the transformation layer on unrecoverable input failure."""


class ReconstructionError(MigratorError):
    """Raised by the reconstruction layer on any write failure."""


class PipelineAbort(MigratorError):
    """Raised by pipeline orchestration when a precondition fails."""
