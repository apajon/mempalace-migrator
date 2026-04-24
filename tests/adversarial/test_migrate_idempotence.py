"""M17 task 20.3 — idempotence of the migrate command.

Three cases:

  (a) Two fresh targets, same source:
      Both runs must exit 0 and produce identical extract_report_signature
      values (modulo the redaction map from M8/hardening/conftest.py).
      Reconstructed record counts must be equal.

  (b) Same target, same source, run twice:
      Second run must exit 5 (EXIT_RECONSTRUCT_FAILED) with anomaly
      TARGET_PATH_NOT_EMPTY, and the target bytes must be unchanged.

  (c) Same target path, run 1 failed mid-batch (injection), then run 2:
      Run 2 must exit 0 and produce a signature equal to the case-(a)
      baseline (proving recovery after rollback leaves a clean slate).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mempalace_migrator.core.context import AnomalyType
from mempalace_migrator.core.errors import MigratorError
from mempalace_migrator.core.pipeline import MIGRATE_PIPELINE, run_pipeline
from mempalace_migrator.reconstruction._writer import BATCH_SIZE, _BatchInsertError
from tests.adversarial._invariants import check_no_unexpected_exit_code
from tests.adversarial.conftest import (
    EXIT_OK,
    EXIT_RECONSTRUCT_FAILED,
    build_minimal_valid_chroma_06,
    run_migrate_cli,
)
from tests.hardening.conftest import extract_report_signature

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snapshot(path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


def _run_migrate(source: Path, target: Path) -> tuple[int, dict[str, Any], str]:
    """Run the migrate CLI via subprocess; return (rc, report_or_{}, stderr)."""
    result = run_migrate_cli(source, target)
    check_no_unexpected_exit_code("idempotence_run", result.returncode, result.stderr)
    report: dict[str, Any] = {}
    if result.stdout.strip():
        import json

        report = json.loads(result.stdout)
    return result.returncode, report, result.stderr


def _run_in_process_with_batch_failure(source: Path, target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run MIGRATE_PIPELINE in-process with batch-0 failure injected; expect MigratorError."""
    import mempalace_migrator.reconstruction._writer as _writer_mod

    original_add = (
        _writer_mod.add_in_batches.__wrapped__ if hasattr(_writer_mod.add_in_batches, "__wrapped__") else None
    )

    def _fail_at_batch_0(collection: Any, drawers: tuple) -> int:
        items = list(drawers)
        first_batch = items[:BATCH_SIZE]
        ids = [d.id for d in first_batch]
        raise _BatchInsertError(
            batch_index=0,
            first_id=ids[0] if ids else "",
            last_id=ids[-1] if ids else "",
            cause=RuntimeError("injected: case-(c) run-1 batch failure"),
        )

    monkeypatch.setattr(_writer_mod, "add_in_batches", _fail_at_batch_0)

    from mempalace_migrator.core.context import MigrationContext

    ctx = MigrationContext(source_path=source, target_path=target)
    with pytest.raises(MigratorError) as exc_info:
        run_pipeline(ctx, MIGRATE_PIPELINE)
    assert exc_info.value.stage == "reconstruct", f"expected stage='reconstruct', got {exc_info.value.stage!r}"
    assert not target.exists(), "run-1 rollback did not remove target"

    # Undo the monkeypatch so run-2 uses the real implementation.
    monkeypatch.undo()


# ---------------------------------------------------------------------------
# Case (a): two fresh targets, same source → identical signatures
# ---------------------------------------------------------------------------


def test_idempotence_two_fresh_targets(tmp_path: Path) -> None:
    """Same source migrated to two independent targets → identical report signatures."""
    source = build_minimal_valid_chroma_06(tmp_path / "src", n_drawers=3)
    target1 = tmp_path / "target1"
    target2 = tmp_path / "target2"

    rc1, report1, _ = _run_migrate(source, target1)
    rc2, report2, _ = _run_migrate(source, target2)

    assert rc1 == EXIT_OK, f"run-1 exited {rc1}"
    assert rc2 == EXIT_OK, f"run-2 exited {rc2}"

    sig1 = extract_report_signature(report1, rc1)
    sig2 = extract_report_signature(report2, rc2)
    assert sig1 == sig2, (
        "report signatures differ between two fresh-target runs of the same source.\n" f"sig1={sig1}\nsig2={sig2}"
    )

    # Record counts must also match.
    rc1_count = (report1.get("reconstruction") or {}).get("imported_count")
    rc2_count = (report2.get("reconstruction") or {}).get("imported_count")
    assert rc1_count == rc2_count, f"imported_count differs: run-1={rc1_count}, run-2={rc2_count}"


# ---------------------------------------------------------------------------
# Case (b): same target, same source, run twice → second run exits 5
# ---------------------------------------------------------------------------


def test_idempotence_reused_target_rejected(tmp_path: Path) -> None:
    """Second migrate against an already-populated target must exit 5."""
    source = build_minimal_valid_chroma_06(tmp_path / "src", n_drawers=3)
    target = tmp_path / "target"

    rc1, _, _ = _run_migrate(source, target)
    assert rc1 == EXIT_OK, f"first run should succeed; got exit {rc1}"
    assert target.exists(), "target was not created by first run"

    snapshot_before = _snapshot(target)

    rc2, report2, stderr2 = _run_migrate(source, target)

    assert (
        rc2 == EXIT_RECONSTRUCT_FAILED
    ), f"second run against populated target must exit {EXIT_RECONSTRUCT_FAILED}; got {rc2}"
    assert report2, "second run must emit a JSON report"
    anomaly_types = [a.get("type") for a in (report2.get("anomalies") or [])]
    assert (
        AnomalyType.TARGET_PATH_NOT_EMPTY.value in anomaly_types
    ), f"expected TARGET_PATH_NOT_EMPTY anomaly; got {anomaly_types}"
    assert (report2.get("failure") or {}).get(
        "stage"
    ) == "reconstruct", "failure.stage must be 'reconstruct' on second run"

    # Target bytes must be unchanged.
    snapshot_after = _snapshot(target)
    assert snapshot_before == snapshot_after, "target was mutated by the rejected second run"


# ---------------------------------------------------------------------------
# Case (c): run-1 fails mid-batch (rollback), run-2 at same path succeeds
# ---------------------------------------------------------------------------


def test_idempotence_recovery_after_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After run-1 rolls back, run-2 on the same path succeeds with matching data (Inv. 7)."""
    source = build_minimal_valid_chroma_06(tmp_path / "src", n_drawers=BATCH_SIZE + 1)
    target = tmp_path / "target"

    # Run-1: inject failure → rollback.
    _run_in_process_with_batch_failure(source, target, monkeypatch)

    # Target must be absent after rollback.
    assert not target.exists(), "target must not exist after run-1 rollback"

    # Collect the case-(a) baseline signature for the same source.
    baseline_target = tmp_path / "baseline_target"
    rc_base, report_base, _ = _run_migrate(source, baseline_target)
    assert rc_base == EXIT_OK, f"baseline run exited {rc_base}"
    sig_base = extract_report_signature(report_base, rc_base)

    # Run-2: real pipeline, target path reused.
    rc2, report2, stderr2 = _run_migrate(source, target)
    assert rc2 == EXIT_OK, f"run-2 (recovery) exited {rc2}; stderr={stderr2}"

    sig2 = extract_report_signature(report2, rc2)
    assert sig2 == sig_base, (
        "run-2 (recovery) signature does not match case-(a) baseline.\n" f"sig2={sig2}\nbaseline={sig_base}"
    )
