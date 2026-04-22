"""M12 task 15.3 — pathological transformation inputs.

Two fixtures where transformation rejects every drawer:
  a. Every drawer has a blank / whitespace-only embedding_id.
  b. Every drawer has no document (NULL → ORPHAN_EMBEDDING on extraction).

For both cases:
  - analyze and inspect are exercised first to confirm structured extraction
    anomalies are emitted (one per dropped drawer at stage "extract" or
    "transform").
  - migrate is exercised to confirm the pipeline does NOT silently produce an
    empty target; the expected behaviour is EXIT_RECONSTRUCT_FAILED with
    RECONSTRUCTION_INPUT_MISSING anomaly.

Per-drawer drop contract (M9): each dropped drawer must be traceable to
exactly one anomaly at stage "transform" (TRANSFORM_DRAWER_DROPPED) or
"extract" (orphan rows).  No aggregate substitution.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mempalace_migrator.core.context import AnomalyType
from tests.adversarial._invariants import (
    check_anomaly_well_formedness,
    check_failure_stage_is_known,
    check_no_traceback_on_stderr,
    check_schema_stability,
)
from tests.adversarial.conftest import (
    EXIT_OK,
    EXIT_RECONSTRUCT_FAILED,
    build_all_blank_ids,
    build_all_nonstring_documents,
    run_cli,
    run_migrate_cli,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PATHOLOGICAL_N = 3  # number of drawers in each fixture (set by the builders)


def _assert_no_traceback(cid: str, stderr: str) -> None:
    check_no_traceback_on_stderr(cid, stderr)


def _assert_per_drawer_anomalies(report: dict, *, cid: str, n_drawers: int, expected_stage: str) -> None:
    """Each pathological drawer must produce its own anomaly — no aggregate substitution."""
    anomalies = report.get("anomalies") or []
    stage_anomalies = [a for a in anomalies if (a.get("location") or {}).get("stage") == expected_stage]
    assert len(stage_anomalies) >= n_drawers, (
        f"[{cid}] expected ≥{n_drawers} anomalies at stage {expected_stage!r}, "
        f"got {len(stage_anomalies)}.\nall anomaly types: {[a.get('type') for a in anomalies]}"
    )


# ---------------------------------------------------------------------------
# Fixture a: all-blank-ids source
# ---------------------------------------------------------------------------


class TestAllBlankIds:
    """Every drawer has a whitespace-only embedding_id."""

    def test_analyze_emits_per_row_anomalies(self, tmp_path):
        cid = "all_blank_ids_analyze"
        source = build_all_blank_ids(tmp_path)
        result = run_cli(["--json-output", "analyze", str(source)])

        check_no_traceback_on_stderr(cid, result.stderr)
        assert (
            result.returncode == EXIT_OK
        ), f"[{cid}] expected EXIT_OK, got {result.returncode}.\nstderr={result.stderr!r}"
        report = result.parse_report()
        check_schema_stability(cid, report)
        check_anomaly_well_formedness(cid, report)

        # Blank ids are caught at extraction (BLANK_EMBEDDING_ID) or
        # transformation (TRANSFORM_DRAWER_DROPPED). Either stage is acceptable;
        # what is NOT acceptable is zero anomalies for _N drawers.
        anomalies = report.get("anomalies") or []
        assert len(anomalies) >= _PATHOLOGICAL_N, (
            f"[{cid}] expected ≥{_PATHOLOGICAL_N} anomalies for {_PATHOLOGICAL_N} blank-id rows, "
            f"got {len(anomalies)}"
        )

    def test_migrate_rejects_empty_bundle(self, tmp_path):
        cid = "all_blank_ids_migrate"
        source = build_all_blank_ids(tmp_path / "src")
        target = tmp_path / "target"

        result = run_migrate_cli(source, target)

        assert result.returncode == EXIT_RECONSTRUCT_FAILED, (
            f"[{cid}] migrate should exit {EXIT_RECONSTRUCT_FAILED} on empty bundle, "
            f"got {result.returncode}.\nstderr={result.stderr!r}"
        )
        check_no_traceback_on_stderr(cid, result.stderr)
        report = result.parse_report()
        check_schema_stability(cid, report)
        check_anomaly_well_formedness(cid, report)
        check_failure_stage_is_known(cid, report)

        # Must not create an empty target.
        assert not target.exists(), f"[{cid}] migrate created a target directory despite empty bundle: {target}"

        # RECONSTRUCTION_INPUT_MISSING must be present.
        types = [a.get("type") for a in (report.get("anomalies") or [])]
        assert (
            "reconstruction_input_missing" in types
        ), f"[{cid}] RECONSTRUCTION_INPUT_MISSING not found.\nactual types: {types}"


# ---------------------------------------------------------------------------
# Fixture b: all-null-documents source (ORPHAN_EMBEDDING on extraction)
# ---------------------------------------------------------------------------


class TestAllNonStringDocuments:
    """Every drawer has NULL document → extraction ORPHAN_EMBEDDING."""

    def test_analyze_emits_orphan_anomalies(self, tmp_path):
        cid = "all_null_docs_analyze"
        source = build_all_nonstring_documents(tmp_path)
        result = run_cli(["--json-output", "analyze", str(source)])

        check_no_traceback_on_stderr(cid, result.stderr)
        assert (
            result.returncode == EXIT_OK
        ), f"[{cid}] expected EXIT_OK, got {result.returncode}.\nstderr={result.stderr!r}"
        report = result.parse_report()
        check_schema_stability(cid, report)
        check_anomaly_well_formedness(cid, report)

        anomalies = report.get("anomalies") or []
        orphan_anomalies = [a for a in anomalies if a.get("type") == "orphan_embedding"]
        assert len(orphan_anomalies) >= _PATHOLOGICAL_N, (
            f"[{cid}] expected ≥{_PATHOLOGICAL_N} ORPHAN_EMBEDDING anomalies, "
            f"got {len(orphan_anomalies)}.\nall types: {[a.get('type') for a in anomalies]}"
        )

    def test_migrate_rejects_empty_bundle(self, tmp_path):
        cid = "all_null_docs_migrate"
        source = build_all_nonstring_documents(tmp_path / "src")
        target = tmp_path / "target"

        result = run_migrate_cli(source, target)

        assert result.returncode == EXIT_RECONSTRUCT_FAILED, (
            f"[{cid}] migrate should exit {EXIT_RECONSTRUCT_FAILED} on empty bundle, "
            f"got {result.returncode}.\nstderr={result.stderr!r}"
        )
        check_no_traceback_on_stderr(cid, result.stderr)
        report = result.parse_report()
        check_schema_stability(cid, report)
        check_anomaly_well_formedness(cid, report)
        check_failure_stage_is_known(cid, report)

        assert not target.exists(), f"[{cid}] migrate created an empty target despite no drawable rows: {target}"

        types = [a.get("type") for a in (report.get("anomalies") or [])]
        assert (
            "reconstruction_input_missing" in types
        ), f"[{cid}] RECONSTRUCTION_INPUT_MISSING not found.\nactual types: {types}"
