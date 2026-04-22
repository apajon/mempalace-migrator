"""M13 — End-to-End Migration Usability Gate (phase 16, tasks 16.1–16.7).

Proves the ``migrate`` command is usable end-to-end on the minimal valid
``chroma_0_6`` fixture (3 drawers) under the documented constraints.

No new pipeline stage, no new exit code, no new ``AnomalyType``.
Reuses helpers from ``test_cli_migrate.py``, ``hardening/conftest.py``,
and ``adversarial/_invariants.py`` verbatim — no duplicates.

All seven tests must be green for M13 to be considered done.  See
``tests/M13_E2E_USABILITY_DESIGN.md`` §9 for the full exit-gate
checklist.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from mempalace_migrator.cli.main import EXIT_OK
from mempalace_migrator.detection.format_detector import (MANIFEST_FILENAME,
                                                          SQLITE_FILENAME)
from mempalace_migrator.extraction.chroma_06_reader import \
    EXPECTED_COLLECTION_NAME
from mempalace_migrator.reconstruction._manifest import \
    TARGET_MANIFEST_FILENAME
from tests.adversarial._invariants import check_no_forbidden_vocabulary
from tests.hardening.conftest import extract_report_signature
from tests.test_cli_migrate import (_make_valid_db, _sha256,  # noqa: F401
                                    _write_manifest, make_valid_palace)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_N_DRAWERS = 3

# The four target-parity check ids that must never appear in checks_not_performed
# after a successful migrate run (M11 contract, re-proven end-to-end here).
_REQUIRED_PARITY_CHECK_IDS: frozenset[str] = frozenset(
    {
        "target_record_count_parity",
        "target_id_set_parity",
        "target_document_hash_parity",
        "target_metadata_parity",
    }
)

_TODO_PATH = Path(__file__).parent / "TODO.json"
_ROADMAP_PATH = Path(__file__).parent / "ROADMAP.json"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _run_migrate(source: Path, target: Path) -> tuple[int, dict[str, Any], str]:
    """Run ``--json-output migrate SOURCE --target TARGET`` in a subprocess.

    Returns ``(exit_code, report_dict, stderr_text)``.  ``report_dict`` is
    empty if stdout was empty (only happens on non-0 exits).
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mempalace_migrator.cli.main",
            "--json-output",
            "migrate",
            str(source),
            "--target",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    report: dict[str, Any] = {}
    if proc.stdout.strip():
        report = json.loads(proc.stdout)
    return proc.returncode, report, proc.stderr


def _snapshot_source(source: Path) -> dict[str, Any]:
    """Capture sha256 + mtime_ns for manifest and sqlite, plus the filename set.

    Used by 16.5 to assert the source is byte- and mtime-identical before
    and after a full ``migrate`` invocation.
    """
    manifest_path = source / MANIFEST_FILENAME
    sqlite_path = source / SQLITE_FILENAME
    return {
        "manifest_sha256": _sha256(manifest_path) if manifest_path.exists() else None,
        "manifest_mtime_ns": manifest_path.stat().st_mtime_ns if manifest_path.exists() else None,
        "sqlite_sha256": _sha256(sqlite_path) if sqlite_path.exists() else None,
        "sqlite_mtime_ns": sqlite_path.stat().st_mtime_ns if sqlite_path.exists() else None,
        "filenames": {p.name for p in source.iterdir()},
    }


# ---------------------------------------------------------------------------
# 16.1 — Successful migrate command contract
# ---------------------------------------------------------------------------


def test_migrate_happy_path_exits_zero_and_builds_target(tmp_path: Path) -> None:
    """16.1: migrate exits 0, writes manifest, executes all five stages, imports 3 drawers."""
    source = tmp_path / "source"
    source.mkdir()
    make_valid_palace(source, n_drawers=_N_DRAWERS)
    target = tmp_path / "target"

    rc, report, stderr = _run_migrate(source, target)

    assert rc == EXIT_OK, (
        f"expected exit 0; got {rc}.\nstderr={stderr!r}\nstdout={json.dumps(report, indent=2)}"
    )
    assert (target / TARGET_MANIFEST_FILENAME).is_file(), (
        f"target manifest not found at {target / TARGET_MANIFEST_FILENAME}"
    )

    stages = report.get("stages") or {}
    for stage_name in ("detect", "extract", "transform", "reconstruct", "validate"):
        st = (stages.get(stage_name) or {}).get("status")
        assert st == "executed", (
            f"stage {stage_name!r} has status {st!r} (expected 'executed').\n"
            f"full stages section: {json.dumps(stages, indent=2)}"
        )

    imported = (report.get("reconstruction") or {}).get("imported_count")
    assert imported == _N_DRAWERS, (
        f"imported_count={imported!r} (expected {_N_DRAWERS}).\n"
        f"report.reconstruction={report.get('reconstruction')}"
    )


# ---------------------------------------------------------------------------
# 16.2 — No skipped parity checks after successful migrate
# ---------------------------------------------------------------------------


def test_parity_checks_are_executed_not_skipped(tmp_path: Path) -> None:
    """16.2: all four required target-parity checks are performed, none skipped."""
    source = tmp_path / "source"
    source.mkdir()
    make_valid_palace(source, n_drawers=_N_DRAWERS)
    target = tmp_path / "target"

    rc, report, stderr = _run_migrate(source, target)
    assert rc == EXIT_OK, f"migrate failed; stderr={stderr!r}"

    validation = report.get("validation") or {}
    skipped_ids = {entry["id"] for entry in validation.get("checks_not_performed") or []}
    overlap = skipped_ids & _REQUIRED_PARITY_CHECK_IDS
    assert not overlap, (
        f"these parity checks were skipped but must be performed after a "
        f"successful migrate: {sorted(overlap)}.\n"
        f"checks_not_performed: {validation.get('checks_not_performed')}"
    )

    # Every performed check with family == "parity" must carry a trichotomous status.
    performed = validation.get("checks_performed") or []
    parity_performed = [c for c in performed if c.get("family") == "parity"]
    valid_statuses = {"passed", "failed", "inconclusive"}
    for chk in parity_performed:
        assert chk.get("status") in valid_statuses, (
            f"parity check {chk.get('id')!r} has unexpected status {chk.get('status')!r}; "
            f"must be one of {sorted(valid_statuses)}"
        )


# ---------------------------------------------------------------------------
# 16.3 — Confidence and honesty gate
# ---------------------------------------------------------------------------


def test_confidence_and_no_forbidden_vocabulary(tmp_path: Path) -> None:
    """16.3: confidence_band ≥ MEDIUM, no forbidden vocabulary, no hidden CRITICAL."""
    source = tmp_path / "source"
    source.mkdir()
    make_valid_palace(source, n_drawers=_N_DRAWERS)
    target = tmp_path / "target"

    rc, report, _stderr = _run_migrate(source, target)
    assert rc == EXIT_OK

    # Confidence gate: LOW and UNKNOWN are rejected on the happy path.
    band = (report.get("validation") or {}).get("confidence_band", "UNKNOWN")
    assert band in ("HIGH", "MEDIUM"), (
        f"confidence_band={band!r} on the happy path (expected HIGH or MEDIUM).\n"
        f"validation={json.dumps(report.get('validation'), indent=2)}"
    )

    # No forbidden correctness vocabulary in the JSON report (M7 Inv. 7).
    check_no_forbidden_vocabulary("M13.16.3", report)

    # No CRITICAL anomaly attached to any executed stage (positive counterpart of M7 Inv. 3).
    stages = report.get("stages") or {}
    anomalies = report.get("anomalies") or []
    for stage_name, stage_info in stages.items():
        if (stage_info or {}).get("status") == "executed":
            critical_in_stage = [
                a
                for a in anomalies
                if a.get("severity") == "critical"
                and (a.get("location") or {}).get("stage") == stage_name
            ]
            assert not critical_in_stage, (
                f"stage {stage_name!r} is 'executed' but carries CRITICAL anomalies: "
                f"{critical_in_stage}"
            )


# ---------------------------------------------------------------------------
# 16.4 — Repeatability on fresh targets
# ---------------------------------------------------------------------------


def test_two_fresh_target_runs_are_repeatable(tmp_path: Path) -> None:
    """16.4: two migrate runs against distinct targets produce identical stable signatures."""
    source = tmp_path / "source"
    source.mkdir()
    make_valid_palace(source, n_drawers=_N_DRAWERS)
    target_a = tmp_path / "target_a"
    target_b = tmp_path / "target_b"

    rc_a, report_a, stderr_a = _run_migrate(source, target_a)
    rc_b, report_b, stderr_b = _run_migrate(source, target_b)

    assert rc_a == EXIT_OK, f"first run failed; stderr={stderr_a!r}"
    assert rc_b == EXIT_OK, f"second run failed; stderr={stderr_b!r}"

    count_a = (report_a.get("reconstruction") or {}).get("imported_count")
    count_b = (report_b.get("reconstruction") or {}).get("imported_count")
    assert count_a == count_b, f"imported_count differs between runs: {count_a} vs {count_b}"

    def _parity_statuses(report: dict[str, Any]) -> dict[str, str]:
        performed = (report.get("validation") or {}).get("checks_performed") or []
        return {c["id"]: c["status"] for c in performed if c.get("family") == "parity"}

    statuses_a = _parity_statuses(report_a)
    statuses_b = _parity_statuses(report_b)
    assert statuses_a == statuses_b, (
        f"parity statuses differ between runs.\nrun_a: {statuses_a}\nrun_b: {statuses_b}"
    )

    # Stable report signatures must be byte-equal (modulo volatile fields redacted
    # by extract_report_signature: run_id, started_at, completed_at,
    # target_manifest_path, chromadb_version, duration fields).
    sig_a = extract_report_signature(report_a, rc_a)
    sig_b = extract_report_signature(report_b, rc_b)
    assert sig_a == sig_b, (
        f"report signatures differ between two consecutive migrate runs.\n"
        f"run_a: {json.dumps(sig_a, indent=2)}\n"
        f"run_b: {json.dumps(sig_b, indent=2)}"
    )


# ---------------------------------------------------------------------------
# 16.5 — End-to-end source invariance
# ---------------------------------------------------------------------------


def test_source_bytes_and_mtime_invariant_across_full_command(tmp_path: Path) -> None:
    """16.5: migrate leaves source files byte- and mtime-identical before and after."""
    source = tmp_path / "source"
    source.mkdir()
    make_valid_palace(source, n_drawers=_N_DRAWERS)
    target = tmp_path / "target"

    before = _snapshot_source(source)
    rc, _report, stderr = _run_migrate(source, target)
    after = _snapshot_source(source)

    assert rc == EXIT_OK, f"migrate failed; stderr={stderr!r}"

    assert after["manifest_sha256"] == before["manifest_sha256"], (
        "source manifest sha256 changed after migrate"
    )
    assert after["manifest_mtime_ns"] == before["manifest_mtime_ns"], (
        "source manifest mtime_ns changed after migrate"
    )
    assert after["sqlite_sha256"] == before["sqlite_sha256"], (
        "source sqlite sha256 changed after migrate"
    )
    assert after["sqlite_mtime_ns"] == before["sqlite_mtime_ns"], (
        "source sqlite mtime_ns changed after migrate"
    )
    assert after["filenames"] == before["filenames"], (
        f"source directory filenames changed after migrate.\n"
        f"before: {sorted(before['filenames'])}\n"
        f"after : {sorted(after['filenames'])}"
    )


# ---------------------------------------------------------------------------
# 16.6 — Readable target smoke test
# ---------------------------------------------------------------------------


def test_reconstructed_target_reopens_in_fresh_process(tmp_path: Path) -> None:
    """16.6: the target can be opened via chromadb.PersistentClient in a fresh process."""
    source = tmp_path / "source"
    source.mkdir()
    make_valid_palace(source, n_drawers=_N_DRAWERS)
    target = tmp_path / "target"

    rc, report, stderr = _run_migrate(source, target)
    assert rc == EXIT_OK, f"migrate failed; stderr={stderr!r}"

    expected_count = (report.get("reconstruction") or {}).get("imported_count")
    assert expected_count is not None, "report.reconstruction.imported_count is absent"

    # Use a fresh Python interpreter — not in-process — so that any cached
    # sqlite handles or chromadb client state from the writer cannot mask a
    # defect where the target is only readable because the writer's process
    # is still alive (M13_E2E_USABILITY_DESIGN.md §6.6).
    script = textwrap.dedent(
        f"""\
        import chromadb
        client = chromadb.PersistentClient(path={str(target)!r})
        col = client.get_collection({EXPECTED_COLLECTION_NAME!r})
        print(col.count())
        """
    )
    reopen = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert reopen.returncode == 0, (
        f"fresh-process target reopen exited {reopen.returncode}.\nstderr={reopen.stderr!r}"
    )
    assert not reopen.stderr.strip(), (
        f"fresh-process target reopen printed to stderr: {reopen.stderr!r}"
    )
    actual_count = int(reopen.stdout.strip())
    assert actual_count == expected_count, (
        f"reopened collection.count()={actual_count} != "
        f"report.reconstruction.imported_count={expected_count}"
    )


# ---------------------------------------------------------------------------
# 16.7 — Current-position promotion rule
# ---------------------------------------------------------------------------


def test_todo_promotion_rule_is_self_consistent() -> None:
    """16.7: ROADMAP.json must not claim M13_done unless 16.1–16.6 are all 'done'."""
    todo = json.loads(_TODO_PATH.read_text(encoding="utf-8"))
    roadmap = json.loads(_ROADMAP_PATH.read_text(encoding="utf-8"))

    phase16 = next((p for p in todo.get("phases") or [] if p.get("id") == 16), None)
    assert phase16 is not None, "phase 16 not found in tests/TODO.json"

    gated_task_ids = {"16.1", "16.2", "16.3", "16.4", "16.5", "16.6"}
    task_statuses = {
        t["id"]: t["status"]
        for t in (phase16.get("tasks") or [])
        if t.get("id") in gated_task_ids
    }
    # All six gated tasks must be present in TODO.json.
    missing_tasks = gated_task_ids - task_statuses.keys()
    assert not missing_tasks, (
        f"tasks {sorted(missing_tasks)} not found in phase 16 of tests/TODO.json"
    )

    all_done = all(s == "done" for s in task_statuses.values())
    current_pos = roadmap.get("current_position") or {}
    current_milestone = current_pos.get("milestone", "")
    completed = current_pos.get("completed_milestones") or []

    if not all_done:
        assert current_milestone != "M13_done", (
            f"ROADMAP.json claims current_position.milestone='M13_done' "
            f"but not all 16.1–16.6 tasks are 'done'.\n"
            f"task statuses: {task_statuses}"
        )
        assert "M13" not in completed, (
            f"ROADMAP.json lists 'M13' in completed_milestones "
            f"but 16.1–16.6 are not all 'done'.\n"
            f"task statuses: {task_statuses}"
        )
            f"but not all 16.1–16.6 tasks are 'done'.\n"
            f"task statuses: {task_statuses}"
        )
        assert "M13" not in completed, (
            f"ROADMAP.json lists 'M13' in completed_milestones "
            f"but 16.1–16.6 are not all 'done'.\n"
            f"task statuses: {task_statuses}"
        )
