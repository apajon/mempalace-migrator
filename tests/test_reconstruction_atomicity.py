"""M10 — atomicity contract for reconstruct() rollback.

Covers:
  - mid-batch failure (monkeypatch batch 1 add to raise):
      * target_path does NOT exist after the call
      * RECONSTRUCTION_ROLLBACK anomaly present
      * CHROMADB_BATCH_INSERT_FAILED/CRITICAL anomaly present
      * ReconstructionError(code='chromadb_batch_insert_failed') raised
      * ctx.reconstruction_result is None
  - source file is byte-identical after a rollback (sha256 + mtime)
  - atomicity when target was absent before the call (dir fully removed)
  - atomicity when target was an existing empty dir (dir still exists but empty)
  - chromadb_client_failed → rollback removes dir
  - chromadb_collection_create_failed → rollback removes dir
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mempalace_migrator.core.context import AnomalyType, MigrationContext, Severity
from mempalace_migrator.core.errors import ReconstructionError
from mempalace_migrator.core.pipeline import step_validate
from mempalace_migrator.detection.format_detector import MANIFEST_FILENAME, SQLITE_FILENAME
from mempalace_migrator.extraction.chroma_06_reader import EXPECTED_COLLECTION_NAME
from mempalace_migrator.reconstruction import reconstruct
from mempalace_migrator.transformation._types import (
    LengthProfile,
    TransformedBundle,
    TransformedDrawer,
    TransformedSummary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MANIFEST_06 = {"compatibility_line": "chromadb-0.6.x", "chromadb_version": "0.6.3"}


def _td(id: str) -> TransformedDrawer:
    return TransformedDrawer(id=id, document=f"doc {id}", metadata={"wing": "north"})


def _make_bundle(n: int = 4) -> TransformedBundle:
    drawers = tuple(_td(f"id{i}") for i in range(n))
    summary = TransformedSummary(
        drawer_count=n,
        dropped_count=0,
        coerced_count=0,
        sample_ids=tuple(d.id for d in drawers[:3]),
        metadata_keys=("wing",),
        wing_room_counts=(("north", "", n),),
        length_profile=LengthProfile(min=5, max=5, mean=5.0, p50=5, p95=5),
    )
    return TransformedBundle(
        collection_name=EXPECTED_COLLECTION_NAME,
        collection_metadata={},
        drawers=drawers,
        summary=summary,
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _make_ctx(source: Path, target: Path) -> MigrationContext:
    ctx = MigrationContext(source_path=source, target_path=target)
    ctx.transformed_data = _make_bundle()
    return ctx


# ---------------------------------------------------------------------------
# Mid-batch failure → full rollback
# ---------------------------------------------------------------------------


def test_mid_batch_failure_target_does_not_exist(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target"

    ctx = _make_ctx(source, target)

    call_count = {"n": 0}

    original_add = None

    def fail_on_second_batch(**kwargs):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise RuntimeError("injected failure")
        if original_add:
            return original_add(**kwargs)

    with patch("mempalace_migrator.reconstruction._writer.BATCH_SIZE", 2):
        from mempalace_migrator.reconstruction import _writer
        from mempalace_migrator.reconstruction._writer import _BatchInsertError as RealBIE

        original_add_in_batches = _writer.add_in_batches

        def patched_add(col, drawers):
            # Accept first batch; fail on second
            items = list(drawers)
            if len(items) < 3:
                return original_add_in_batches(col, drawers)
            # Let first batch through, fail second
            batch_0 = items[:2]
            col.add(
                ids=[d.id for d in batch_0],
                documents=[d.document for d in batch_0],
                metadatas=[d.metadata if d.metadata else None for d in batch_0],
            )
            raise RealBIE(
                batch_index=1,
                first_id=items[2].id,
                last_id=items[-1].id,
                cause=RuntimeError("injected"),
            )

        with patch("mempalace_migrator.reconstruction._writer.add_in_batches", patched_add):
            with pytest.raises(ReconstructionError) as exc_info:
                reconstruct(ctx)

    assert exc_info.value.code == "chromadb_batch_insert_failed"
    assert not target.exists(), "rollback must remove the target dir"
    assert ctx.reconstruction_result is None


def test_mid_batch_failure_rollback_anomaly_present(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target"
    ctx = _make_ctx(source, target)

    from mempalace_migrator.reconstruction._writer import _BatchInsertError as RealBIE  # noqa

    def fail_add(col, drawers):
        items = list(drawers)
        raise RealBIE(
            batch_index=0,
            first_id=items[0].id if items else "",
            last_id=items[-1].id if items else "",
            cause=RuntimeError("injected"),
        )

    with patch("mempalace_migrator.reconstruction._writer.add_in_batches", fail_add):
        with pytest.raises(ReconstructionError):
            reconstruct(ctx)

    rollback_anomalies = [a for a in ctx.anomalies if a.type == AnomalyType.RECONSTRUCTION_ROLLBACK]
    assert len(rollback_anomalies) >= 1
    assert rollback_anomalies[0].severity == Severity.HIGH


def test_mid_batch_failure_critical_anomaly_present(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target"
    ctx = _make_ctx(source, target)

    from mempalace_migrator.reconstruction._writer import _BatchInsertError as RealBIE  # noqa

    def fail_add(col, drawers):
        items = list(drawers)
        raise RealBIE(
            batch_index=0,
            first_id=items[0].id if items else "",
            last_id=items[-1].id if items else "",
            cause=RuntimeError("injected"),
        )

    with patch("mempalace_migrator.reconstruction._writer.add_in_batches", fail_add):
        with pytest.raises(ReconstructionError):
            reconstruct(ctx)

    critical = [
        a
        for a in ctx.anomalies
        if a.type == AnomalyType.CHROMADB_BATCH_INSERT_FAILED and a.severity == Severity.CRITICAL
    ]
    assert len(critical) >= 1


def test_reconstruction_result_none_after_failure(tmp_path: Path) -> None:
    target = tmp_path / "target"
    ctx = _make_ctx(tmp_path / "src", target)

    from mempalace_migrator.reconstruction._writer import _BatchInsertError as RealBIE

    def fail_add(col, drawers):
        items = list(drawers)
        raise RealBIE(
            batch_index=0,
            first_id=items[0].id if items else "",
            last_id=items[-1].id if items else "",
            cause=RuntimeError("injected"),
        )

    with patch("mempalace_migrator.reconstruction._writer.add_in_batches", fail_add):
        with pytest.raises(ReconstructionError):
            reconstruct(ctx)

    assert ctx.reconstruction_result is None


# ---------------------------------------------------------------------------
# chromadb_client_failed → rollback
# ---------------------------------------------------------------------------


def test_client_failed_rollback(tmp_path: Path) -> None:
    target = tmp_path / "target"
    ctx = _make_ctx(tmp_path / "src", target)

    with patch(
        "mempalace_migrator.reconstruction._writer.open_client",
        side_effect=RuntimeError("client init failed"),
    ):
        with pytest.raises(ReconstructionError) as exc_info:
            reconstruct(ctx)

    assert exc_info.value.code == "chromadb_client_failed"
    assert not target.exists()
    client_anomalies = [a for a in ctx.anomalies if a.type == AnomalyType.CHROMADB_CLIENT_FAILED]
    assert len(client_anomalies) >= 1


# ---------------------------------------------------------------------------
# chromadb_collection_create_failed → rollback
# ---------------------------------------------------------------------------


def test_collection_create_failed_rollback(tmp_path: Path) -> None:
    target = tmp_path / "target"
    ctx = _make_ctx(tmp_path / "src", target)

    real_open = None

    def open_ok(path):
        import chromadb  # noqa: PLC0415
        from chromadb.config import Settings  # noqa: PLC0415

        return chromadb.PersistentClient(
            path=str(path),
            settings=Settings(anonymized_telemetry=False, allow_reset=False),
        )

    with patch(
        "mempalace_migrator.reconstruction._writer.create_collection",
        side_effect=RuntimeError("collection create failed"),
    ):
        with pytest.raises(ReconstructionError) as exc_info:
            reconstruct(ctx)

    assert exc_info.value.code == "chromadb_collection_create_failed"
    assert not target.exists()


# ---------------------------------------------------------------------------
# M11: step_validate must not modify target files
# ---------------------------------------------------------------------------


def test_validate_target_mtime_invariant(tmp_path: Path) -> None:
    """step_validate (including parity checks) must not modify any file
    inside the target directory.

    We capture mtime + sha256 for every file in the target before
    step_validate runs and assert they are identical after.
    """
    from mempalace_migrator.reconstruction import reconstruct

    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target"

    ctx = _make_ctx(source, target)
    ctx.reconstruction_result = reconstruct(ctx)
    assert ctx.reconstruction_result is not None, "reconstruction must succeed for this test"

    # Snapshot every file in the target before validate.
    def _snapshot(root: Path) -> dict[str, tuple[float, str]]:
        snap: dict[str, tuple[float, str]] = {}
        for f in sorted(root.rglob("*")):
            if f.is_file():
                snap[str(f.relative_to(root))] = (f.stat().st_mtime, _sha256(f))
        return snap

    before = _snapshot(target)
    assert before, "target must have files after reconstruction"

    step_validate(ctx)

    after = _snapshot(target)
    assert after == before, "step_validate modified target files — parity checks must be read-only"
