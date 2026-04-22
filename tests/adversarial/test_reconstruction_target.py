"""M12 task 15.1 — target safety: structured rejection of bad target paths.

Covers three cases:
  a. --target points to an existing regular file.
  b. --target points to an existing non-empty directory.
  c. --target parent is read-only (POSIX-only, skipif elsewhere).

For every case the invariants asserted are:
  - exit 5 (EXIT_RECONSTRUCT_FAILED).
  - No Python traceback on stderr.
  - Report failure.stage == "reconstruct".
  - At least one anomaly of the expected type with non-empty evidence.
  - Source palace is byte-identical after the run (write-path invariant).
  - Target path is byte-for-byte identical to its pre-run state (no partial
    overwrite).
"""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from tests.adversarial._invariants import (
    check_anomaly_well_formedness,
    check_failure_stage_is_known,
    check_no_traceback_on_stderr,
    check_schema_stability,
)
from tests.adversarial.conftest import EXIT_RECONSTRUCT_FAILED, build_minimal_valid_chroma_06, run_migrate_cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_snapshot(source: Path) -> dict[str, bytes]:
    """Return a dict of {relative_path: bytes} for the source directory."""
    return {str(p.relative_to(source)): p.read_bytes() for p in sorted(source.rglob("*")) if p.is_file()}


def _assert_source_unchanged(source: Path, before: dict[str, bytes], *, cid: str) -> None:
    after = _source_snapshot(source)
    assert after == before, (
        f"[{cid}] source palace was mutated during migration.\n"
        f"before keys: {sorted(before)}\n"
        f"after  keys: {sorted(after)}"
    )


def _assert_anomaly_type_present(report: dict, expected_type: str, *, cid: str) -> None:
    types = [a.get("type") for a in (report.get("anomalies") or [])]
    assert expected_type in types, (
        f"[{cid}] expected anomaly type {expected_type!r} not found.\n" f"actual types: {types}"
    )


def _assert_failure_stage(report: dict, expected_stage: str, *, cid: str) -> None:
    failure = report.get("failure") or {}
    assert (
        failure.get("stage") == expected_stage
    ), f"[{cid}] failure.stage={failure.get('stage')!r} != {expected_stage!r}"


# ---------------------------------------------------------------------------
# Case a: target is a regular file
# ---------------------------------------------------------------------------


def test_target_is_file_exit5(tmp_path):
    """migrate must reject a --target that is an existing file (exit 5)."""
    cid = "target_is_file"
    source = build_minimal_valid_chroma_06(tmp_path / "src")
    target = tmp_path / "target.txt"
    target.write_text("I am a file, not a directory", encoding="utf-8")

    before = _source_snapshot(source)
    result = run_migrate_cli(source, target)

    assert result.returncode == EXIT_RECONSTRUCT_FAILED, (
        f"[{cid}] expected exit {EXIT_RECONSTRUCT_FAILED}, got {result.returncode}.\n" f"stderr={result.stderr!r}"
    )
    check_no_traceback_on_stderr(cid, result.stderr)

    report = result.parse_report()
    check_schema_stability(cid, report)
    _assert_failure_stage(report, "reconstruct", cid=cid)
    _assert_anomaly_type_present(report, "target_path_not_directory", cid=cid)
    check_anomaly_well_formedness(cid, report)
    check_failure_stage_is_known(cid, report)

    # Target file must be unchanged (no silent overwrite).
    assert (
        target.read_text(encoding="utf-8") == "I am a file, not a directory"
    ), f"[{cid}] target file was overwritten."
    _assert_source_unchanged(source, before, cid=cid)


# ---------------------------------------------------------------------------
# Case b: target is a non-empty directory
# ---------------------------------------------------------------------------


def test_target_is_nonempty_dir_exit5(tmp_path):
    """migrate must reject --target pointing to a non-empty directory (exit 5)."""
    cid = "target_is_nonempty_dir"
    source = build_minimal_valid_chroma_06(tmp_path / "src")
    target = tmp_path / "target_dir"
    target.mkdir()
    stray = target / "stray.txt"
    stray.write_bytes(b"pre-existing content")

    # Record pre-run target state.
    target_before = {str(p.relative_to(target)): p.read_bytes() for p in sorted(target.rglob("*")) if p.is_file()}
    source_before = _source_snapshot(source)

    result = run_migrate_cli(source, target)

    assert result.returncode == EXIT_RECONSTRUCT_FAILED, (
        f"[{cid}] expected exit {EXIT_RECONSTRUCT_FAILED}, got {result.returncode}.\n" f"stderr={result.stderr!r}"
    )
    check_no_traceback_on_stderr(cid, result.stderr)

    report = result.parse_report()
    check_schema_stability(cid, report)
    _assert_failure_stage(report, "reconstruct", cid=cid)
    _assert_anomaly_type_present(report, "target_path_not_empty", cid=cid)
    check_anomaly_well_formedness(cid, report)

    # Target directory must be identical to its pre-run state.
    target_after = {str(p.relative_to(target)): p.read_bytes() for p in sorted(target.rglob("*")) if p.is_file()}
    assert target_after == target_before, f"[{cid}] target directory state changed after safety rejection."
    _assert_source_unchanged(source, source_before, cid=cid)


# ---------------------------------------------------------------------------
# Case c: target parent is read-only (POSIX-only)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not hasattr(os, "getuid") or os.getuid() == 0,
    reason="POSIX read-only parent test requires non-root POSIX environment",
)
def test_target_parent_readonly_exit5(tmp_path):
    """migrate must reject --target whose parent directory is read-only (POSIX only).

    This tests the manifest-write failure path: the directory itself can
    be created (chromadb may succeed), but writing the target manifest
    will raise a PermissionError, triggering structured rollback.
    """
    cid = "target_parent_readonly"
    source = build_minimal_valid_chroma_06(tmp_path / "src")
    readonly_parent = tmp_path / "readonly_dir"
    readonly_parent.mkdir()
    target = readonly_parent / "my_palace"

    source_before = _source_snapshot(source)

    # Make parent directory read-only (no write permission).
    original_mode = readonly_parent.stat().st_mode
    os.chmod(readonly_parent, stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = run_migrate_cli(source, target)
    finally:
        # Restore permissions so pytest can clean up tmp_path.
        os.chmod(readonly_parent, original_mode)

    assert result.returncode == EXIT_RECONSTRUCT_FAILED, (
        f"[{cid}] expected exit {EXIT_RECONSTRUCT_FAILED}, got {result.returncode}.\n" f"stderr={result.stderr!r}"
    )
    check_no_traceback_on_stderr(cid, result.stderr)

    report = result.parse_report()
    check_schema_stability(cid, report)
    _assert_failure_stage(report, "reconstruct", cid=cid)

    # Acceptable outcomes: manifest-write failure, rollback (chromadb wrote but
    # manifest failed), or mkdir failure (permission denied before any write).
    # target_path_not_directory must NOT appear: the target is a valid directory
    # path — it simply could not be created due to read-only parent permissions.
    types = [a.get("type") for a in (report.get("anomalies") or [])]
    assert any(
        t in types for t in ("target_manifest_write_failed", "reconstruction_rollback", "target_mkdir_failed")
    ), (f"[{cid}] expected a structured rollback/mkdir anomaly, got types: {types}\n" f"stderr={result.stderr!r}")
    assert "target_path_not_directory" not in types, (
        f"[{cid}] target_path_not_directory must not be emitted for a read-only parent "
        f"(the target is a directory, not a file): {types}"
    )

    # Target must not exist after rollback (or, if it existed before, must be
    # absent since we started from a non-existent path).
    # Restore perms for the check.
    os.chmod(readonly_parent, original_mode)
    assert (
        not target.exists()
    ), f"[{cid}] partial target directory still exists after rollback: {list(target.iterdir()) if target.exists() else 'N/A'}"
    os.chmod(readonly_parent, stat.S_IRUSR | stat.S_IXUSR)

    _assert_source_unchanged(source, source_before, cid=cid)
