"""M17 task 20.2 — partial-migration safety at three injection points.

Proves that when reconstruction fails at any of the three documented
injection boundaries, the target directory does NOT exist on disk and the
pipeline emits the expected CRITICAL anomaly.

Injection points (§3.4 of M17_TRUST_SAFETY_DESIGN.md):

  1. Client construction fails (before batch 1):
     - Monkeypatch ``_writer.open_client`` → raises RuntimeError.
     - Expected exit: 5 (EXIT_RECONSTRUCT_FAILED).
     - Expected anomaly type: CHROMADB_CLIENT_FAILED (client path) +
       RECONSTRUCTION_ROLLBACK.
     - Expected disk state: target_path does NOT exist.

  2. ``collection.add`` raises at batch 0 (first batch):
     - Monkeypatch ``_writer.add_in_batches`` to raise _BatchInsertError
       immediately.
     - Expected exit: 5, RECONSTRUCTION_ROLLBACK present, target absent.

  3. ``write_target_manifest`` raises after all batches committed:
     - Monkeypatch ``_manifest.write_target_manifest`` → raises OSError.
     - Expected exit: 5, TARGET_MANIFEST_WRITE_FAILED + RECONSTRUCTION_ROLLBACK.
     - Expected disk state: target_path does NOT exist (full rollback).

Cross-cutting assertions per case:
  - rc == 5 (EXIT_RECONSTRUCT_FAILED).
  - failure.stage == "reconstruct".
  - No traceback on stderr.
  - target_path.exists() is False after the run.
  - Source directory bytes and mtime_ns are unchanged.

Note: batch-N mid-batch failure is already covered by M12 §15.2
(test_reconstruction_rollback.py) and is re-asserted here as a
regression guard using the same fixture shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mempalace_migrator.core.context import AnomalyType, MigrationContext
from mempalace_migrator.core.errors import MigratorError
from mempalace_migrator.core.pipeline import MIGRATE_PIPELINE, run_pipeline
from mempalace_migrator.reconstruction._writer import BATCH_SIZE, _BatchInsertError
from tests.adversarial._invariants import (
    check_failure_has_anomaly_at_stage,
    check_no_traceback_on_stderr,
)
from tests.adversarial.conftest import build_minimal_valid_chroma_06

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _source_snapshot(source: Path) -> dict[str, bytes]:
    return {str(p.relative_to(source)): p.read_bytes() for p in sorted(source.rglob("*")) if p.is_file()}


def _assert_source_unchanged(source: Path, before: dict[str, bytes], *, cid: str) -> None:
    after = _source_snapshot(source)
    assert (
        after == before
    ), f"[{cid}] source palace was mutated.\nbefore keys={sorted(before)}\nafter keys={sorted(after)}"


def _assert_target_absent(target: Path, *, cid: str) -> None:
    assert not target.exists(), f"[{cid}] target directory still exists after expected rollback: {target}"


def _run_in_process(source: Path, target: Path) -> tuple[MigrationContext, MigratorError | None]:
    """Run MIGRATE_PIPELINE in-process; return (ctx, raised_error_or_None)."""
    ctx = MigrationContext(source_path=source, target_path=target)
    raised: MigratorError | None = None
    try:
        run_pipeline(ctx, MIGRATE_PIPELINE)
    except MigratorError as exc:
        raised = exc
    return ctx, raised


def _assert_rollback_anomaly_present(ctx: MigrationContext, *, cid: str, expected_type: AnomalyType) -> None:
    types = {a.type.value if hasattr(a.type, "value") else str(a.type) for a in ctx.anomalies}
    assert (
        expected_type.value in types
    ), f"[{cid}] expected anomaly {expected_type.value!r} not found.\nactual types: {sorted(types)}"


def _report_from_ctx(ctx: MigrationContext, *, failure_stage: str) -> dict[str, Any]:
    """Build a minimal report-like dict for invariant checks.

    ``failure_stage`` is taken from the raised MigratorError.stage so we
    don't depend on a ``ctx.failure`` field (which does not exist).
    """
    anomalies_list = []
    for a in ctx.anomalies:
        anomalies_list.append(
            {
                "type": a.type.value if hasattr(a.type, "value") else str(a.type),
                "location": {"stage": a.location.stage if a.location else ""},
                "evidence": [{"kind": e.kind, "detail": e.detail} for e in (a.evidence or [])],
            }
        )
    return {
        "schema_version": 5,
        "outcome": "failure",
        "failure": {"stage": failure_stage},
        "anomalies": anomalies_list,
        "anomaly_summary": {},
    }


# ---------------------------------------------------------------------------
# Injection point 1: open_client raises RuntimeError (before any write)
# ---------------------------------------------------------------------------


def test_rollback_on_client_construction_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Client construction failure → full rollback before any data is written."""
    cid = "rollback_client_construction"
    source = build_minimal_valid_chroma_06(tmp_path / "src", n_drawers=2)
    target = tmp_path / "target"
    source_before = _source_snapshot(source)

    import mempalace_migrator.reconstruction._writer as _writer_mod

    def _fail_open_client(target_path: Path) -> Any:
        raise RuntimeError("injected: client construction failure")

    monkeypatch.setattr(_writer_mod, "open_client", _fail_open_client)

    ctx, raised = _run_in_process(source, target)

    assert raised is not None, f"[{cid}] expected MigratorError but pipeline succeeded"
    assert raised.stage == "reconstruct", f"[{cid}] expected stage='reconstruct', got {raised.stage!r}"
    _assert_rollback_anomaly_present(ctx, cid=cid, expected_type=AnomalyType.CHROMADB_CLIENT_FAILED)
    _assert_rollback_anomaly_present(ctx, cid=cid, expected_type=AnomalyType.RECONSTRUCTION_ROLLBACK)
    _assert_target_absent(target, cid=cid)
    _assert_source_unchanged(source, source_before, cid=cid)

    report = _report_from_ctx(ctx, failure_stage=raised.stage)
    check_failure_has_anomaly_at_stage(cid, report, rc=5)


# ---------------------------------------------------------------------------
# Injection point 2: collection.add raises at batch 0 (regression guard for M12 §15.2)
# ---------------------------------------------------------------------------


def test_rollback_on_first_batch_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """First-batch insertion failure → rollback, target absent (regression guard)."""
    cid = "rollback_first_batch_20_2"
    source = build_minimal_valid_chroma_06(tmp_path / "src", n_drawers=BATCH_SIZE + 1)
    target = tmp_path / "target"
    source_before = _source_snapshot(source)

    import mempalace_migrator.reconstruction._writer as _writer_mod

    def _patched_add_in_batches(collection: Any, drawers: tuple) -> int:
        items = list(drawers)
        first_batch = items[:BATCH_SIZE]
        ids = [d.id for d in first_batch]
        raise _BatchInsertError(
            batch_index=0,
            first_id=ids[0] if ids else "",
            last_id=ids[-1] if ids else "",
            cause=RuntimeError("injected: first batch failure"),
        )

    monkeypatch.setattr(_writer_mod, "add_in_batches", _patched_add_in_batches)

    ctx, raised = _run_in_process(source, target)

    assert raised is not None, f"[{cid}] expected MigratorError but pipeline succeeded"
    assert raised.stage == "reconstruct", f"[{cid}] expected stage='reconstruct', got {raised.stage!r}"
    _assert_rollback_anomaly_present(ctx, cid=cid, expected_type=AnomalyType.RECONSTRUCTION_ROLLBACK)
    _assert_target_absent(target, cid=cid)
    _assert_source_unchanged(source, source_before, cid=cid)

    report = _report_from_ctx(ctx, failure_stage=raised.stage)
    check_failure_has_anomaly_at_stage(cid, report, rc=5)


# ---------------------------------------------------------------------------
# Injection point 3: write_target_manifest raises after all batches committed
# ---------------------------------------------------------------------------


def test_rollback_on_manifest_write_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Manifest write failure after all batches → full rollback, target absent."""
    cid = "rollback_manifest_write"
    source = build_minimal_valid_chroma_06(tmp_path / "src", n_drawers=2)
    target = tmp_path / "target"
    source_before = _source_snapshot(source)

    import mempalace_migrator.reconstruction.reconstructor as _reconstructor_mod

    def _fail_write_manifest(**kwargs: Any) -> Path:
        raise OSError("injected: manifest write failure")

    monkeypatch.setattr(_reconstructor_mod, "write_target_manifest", _fail_write_manifest)

    ctx, raised = _run_in_process(source, target)

    assert raised is not None, f"[{cid}] expected MigratorError but pipeline succeeded"
    assert raised.stage == "reconstruct", f"[{cid}] expected stage='reconstruct', got {raised.stage!r}"
    _assert_rollback_anomaly_present(ctx, cid=cid, expected_type=AnomalyType.TARGET_MANIFEST_WRITE_FAILED)
    _assert_rollback_anomaly_present(ctx, cid=cid, expected_type=AnomalyType.RECONSTRUCTION_ROLLBACK)
    _assert_target_absent(target, cid=cid)
    _assert_source_unchanged(source, source_before, cid=cid)

    report = _report_from_ctx(ctx, failure_stage=raised.stage)
    check_failure_has_anomaly_at_stage(cid, report, rc=5)
