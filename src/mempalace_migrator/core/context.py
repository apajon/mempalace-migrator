"""Central state container for a migration run.

This module also defines the **Truth Model (M3)**: the structured
representation of every inconsistency the system observes.

Truth model invariants:

  * Every anomaly carries a strongly-typed ``AnomalyType``. Free-form
    type strings are rejected. New types must be registered in the
    ``AnomalyType`` enum.
  * Every anomaly carries a strongly-typed ``Severity``. Invalid values
    are rejected at construction time.
  * Every anomaly carries an ``AnomalyLocation`` whose ``stage`` is
    non-empty. Locations are first-class values, not free-form strings
    embedded in a message.
  * Every anomaly carries at least one ``AnomalyEvidence`` entry. An
    anomaly without evidence is rejected.

Together these rules guarantee that ``ctx.anomalies`` is the *single*
structured channel through which inconsistencies leave the system.
There are no free-form logs, no hidden errors, and no ambiguous
warnings: if the system observed something, it is in here, with type +
severity + location + evidence attached.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

# --- Severity --------------------------------------------------------------


class Severity(str, Enum):
    """Severity of an anomaly. Subclassing ``str`` keeps backward-compat
    with consumers that compare ``a.severity == "high"``.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Public, stable iteration order (low -> critical). Some consumers (e.g.
# report_builder) rely on it.
SEVERITIES: tuple[str, ...] = tuple(s.value for s in Severity)


def _coerce_severity(value: "Severity | str") -> Severity:
    if isinstance(value, Severity):
        return value
    try:
        return Severity(value)
    except ValueError as exc:
        raise ValueError(f"invalid severity {value!r}; expected one of {SEVERITIES}") from exc


# --- AnomalyType registry --------------------------------------------------


class AnomalyType(str, Enum):
    """Closed registry of every anomaly type the system can emit.

    Adding a new anomaly type to the system requires adding a member
    here. This is intentional: it prevents free-form anomaly logs from
    leaking into the truth model.
    """

    # --- Extraction: per-row failures ---
    BLANK_EMBEDDING_ID = "blank_embedding_id"
    CONTROL_CHARS_IN_ID = "control_chars_in_id"
    DUPLICATE_EMBEDDING_IDS = "duplicate_embedding_ids"
    METADATA_QUERY_FAILED = "metadata_query_failed"
    ORPHAN_EMBEDDING = "orphan_embedding"
    DOCUMENT_STRING_VALUE_NULL = "document_string_value_null"
    METADATA_ALL_NULL = "metadata_all_null"
    DOCUMENT_MISSING = "document_missing"
    DOCUMENT_MULTIPLE = "document_multiple"
    DUPLICATE_METADATA_KEYS = "duplicate_metadata_keys"

    # --- Extraction: scan-level ---
    EMBEDDINGS_SCAN_ABORTED = "embeddings_scan_aborted"
    EXTRACTION_ARITHMETIC_MISMATCH = "extraction_arithmetic_mismatch"

    # --- Extraction: pre-flight critical errors (raised + recorded) ---
    SQLITE_MISSING = "sqlite_missing"
    WAL_NOT_CHECKPOINTED = "wal_not_checkpointed"
    SQLITE_OPEN_FAILED = "sqlite_open_failed"
    PRAGMA_INTEGRITY_CHECK_FAILED_TO_RUN = "pragma_integrity_check_failed_to_run"
    SQLITE_INTEGRITY_CHECK_FAILED = "sqlite_integrity_check_failed"
    SQLITE_MASTER_UNREADABLE = "sqlite_master_unreadable"
    REQUIRED_TABLES_MISSING = "required_tables_missing"
    COLLECTIONS_QUERY_FAILED = "collections_query_failed"
    NO_COLLECTION = "no_collection"
    MULTIPLE_COLLECTIONS = "multiple_collections"
    UNEXPECTED_COLLECTION_NAME = "unexpected_collection_name"
    EMBEDDINGS_QUERY_FAILED = "embeddings_query_failed"
    EMBEDDINGS_SCAN_FAILED = "embeddings_scan_failed"
    EMBEDDINGS_ITER_FAILED = "embeddings_iter_failed"

    # --- Detection / pipeline gate ---
    UNSUPPORTED_SOURCE_FORMAT = "unsupported_source_format"
    INSUFFICIENT_DETECTION_CONFIDENCE = "insufficient_detection_confidence"
    UNSUPPORTED_VERSION = "unsupported_version"

    # --- Stub stages ---
    NOT_IMPLEMENTED = "not_implemented"

    # --- Reporting meta ---
    REPORT_INCONSISTENT_FAILURE = "report_inconsistent_failure"


def _coerce_type(value: "AnomalyType | str") -> AnomalyType:
    if isinstance(value, AnomalyType):
        return value
    try:
        return AnomalyType(value)
    except ValueError as exc:
        raise ValueError(f"unknown anomaly type {value!r}; register it in AnomalyType") from exc


# --- Location & Evidence --------------------------------------------------


@dataclass(frozen=True)
class AnomalyLocation:
    """Where in the system the anomaly was observed.

    ``stage`` is required and identifies the pipeline step
    ('detect', 'extract', 'transform', 'reconstruct', 'validate',
    'report'). The remaining fields are optional but at least one of
    them should be set when the anomaly is bound to a specific record,
    file, or substrate component, so that the consumer can locate it
    without re-deriving the position from the message string.
    """

    stage: str
    source: str | None = None  # logical source: 'embeddings', 'manifest', 'filesystem', ...
    identifier: str | None = None  # human-meaningful id, e.g. embedding_id
    record_pk: int | None = None  # primary key in the underlying store
    path: str | None = None  # file path (when relevant)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.stage, str) or not self.stage.strip():
            raise ValueError("AnomalyLocation.stage must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "source": self.source,
            "identifier": self.identifier,
            "record_pk": self.record_pk,
            "path": self.path,
            "extra": dict(self.extra),
        }


@dataclass(frozen=True)
class AnomalyEvidence:
    """A single piece of evidence supporting an anomaly.

    ``kind`` is a short stable tag describing the nature of the
    evidence ('observation', 'sqlite_error', 'count', 'sample',
    'value', 'config_value', ...). ``detail`` is a human-readable
    string. ``data`` carries machine-readable payload. The ``data``
    dict is intentionally JSON-safe by contract.
    """

    kind: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("AnomalyEvidence.kind must be a non-empty string")
        if not isinstance(self.detail, str):
            raise ValueError("AnomalyEvidence.detail must be a string")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail, "data": dict(self.data)}


# --- Anomaly ---------------------------------------------------------------


@dataclass(frozen=True)
class Anomaly:
    """Structured anomaly. The dicts in ``location.extra`` and in each
    ``AnomalyEvidence.data`` are JSON-safe by contract.

    Backward-compatible accessors:
      * ``a.stage``    -> ``a.location.stage``
      * ``a.context``  -> merged read-only dict view of location and
                          evidence payloads, for consumers that just
                          want a flat dict. The structured truth lives
                          in ``a.location`` and ``a.evidence``.
    """

    type: AnomalyType
    severity: Severity
    message: str
    location: AnomalyLocation
    evidence: tuple[AnomalyEvidence, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.type, AnomalyType):
            raise TypeError(f"Anomaly.type must be AnomalyType, got {type(self.type).__name__}")
        if not isinstance(self.severity, Severity):
            raise TypeError(f"Anomaly.severity must be Severity, got {type(self.severity).__name__}")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("Anomaly.message must be a non-empty string")
        if not isinstance(self.location, AnomalyLocation):
            raise TypeError("Anomaly.location must be an AnomalyLocation")
        if not isinstance(self.evidence, tuple):
            raise TypeError("Anomaly.evidence must be a tuple")
        if len(self.evidence) == 0:
            raise ValueError("Anomaly.evidence must contain at least one entry")
        for ev in self.evidence:
            if not isinstance(ev, AnomalyEvidence):
                raise TypeError("Anomaly.evidence entries must be AnomalyEvidence, got " f"{type(ev).__name__}")

    @property
    def stage(self) -> str:
        return self.location.stage

    @property
    def context(self) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        merged.update(self.location.extra)
        if self.location.identifier is not None:
            merged.setdefault("embedding_id", self.location.identifier)
        if self.location.record_pk is not None:
            merged.setdefault("embedding_pk", self.location.record_pk)
        if self.location.path is not None:
            merged.setdefault("path", self.location.path)
        if self.location.source is not None:
            merged.setdefault("source", self.location.source)
        for ev in self.evidence:
            for k, v in ev.data.items():
                merged[k] = v
        return merged

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "severity": self.severity.value,
            "stage": self.location.stage,
            "message": self.message,
            "location": self.location.to_dict(),
            "evidence": [e.to_dict() for e in self.evidence],
        }


def _coerce_evidence(
    evidence: "Iterable[AnomalyEvidence] | AnomalyEvidence",
) -> tuple[AnomalyEvidence, ...]:
    if isinstance(evidence, AnomalyEvidence):
        return (evidence,)
    return tuple(evidence)


# --- MigrationContext -----------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class MigrationContext:
    source_path: Path
    target_path: Path | None = None

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str = field(default_factory=_utc_now_iso)

    detected_format: Any = None  # DetectionResult
    extracted_data: Any = None  # ExtractionResult
    transformed_data: Any = None  # stub
    reconstruction_result: Any = None  # stub
    validation_result: Any = None  # stub

    anomalies: list[Anomaly] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)

    def add_anomaly(
        self,
        *,
        type: "AnomalyType | str",
        severity: "Severity | str",
        message: str,
        location: AnomalyLocation | None = None,
        evidence: "Sequence[AnomalyEvidence] | AnomalyEvidence | None" = None,
        # --- legacy keyword params (transitional) ---
        stage: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> Anomaly:
        """Record a structured anomaly. Returns the created ``Anomaly``.

        Two call shapes are accepted:

          * Structured (preferred):
              ``add_anomaly(type=..., severity=..., message=...,
                            location=AnomalyLocation(...),
                            evidence=[AnomalyEvidence(...)])``

          * Legacy (transitional): pass ``stage`` and ``context``; the
            method synthesizes a minimal location and a single evidence
            entry from them. This shape exists only so that legacy
            call sites keep working while they are migrated; structural
            invariants are still enforced.

        In both shapes:
          * ``type`` must be a registered ``AnomalyType``;
          * ``severity`` must be a valid ``Severity``;
          * ``message`` must be non-empty;
          * the resulting ``Anomaly`` always has a non-empty stage
            and at least one evidence entry.
        """
        coerced_type = _coerce_type(type)
        coerced_severity = _coerce_severity(severity)

        # --- resolve location ---
        if location is None:
            if stage is None:
                raise ValueError("add_anomaly requires either `location=` or `stage=`")
            location = AnomalyLocation(stage=stage, extra=dict(context or {}))
        elif stage is not None and stage != location.stage:
            raise ValueError(f"stage={stage!r} disagrees with location.stage={location.stage!r}")

        # --- resolve evidence ---
        if evidence is None:
            evidence_tuple: tuple[AnomalyEvidence, ...] = (
                AnomalyEvidence(
                    kind="legacy_context",
                    detail=message,
                    data=dict(context or {}),
                ),
            )
        else:
            evidence_tuple = _coerce_evidence(evidence)

        anomaly = Anomaly(
            type=coerced_type,
            severity=coerced_severity,
            message=message,
            location=location,
            evidence=evidence_tuple,
        )
        self.anomalies.append(anomaly)
        return anomaly

    @property
    def short_run_id(self) -> str:
        return self.run_id[:8]
