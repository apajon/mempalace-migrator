"""Transformation orchestrator — the single entry point for the stage.

transform(ctx) reads ctx.extracted_data.drawers, applies per-drawer
normalisation, emits structured anomalies for every drop or coercion, and
stores a TransformedBundle on ctx.transformed_data.

Contracts:
  - Pure function: no I/O, no chromadb import, no sqlite3 access, no
    filesystem operations. Enforced by tests/test_transformation_purity.py.
  - Per-drawer first-failure-wins: exactly one anomaly per dropped drawer.
  - Deterministic: output follows input drawer order; summaries are sorted.
  - Does NOT raise on per-drawer failure; only raises TransformError on
    unrecoverable input (extracted_data is None).

This module imports nothing outside the standard library and project
modules (no chromadb, no sqlite3, no os, no pathlib, no shutil, no
tempfile).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from mempalace_migrator.core.context import AnomalyEvidence, AnomalyLocation, AnomalyType, MigrationContext, Severity
from mempalace_migrator.core.errors import TransformError
from mempalace_migrator.extraction.chroma_06_reader import EXPECTED_COLLECTION_NAME
from mempalace_migrator.transformation._analyze import build_summary
from mempalace_migrator.transformation._normalize import normalize_metadata
from mempalace_migrator.transformation._types import TransformedBundle, TransformedDrawer

if TYPE_CHECKING:
    from mempalace_migrator.extraction.chroma_06_reader import DrawerRecord

_STAGE = "transform"
_ID_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def transform(ctx: MigrationContext) -> TransformedBundle:
    """Normalise extracted drawers into a TransformedBundle.

    Raises TransformError if extracted_data is None (unrecoverable).
    All per-drawer failures are anomalies, never raises.
    """
    if ctx.extracted_data is None:
        _emit_critical(ctx, AnomalyType.TRANSFORM_INPUT_MISSING, "extracted_data is None")
        raise TransformError(
            stage=_STAGE,
            code="transform_input_missing",
            summary="extraction did not produce a result; cannot transform",
        )

    raw_drawers = ctx.extracted_data.drawers
    accepted: list[TransformedDrawer] = []
    seen_ids: set[str] = set()
    dropped = 0
    coerced_ids: set[str] = set()

    for drawer in raw_drawers:
        result = _process_drawer(ctx, drawer, seen_ids)
        if result is None:
            dropped += 1
            continue
        td, was_coerced = result
        accepted.append(td)
        seen_ids.add(td.id)
        if was_coerced:
            coerced_ids.add(td.id)

    summary = build_summary(
        accepted,
        dropped_count=dropped,
        coerced_count=len(coerced_ids),
    )

    bundle = TransformedBundle(
        collection_name=EXPECTED_COLLECTION_NAME,
        collection_metadata={},
        drawers=tuple(accepted),
        summary=summary,
    )
    ctx.transformed_data = bundle
    return bundle


# ---------------------------------------------------------------------------
# Per-drawer processing
# ---------------------------------------------------------------------------


def _process_drawer(
    ctx: MigrationContext,
    drawer: "DrawerRecord",
    seen_ids: set[str],
) -> "tuple[TransformedDrawer, bool] | None":
    """Validate and normalise a single drawer.

    Returns (TransformedDrawer, was_coerced) on success, None on drop.
    Emits exactly one anomaly when dropping.
    """
    # 1. id must be a non-empty string with no control characters
    if not isinstance(drawer.id, str) or not drawer.id.strip():
        _drop(ctx, drawer, "invalid_id", "drawer id is not a non-empty string")
        return None
    if _ID_CONTROL_CHARS_RE.search(drawer.id):
        _drop(ctx, drawer, "invalid_id", "drawer id contains control characters")
        return None

    # 2. document must be a non-empty string
    if not isinstance(drawer.document, str) or not drawer.document:
        _drop(ctx, drawer, "invalid_document", "document is not a non-empty string")
        return None

    # 3. metadata normalisation (checks for non-str keys, bad value types)
    norm_meta, drop_reason, coercions = normalize_metadata(drawer.metadata)
    if drop_reason is not None:
        _drop(ctx, drawer, drop_reason, f"metadata rejected: {drop_reason}")
        return None

    # Emit MEDIUM anomaly for each coercion before accepting
    for coercion in coercions:
        ctx.add_anomaly(
            type=AnomalyType.TRANSFORM_METADATA_COERCED,
            severity=Severity.MEDIUM,
            message=(f"drawer id={drawer.id!r}: metadata key {coercion['key']!r} " f"coerced ({coercion['reason']})"),
            location=AnomalyLocation(
                stage=_STAGE,
                source="metadata",
                identifier=drawer.id,
            ),
            evidence=[
                AnomalyEvidence(
                    kind="coercion",
                    detail=(
                        f"key={coercion['key']!r} "
                        f"original={coercion['original_repr']} "
                        f"new={coercion['new_value']!r}"
                    ),
                    data={
                        "key": coercion["key"],
                        "original_repr": coercion["original_repr"],
                        "new_value": coercion["new_value"],
                        "reason": coercion["reason"],
                        "drawer_id": drawer.id,
                    },
                )
            ],
        )

    # 4. Duplicate id check (after normalisation, against ids already accepted)
    if drawer.id in seen_ids:
        ctx.add_anomaly(
            type=AnomalyType.TRANSFORM_DUPLICATE_ID_DROPPED,
            severity=Severity.HIGH,
            message=f"drawer id={drawer.id!r}: duplicate id; later occurrence dropped",
            location=AnomalyLocation(
                stage=_STAGE,
                source="drawers",
                identifier=drawer.id,
            ),
            evidence=[
                AnomalyEvidence(
                    kind="observation",
                    detail="id already present in accepted set; this occurrence dropped",
                    data={"drawer_id": drawer.id},
                )
            ],
        )
        return None

    was_coerced = len(coercions) > 0
    return TransformedDrawer(id=drawer.id, document=drawer.document, metadata=norm_meta), was_coerced


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drop(
    ctx: MigrationContext,
    drawer: "DrawerRecord",
    reason: str,
    detail: str,
) -> None:
    """Emit exactly one TRANSFORM_DRAWER_DROPPED/HIGH anomaly for a drawer."""
    drawer_id = drawer.id if isinstance(drawer.id, str) else repr(drawer.id)
    ctx.add_anomaly(
        type=AnomalyType.TRANSFORM_DRAWER_DROPPED,
        severity=Severity.HIGH,
        message=f"drawer id={drawer_id!r}: dropped — {reason}",
        location=AnomalyLocation(
            stage=_STAGE,
            source="drawers",
            identifier=drawer_id,
        ),
        evidence=[
            AnomalyEvidence(
                kind="observation",
                detail=detail,
                data={"drawer_id": drawer_id, "reason": reason},
            )
        ],
    )


def _emit_critical(ctx: MigrationContext, anomaly_type: AnomalyType, detail: str) -> None:
    ctx.add_anomaly(
        type=anomaly_type,
        severity=Severity.CRITICAL,
        message=detail,
        location=AnomalyLocation(stage=_STAGE, source="pipeline"),
        evidence=[AnomalyEvidence(kind="observation", detail=detail)],
    )
