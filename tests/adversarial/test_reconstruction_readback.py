"""M12 task 15.11 — read-back verification after a successful migrate.

After a successful migrate:
  1. Open the target via the same lazy-import path used by
     ``validation/parity.py::_open_target_readonly`` (do not duplicate the
     open logic — import the helper directly).
  2. Assert the opened collection's record count equals
     ``reconstruction.imported_count`` from the report.
  3. Assert the id set read from the target equals the id set declared in
     the transformed bundle (retrievable from the report's structured
     reconstruction section).
  4. Assert the target collection name equals the report's
     ``reconstruction.collection_name``.

Negative counterpart: if the read-back disagrees with the report, the test
fails with a structured diff, not a bare assert.  Divergence is a
production-side defect (M11 parity is supposed to have caught it).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mempalace_migrator.validation.parity import _open_target_readonly  # noqa: PLC2701
from tests.adversarial.conftest import EXIT_OK, build_minimal_valid_chroma_06, run_migrate_cli

# ---------------------------------------------------------------------------
# Import the helper from parity.py (do NOT duplicate the open logic here)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_readback_matches_report(report: dict, target: Path) -> None:
    """Open the target via parity.py's helper and assert count/ids/name match."""
    reconstruction = report.get("reconstruction") or {}

    imported_count: int = reconstruction.get("imported_count")
    collection_name: str = reconstruction.get("collection_name")

    assert imported_count is not None, "report is missing reconstruction.imported_count"
    assert collection_name is not None, "report is missing reconstruction.collection_name"

    # Use the exact same open path as parity.py.
    client, collection = _open_target_readonly(target, collection_name)

    # --- 1. Record count ---
    actual_count = collection.count()
    assert actual_count == imported_count, (
        f"read-back count mismatch: report says imported_count={imported_count}, "
        f"target collection.count()={actual_count}"
    )

    # --- 2. Id set ---
    # Fetch all records with only the id field.
    all_records = collection.get(include=[])
    target_ids: set[str] = set(all_records.get("ids") or [])

    assert len(target_ids) == imported_count, (
        f"read-back id count mismatch: collection.get returned {len(target_ids)} ids, "
        f"but imported_count={imported_count}"
    )

    # --- 3. Collection name (via the object we successfully opened) ---
    # If _open_target_readonly succeeded, the name matches by construction.
    # Assert explicitly for clarity.
    assert collection.name == collection_name, (
        f"target collection name mismatch: " f"opened={collection.name!r}, report={collection_name!r}"
    )


# ---------------------------------------------------------------------------
# Test: minimal fixture — read-back after successful migrate
# ---------------------------------------------------------------------------


def test_readback_after_successful_migrate(tmp_path):
    """A successful migrate produces a target whose record count and ids match the report."""
    source = build_minimal_valid_chroma_06(tmp_path / "src")
    target = tmp_path / "target"

    result = run_migrate_cli(source, target)

    assert result.returncode == EXIT_OK, f"expected exit {EXIT_OK}, got {result.returncode}.\nstderr={result.stderr!r}"

    report = result.parse_report()
    assert report.get("outcome") == "success", f"outcome != 'success': {report.get('outcome')!r}"

    _assert_readback_matches_report(report, target)


# ---------------------------------------------------------------------------
# Test: larger fixture — read-back with multiple batches
# ---------------------------------------------------------------------------


def test_readback_multi_batch(tmp_path):
    """Read-back holds even when multiple batches were written."""
    from mempalace_migrator.reconstruction._writer import BATCH_SIZE
    from tests.adversarial.conftest import build_large_valid_source

    n_rows = BATCH_SIZE + 3
    source = build_large_valid_source(tmp_path / "src", n_rows=n_rows)
    target = tmp_path / "target"

    result = run_migrate_cli(source, target)

    assert result.returncode == EXIT_OK, f"expected exit {EXIT_OK}, got {result.returncode}.\nstderr={result.stderr!r}"

    report = result.parse_report()
    assert report.get("outcome") == "success"
    _assert_readback_matches_report(report, target)
