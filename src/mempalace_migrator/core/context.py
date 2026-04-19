"""Central state container for a migration run.

MigrationContext is a dumb holder. It carries structured anomalies,
soft-failure tracking, and a final report dict.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

Severity = Literal["low", "medium", "high", "critical"]
SEVERITIES: tuple[Severity, ...] = ("low", "medium", "high", "critical")


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class Anomaly:
    """Structured anomaly. The dict in `context` is JSON-safe by contract."""

    type: str
    severity: Severity
    message: str
    stage: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "severity": self.severity,
            "stage": self.stage,
            "message": self.message,
            "context": dict(self.context),
        }


@dataclass
class MigrationContext:
    source_path: Path
    target_path: Path | None = None

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str = field(default_factory=_utc_now_iso)

    detected_format: Any = None        # DetectionResult
    extracted_data: Any = None         # ExtractionResult
    transformed_data: Any = None       # stub
    reconstruction_result: Any = None  # stub
    validation_result: Any = None      # stub

    anomalies: list[Anomaly] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)

    def add_anomaly(
        self,
        *,
        type: str,
        severity: Severity,
        message: str,
        stage: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        if severity not in SEVERITIES:
            raise ValueError(f"invalid severity {severity!r}")
        self.anomalies.append(
            Anomaly(
                type=type,
                severity=severity,
                stage=stage,
                message=message,
                context=dict(context or {}),
            )
        )

    @property
    def short_run_id(self) -> str:
        return self.run_id[:8]
