"""Reconstruction orchestrator — the single entry point for the stage.

reconstruct(ctx) safety-checks the target, creates the directory, opens
the chromadb client, inserts all drawers in batches, writes the manifest,
and stores a ``ReconstructionResult`` on ``ctx.reconstruction_result``.

Atomicity contract: any exception after ``target_path.mkdir`` triggers
``_rollback_target`` which removes the partial directory. The source
palace is never touched.

This module does **not** import chromadb at the module level. The
chromadb import lives in ``_writer.py`` only. Asserted by
``tests/test_reconstruction_purity.py``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from mempalace_migrator.core.context import AnomalyEvidence, AnomalyLocation, AnomalyType, MigrationContext, Severity
from mempalace_migrator.core.errors import ReconstructionError
from mempalace_migrator.reconstruction._manifest import TARGET_MANIFEST_FILENAME, write_target_manifest
from mempalace_migrator.reconstruction._safety import ensure_target_is_safe
from mempalace_migrator.reconstruction._types import ReconstructionResult
from mempalace_migrator.reporting.report_builder import TOOL_VERSION

if TYPE_CHECKING:
    pass

_STAGE = "reconstruct"


def reconstruct(ctx: MigrationContext) -> ReconstructionResult:
    """Build a fresh ChromaDB 1.x palace at ``ctx.target_path``.

    Preconditions (caller must verify):
      - ``ctx.target_path`` is set (not None)
      - ``ctx.transformed_data`` is non-None with ``drawers`` non-empty

    Raises ``ReconstructionError`` on any failure, always with a CRITICAL
    anomaly pre-recorded in ``ctx``. Atomicity is guaranteed: on any
    exception after mkdir, the partial target is removed.
    """
    target_path: Path = ctx.target_path  # type: ignore[assignment]  # caller checks
    bundle = ctx.transformed_data  # type: ignore[assignment]

    # --- Safety check (pre-mkdir: no state changed yet) ---
    _run_safety(ctx, target_path)

    # Track whether we created the directory so rollback knows what to clean.
    _did_create = not target_path.exists()

    try:
        target_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _emit(
            ctx,
            type=AnomalyType.TARGET_PATH_NOT_DIRECTORY,
            severity=Severity.CRITICAL,
            message=f"could not create target directory: {exc}",
            evidence=[
                AnomalyEvidence(
                    kind="os_error",
                    detail=str(exc),
                    data={"errno": exc.errno, "path": str(target_path)},
                )
            ],
        )
        raise ReconstructionError(
            stage=_STAGE,
            code="target_path_mkdir_failed",
            summary=f"could not create target directory: {exc}",
        ) from exc

    # Beyond this point: rollback on any exception.
    try:
        result = _write(ctx, target_path, bundle, _did_create)
    except ReconstructionError:
        raise
    except Exception as exc:
        # Unexpected exception: wrap and rollback.
        _emit_rollback(ctx, "unexpected_error", str(exc))
        _rollback_target(ctx, target_path, _did_create)
        raise ReconstructionError(
            stage=_STAGE,
            code="unexpected_error",
            summary=f"unexpected error during reconstruction: {exc}",
        ) from exc

    return result


def _run_safety(ctx: MigrationContext, target_path: Path) -> None:
    """Wrap ensure_target_is_safe, converting its ReconstructionError into
    a proper CRITICAL anomaly in ctx before re-raising.
    """
    try:
        ensure_target_is_safe(target_path)
    except ReconstructionError as exc:
        anomaly_type = (
            AnomalyType.TARGET_PATH_NOT_DIRECTORY
            if exc.code == "target_path_not_directory"
            else AnomalyType.TARGET_PATH_NOT_EMPTY
        )
        _emit(
            ctx,
            type=anomaly_type,
            severity=Severity.CRITICAL,
            message=exc.summary,
            evidence=[
                AnomalyEvidence(
                    kind="path_check",
                    detail=exc.summary,
                    data={"path": str(target_path), "code": exc.code},
                )
            ],
        )
        raise


def _write(
    ctx: MigrationContext,
    target_path: Path,
    bundle: object,
    did_create: bool,
) -> ReconstructionResult:
    """Core write sequence. Any exception triggers rollback + ReconstructionError."""
    # Lazy-import the writer so reconstructor.py stays chromadb-free at module level.
    from mempalace_migrator.reconstruction import _writer  # noqa: PLC0415

    # --- Open client ---
    try:
        client = _writer.open_client(target_path)
    except Exception as exc:
        _emit(
            ctx,
            type=AnomalyType.CHROMADB_CLIENT_FAILED,
            severity=Severity.CRITICAL,
            message=f"failed to open chromadb client at {target_path}: {exc}",
            evidence=[AnomalyEvidence(kind="exception", detail=str(exc), data={"path": str(target_path)})],
        )
        _emit_rollback(ctx, "chromadb_client_failed", str(exc))
        _rollback_target(ctx, target_path, did_create)
        raise ReconstructionError(
            stage=_STAGE,
            code="chromadb_client_failed",
            summary=f"failed to open chromadb client: {exc}",
        ) from exc

    chromadb_version: str = _get_chromadb_version()

    # --- Create collection ---
    try:
        collection = _writer.create_collection(
            client,
            name=bundle.collection_name,  # type: ignore[attr-defined]
            metadata=dict(bundle.collection_metadata),  # type: ignore[attr-defined]
        )
    except Exception as exc:
        _emit(
            ctx,
            type=AnomalyType.CHROMADB_COLLECTION_CREATE_FAILED,
            severity=Severity.CRITICAL,
            message=f"failed to create collection {bundle.collection_name!r}: {exc}",  # type: ignore[attr-defined]
            evidence=[
                AnomalyEvidence(
                    kind="exception",
                    detail=str(exc),
                    data={"collection_name": bundle.collection_name},  # type: ignore[attr-defined]
                )
            ],
        )
        _emit_rollback(ctx, "chromadb_collection_create_failed", str(exc))
        _rollback_target(ctx, target_path, did_create)
        raise ReconstructionError(
            stage=_STAGE,
            code="chromadb_collection_create_failed",
            summary=f"failed to create collection: {exc}",
        ) from exc

    # --- Batch insert ---
    try:
        imported_count = _writer.add_in_batches(collection, bundle.drawers)  # type: ignore[attr-defined]
    except _writer._BatchInsertError as exc:
        _emit(
            ctx,
            type=AnomalyType.CHROMADB_BATCH_INSERT_FAILED,
            severity=Severity.CRITICAL,
            message=(
                f"batch {exc.batch_index} insert failed " f"(ids {exc.first_id!r}..{exc.last_id!r}): {exc.cause}"
            ),
            evidence=[
                AnomalyEvidence(
                    kind="batch_error",
                    detail=str(exc.cause),
                    data={
                        "batch_index": exc.batch_index,
                        "first_id": exc.first_id,
                        "last_id": exc.last_id,
                    },
                )
            ],
        )
        _emit_rollback(ctx, "chromadb_batch_insert_failed", str(exc.cause))
        _rollback_target(ctx, target_path, did_create)
        raise ReconstructionError(
            stage=_STAGE,
            code="chromadb_batch_insert_failed",
            summary=f"batch {exc.batch_index} insert failed: {exc.cause}",
        ) from exc

    # --- Write manifest ---
    detected_format = _safe_attr(ctx.detected_format, "classification", "unknown")
    source_version = _safe_attr(ctx.detected_format, "source_version", None)
    try:
        manifest_path = write_target_manifest(
            target_path=target_path,
            source_palace_path=ctx.source_path,
            detected_format=detected_format,
            source_version=source_version,
            drawer_count=imported_count,
            collection_name=bundle.collection_name,  # type: ignore[attr-defined]
            chromadb_version=chromadb_version,
            migrator_version=TOOL_VERSION,
        )
    except OSError as exc:
        _emit(
            ctx,
            type=AnomalyType.TARGET_MANIFEST_WRITE_FAILED,
            severity=Severity.CRITICAL,
            message=f"failed to write target manifest: {exc}",
            evidence=[AnomalyEvidence(kind="os_error", detail=str(exc), data={"errno": exc.errno})],
        )
        _emit_rollback(ctx, "target_manifest_write_failed", str(exc))
        _rollback_target(ctx, target_path, did_create)
        raise ReconstructionError(
            stage=_STAGE,
            code="target_manifest_write_failed",
            summary=f"failed to write target manifest: {exc}",
        ) from exc

    return ReconstructionResult(
        target_path=target_path,
        collection_name=bundle.collection_name,  # type: ignore[attr-defined]
        imported_count=imported_count,
        batch_size=_writer.BATCH_SIZE,
        chromadb_version=chromadb_version,
        target_manifest_path=manifest_path,
    )


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def _rollback_target(ctx: MigrationContext, target_path: Path, did_create: bool) -> None:
    """Remove the partial target directory if we created it.

    If we did not create it (caller passed an already-existing empty dir),
    remove only the children written during this run rather than the dir
    itself.

    On rmtree failure: the exception is added to the last anomaly's details
    and re-raised so the caller surfaces both the original error and the
    rollback failure.
    """
    if not target_path.exists():
        return  # Nothing to roll back.

    try:
        if did_create:
            shutil.rmtree(target_path)
        else:
            # Best-effort: remove children written by this run.
            for child in list(target_path.iterdir()):
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
    except Exception as rollback_exc:
        # Append rollback failure to the last anomaly in ctx for visibility.
        if ctx.anomalies:
            # We cannot mutate a frozen Anomaly; record the rollback note
            # as an additional anomaly.
            _emit(
                ctx,
                type=AnomalyType.RECONSTRUCTION_ROLLBACK,
                severity=Severity.HIGH,
                message=f"rollback itself failed: {rollback_exc}",
                evidence=[
                    AnomalyEvidence(
                        kind="rollback_error",
                        detail=str(rollback_exc),
                        data={"path": str(target_path)},
                    )
                ],
            )
        raise


def _emit_rollback(ctx: MigrationContext, cause_code: str, cause_detail: str) -> None:
    """Emit RECONSTRUCTION_ROLLBACK/HIGH before initiating rmtree."""
    _emit(
        ctx,
        type=AnomalyType.RECONSTRUCTION_ROLLBACK,
        severity=Severity.HIGH,
        message=f"rolling back target directory due to: {cause_code}",
        evidence=[
            AnomalyEvidence(
                kind="rollback",
                detail=cause_detail,
                data={"cause_code": cause_code},
            )
        ],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit(
    ctx: MigrationContext,
    *,
    type: AnomalyType,
    severity: Severity,
    message: str,
    evidence: list[AnomalyEvidence],
) -> None:
    ctx.add_anomaly(
        type=type,
        severity=severity,
        message=message,
        location=AnomalyLocation(stage=_STAGE, source="reconstruction"),
        evidence=evidence,
    )


def _safe_attr(obj: object, attr: str, default: object) -> object:
    try:
        return getattr(obj, attr)
    except AttributeError:
        return default


def _get_chromadb_version() -> str:
    try:
        import chromadb  # noqa: PLC0415

        return str(chromadb.__version__)
    except Exception:
        return "unknown"


def _get_chromadb_version() -> str:
    try:
        import chromadb  # noqa: PLC0415

        return str(chromadb.__version__)
    except Exception:
        return "unknown"
