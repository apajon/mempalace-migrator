"""M12 task 15.10 — duplicate-id ingestion: contract chain verification.

Contract chain to pin (§4.10 of M12 design):
  1. Extraction emits DUPLICATE_EMBEDDING_IDS when two source rows share an id.
  2. Transformation either drops the duplicate(s) with structured anomalies
     OR passes both through depending on current behaviour.
  3. If duplicates reach the writer, collection.add raises. M12 asserts:
       rc == 5, RECONSTRUCTION_ROLLBACK emitted, target absent, source unchanged.

The test parametrises on the chain outcome:
  - Branch A: transformation filters the duplicate.
    Assertion: no target, structured transformation anomalies present.
  - Branch B: writer sees duplicates and atomic rollback fires.
    Assertion: rollback invariants (rc=5, RECONSTRUCTION_ROLLBACK, no target).

The one and only *forbidden* outcome: both duplicates written, one silently
overwriting the other (outcome == "success" with only 2 records when 3 are
expected, or 3 records for the 3-row fixture where 1 is a duplicate).
"""

from __future__ import annotations

import pytest

from tests.adversarial.conftest import (
    EXIT_OK,
    EXIT_RECONSTRUCT_FAILED,
    build_duplicate_ids_for_writer,
    run_migrate_cli,
)

# Fixture: build_duplicate_ids_for_writer creates 3 rows — ids: "dup", "dup", "ok".
# Extraction drops ALL occurrences of any duplicated id, so only "ok" survives.
# Observed-chain contract: imported_count == 1 on a successful migrate.
_FIXTURE_TOTAL_ROWS = 3
_FIXTURE_UNIQUE_IDS = 1  # only "ok" survives; both "dup" rows are dropped at extraction


def _source_snapshot(source):
    from pathlib import Path

    return {str(p.relative_to(source)): p.read_bytes() for p in sorted(Path(source).rglob("*")) if p.is_file()}


def test_duplicate_ids_do_not_silently_overwrite(tmp_path):
    """Silent overwrite of a duplicate id must never succeed undetected.

    This test covers both valid outcomes:
      A) transformation deduplicates: migrate succeeds with unique-id count,
         and TRANSFORM_DUPLICATE_ID_DROPPED anomaly is present.
      B) writer encounters duplicates: migrate exits 5 with RECONSTRUCTION_ROLLBACK.

    If the migration "succeeds" with imported_count == total_rows (not unique),
    that means a duplicate was silently written — which is the forbidden case.
    """
    source = build_duplicate_ids_for_writer(tmp_path / "src")
    target = tmp_path / "target"
    source_before = _source_snapshot(source)

    result = run_migrate_cli(source, target)

    if result.returncode == EXIT_OK:
        # Branch A: migration succeeded — deduplication happened upstream.
        report = result.parse_report()
        assert report.get("outcome") == "success"

        # imported_count must equal the number of unique ids (not total rows).
        reconstruction = report.get("reconstruction") or {}
        imported = reconstruction.get("imported_count")

        # Forbidden: silent overwrite (imported == total rows while there are
        # duplicates).  The only acceptable success count is unique-id count.
        assert imported == _FIXTURE_UNIQUE_IDS, (
            f"[duplicate_ids] migrate succeeded but imported_count={imported!r} != "
            f"expected {_FIXTURE_UNIQUE_IDS} (unique ids); "
            f"this indicates a duplicate was silently overwritten."
        )

        # Transformation must have emitted a deduplication anomaly.
        # Depending on where duplicates are caught:
        #   - extraction stage: DUPLICATE_EMBEDDING_IDS
        #   - transformation stage: TRANSFORM_DUPLICATE_ID_DROPPED
        _DEDUP_ANOMALY_TYPES = {"duplicate_embedding_ids", "transform_duplicate_id_dropped"}
        types = [a.get("type") for a in (report.get("anomalies") or [])]
        assert any(t in _DEDUP_ANOMALY_TYPES for t in types), (
            f"[duplicate_ids] migration succeeded but no deduplication anomaly "
            f"({_DEDUP_ANOMALY_TYPES}) present.\nactual types: {types}"
        )

        # Source must be unchanged.
        after = _source_snapshot(source)
        assert after == source_before, "[duplicate_ids] source was mutated during migration."

    elif result.returncode == EXIT_RECONSTRUCT_FAILED:
        # Branch B: writer saw duplicates; rollback must have fired.
        report = result.parse_report()
        failure = report.get("failure") or {}
        assert (
            failure.get("stage") == "reconstruct"
        ), f"[duplicate_ids] failure.stage={failure.get('stage')!r}, expected 'reconstruct'"
        types = [a.get("type") for a in (report.get("anomalies") or [])]
        assert (
            "reconstruction_rollback" in types
        ), f"[duplicate_ids] RECONSTRUCTION_ROLLBACK not found.\nactual types: {types}"
        assert not target.exists(), f"[duplicate_ids] target still exists after rollback: {target}"
        after = _source_snapshot(source)
        assert after == source_before, "[duplicate_ids] source was mutated during migration."

    else:
        pytest.fail(
            f"[duplicate_ids] unexpected exit code {result.returncode}; "
            f"expected {EXIT_OK} (dedup success) or {EXIT_RECONSTRUCT_FAILED} (writer rollback).\n"
            f"stderr={result.stderr!r}"
        )
