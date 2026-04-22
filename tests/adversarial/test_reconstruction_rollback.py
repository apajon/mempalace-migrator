"""M12 tasks 15.2 + 15.8 — mid-batch ChromaDB failure and atomic rollback.

15.2: Parametrised over batch-index N ∈ {0, 1, k} where k is the last batch
index.  A monkeypatch injects a _BatchInsertError into ``add_in_batches`` on
the Nth batch; all prior batches succeed normally.

15.8: Re-asserts the atomic-rollback invariant across *all* write-path failure
points:
  - first-batch add raises (N=0)
  - last-batch add raises (N=k)
  - manifest write raises (POSIX read-only parent)

Injection strategy (§3.3 of M12_WRITE_PATH_DESIGN.md):
  monkeypatch tests use in-process ``run_pipeline`` so that monkeypatch is
  visible inside the same process.  Only the POSIX permission test uses a
  subprocess (``run_migrate_cli``) because it relies on OS-level
  PermissionError, not a patched code path.

Cross-cutting invariants on every failure case:
  - failure is a MigratorError with stage == "reconstruct"
  - RECONSTRUCTION_ROLLBACK anomaly present with non-empty evidence
  - target_path does **not** exist on disk post-run
  - source palace is byte-identical after the run
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from mempalace_migrator.core.context import AnomalyType, MigrationContext
from mempalace_migrator.core.errors import MigratorError
from mempalace_migrator.core.pipeline import MIGRATE_PIPELINE, run_pipeline
from mempalace_migrator.reconstruction._writer import BATCH_SIZE
from tests.adversarial._invariants import check_no_traceback_on_stderr
from tests.adversarial.conftest import EXIT_RECONSTRUCT_FAILED, build_minimal_valid_chroma_06, run_migrate_cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_snapshot(source: Path) -> dict[str, bytes]:
    return {str(p.relative_to(source)): p.read_bytes() for p in sorted(source.rglob("*")) if p.is_file()}


def _assert_source_unchanged(source: Path, before: dict[str, bytes], *, cid: str) -> None:
    after = _source_snapshot(source)
    assert after == before, f"[{cid}] source palace was mutated.\nbefore={sorted(before)}\nafter={sorted(after)}"


def _assert_rollback_complete(target: Path, *, cid: str) -> None:
    assert not target.exists(), f"[{cid}] target directory still exists after rollback: {target}"


def _assert_rollback_anomaly_in_ctx(ctx: MigrationContext, *, cid: str) -> None:
    """Assert RECONSTRUCTION_ROLLBACK is in the context anomaly list."""
    types = [a.type.value if hasattr(a.type, "value") else str(a.type) for a in ctx.anomalies]
    assert (
        AnomalyType.RECONSTRUCTION_ROLLBACK.value in types
    ), f"[{cid}] RECONSTRUCTION_ROLLBACK anomaly not present.\nactual types: {types}"


def _run_pipeline_in_process(source: Path, target: Path) -> tuple[MigrationContext, MigratorError | None]:
    """Run MIGRATE_PIPELINE in-process; return (ctx, raised_or_None)."""
    ctx = MigrationContext(source_path=source, target_path=target)
    raised: MigratorError | None = None
    try:
        run_pipeline(ctx, MIGRATE_PIPELINE)
    except MigratorError as exc:
        raised = exc
    return ctx, raised


def _build_n_drawer_source(tmp_path: Path, n: int) -> Path:
    """Build a valid chroma 0.6 source with *n* drawers."""
    return build_minimal_valid_chroma_06(tmp_path, n_drawers=n)


# ---------------------------------------------------------------------------
# 15.2 — mid-batch failure via monkeypatch (in-process)
# ---------------------------------------------------------------------------

_STRESS_N = 2 * BATCH_SIZE + 1  # total rows: gives 3 batches
_LAST_BATCH_INDEX = 2  # 0-indexed: batches 0,1,2


@pytest.mark.parametrize(
    "fail_at_batch,n_rows",
    [
        pytest.param(0, BATCH_SIZE + 1, id="first_batch"),
        pytest.param(1, 2 * BATCH_SIZE + 1, id="middle_batch"),
        pytest.param(_LAST_BATCH_INDEX, _STRESS_N, id="last_batch"),
    ],
)
def test_mid_batch_failure_rollback(fail_at_batch: int, n_rows: int, tmp_path, monkeypatch):
    """Injecting a failure at batch *fail_at_batch* must trigger full rollback (in-process)."""
    cid = f"mid_batch_failure_N{fail_at_batch}"
    source = _build_n_drawer_source(tmp_path / "src", n_rows)
    target = tmp_path / "target"
    source_before = _source_snapshot(source)

    import mempalace_migrator.reconstruction._writer as _writer_mod
    from mempalace_migrator.reconstruction._writer import _BatchInsertError

    def _patched_add_in_batches(collection: Any, drawers: tuple) -> int:
        items = list(drawers)
        total = 0
        for batch_index in range(0, len(items), BATCH_SIZE):
            batch = items[batch_index : batch_index + BATCH_SIZE]
            ids = [d.id for d in batch]
            documents = [d.document for d in batch]
            metadatas = [d.metadata if d.metadata else None for d in batch]
            current_batch_number = batch_index // BATCH_SIZE
            if current_batch_number == fail_at_batch:
                raise _BatchInsertError(
                    batch_index=current_batch_number,
                    first_id=ids[0] if ids else "",
                    last_id=ids[-1] if ids else "",
                    cause=RuntimeError(f"injected mid-batch failure at batch {current_batch_number}"),
                )
            collection.add(ids=ids, documents=documents, metadatas=metadatas)
            total += len(batch)
        return total

    monkeypatch.setattr(_writer_mod, "add_in_batches", _patched_add_in_batches)

    ctx, raised = _run_pipeline_in_process(source, target)

    assert raised is not None, f"[{cid}] expected MigratorError to be raised, but pipeline succeeded"
    assert raised.stage == "reconstruct", f"[{cid}] expected stage='reconstruct', got {raised.stage!r}"
    _assert_rollback_anomaly_in_ctx(ctx, cid=cid)
    _assert_rollback_complete(target, cid=cid)
    _assert_source_unchanged(source, source_before, cid=cid)


# ---------------------------------------------------------------------------
# 15.8 — atomic rollback re-assertion across multiple failure points
# ---------------------------------------------------------------------------


def test_rollback_first_batch_failure(tmp_path, monkeypatch):
    """Rollback fires on first-batch failure (batch N=0) — in-process."""
    cid = "rollback_first_batch"
    source = _build_n_drawer_source(tmp_path / "src", BATCH_SIZE + 1)
    target = tmp_path / "target"
    source_before = _source_snapshot(source)

    import mempalace_migrator.reconstruction._writer as _writer_mod
    from mempalace_migrator.reconstruction._writer import _BatchInsertError

    def _fail_first_batch(collection: Any, drawers: tuple) -> int:
        items = list(drawers)
        ids = [d.id for d in items[:BATCH_SIZE]]
        raise _BatchInsertError(
            batch_index=0,
            first_id=ids[0] if ids else "",
            last_id=ids[-1] if ids else "",
            cause=RuntimeError("injected first-batch failure"),
        )

    monkeypatch.setattr(_writer_mod, "add_in_batches", _fail_first_batch)

    ctx, raised = _run_pipeline_in_process(source, target)

    assert (
        raised is not None and raised.stage == "reconstruct"
    ), f"[{cid}] expected reconstruct-stage MigratorError, got raised={raised!r}"
    _assert_rollback_complete(target, cid=cid)
    _assert_source_unchanged(source, source_before, cid=cid)
    _assert_rollback_anomaly_in_ctx(ctx, cid=cid)


def test_rollback_last_batch_failure(tmp_path, monkeypatch):
    """Rollback fires on last-batch failure (N=k) — in-process."""
    cid = "rollback_last_batch"
    n_rows = 2 * BATCH_SIZE + 1  # 3 batches; fail the last
    source = _build_n_drawer_source(tmp_path / "src", n_rows)
    target = tmp_path / "target"
    source_before = _source_snapshot(source)

    import mempalace_migrator.reconstruction._writer as _writer_mod
    from mempalace_migrator.reconstruction._writer import _BatchInsertError

    last_batch_index = (n_rows - 1) // BATCH_SIZE

    def _fail_last_batch(collection: Any, drawers: tuple) -> int:
        items = list(drawers)
        total = 0
        for bi in range(0, len(items), BATCH_SIZE):
            batch = items[bi : bi + BATCH_SIZE]
            ids = [d.id for d in batch]
            documents = [d.document for d in batch]
            metadatas = [d.metadata if d.metadata else None for d in batch]
            current = bi // BATCH_SIZE
            if current == last_batch_index:
                raise _BatchInsertError(
                    batch_index=current,
                    first_id=ids[0] if ids else "",
                    last_id=ids[-1] if ids else "",
                    cause=RuntimeError("injected last-batch failure"),
                )
            collection.add(ids=ids, documents=documents, metadatas=metadatas)
            total += len(batch)
        return total

    monkeypatch.setattr(_writer_mod, "add_in_batches", _fail_last_batch)

    ctx, raised = _run_pipeline_in_process(source, target)

    assert (
        raised is not None and raised.stage == "reconstruct"
    ), f"[{cid}] expected reconstruct-stage MigratorError, got raised={raised!r}"
    _assert_rollback_complete(target, cid=cid)
    _assert_source_unchanged(source, source_before, cid=cid)
    _assert_rollback_anomaly_in_ctx(ctx, cid=cid)


@pytest.mark.skipif(
    not hasattr(os, "getuid") or os.getuid() == 0,
    reason="POSIX read-only parent test requires non-root POSIX environment",
)
def test_rollback_manifest_write_permission_error(tmp_path):
    """Rollback fires when manifest write raises PermissionError (POSIX only).

    This test uses subprocess (``run_migrate_cli``) because the failure is an
    OS-level PermissionError — no monkeypatching needed.
    """
    cid = "rollback_manifest_write"
    source = build_minimal_valid_chroma_06(tmp_path / "src")
    readonly_parent = tmp_path / "readonly"
    readonly_parent.mkdir()
    target = readonly_parent / "my_palace"
    source_before = _source_snapshot(source)

    original_mode = readonly_parent.stat().st_mode
    os.chmod(readonly_parent, stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = run_migrate_cli(source, target)
    finally:
        os.chmod(readonly_parent, original_mode)

    assert result.returncode == EXIT_RECONSTRUCT_FAILED, (
        f"[{cid}] expected exit {EXIT_RECONSTRUCT_FAILED}, got {result.returncode}.\n" f"stderr={result.stderr!r}"
    )
    check_no_traceback_on_stderr(cid, result.stderr)
    # Target must not exist (rollback completed).
    os.chmod(readonly_parent, original_mode)
    _assert_rollback_complete(target, cid=cid)
    os.chmod(readonly_parent, stat.S_IRUSR | stat.S_IXUSR)
    _assert_source_unchanged(source, source_before, cid=cid)
    os.chmod(readonly_parent, original_mode)
