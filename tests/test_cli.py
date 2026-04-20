"""M6 exit-gate tests — User Access (CLI).

Exit gate: "User can run end-to-end pipeline."

Coverage map:
  9.1  Input handling         -> test_analyze_*, test_inspect_*, test_report_*
  9.2  Output display         -> test_json_output_*, test_text_output_*, test_quiet_*
  9.3  Execution modes        -> test_analyze_*, test_inspect_*, test_report_*
  9.4  Exit-code policy       -> test_decide_exit_code_*
       Stdout/stderr purity   -> test_json_output_stdout_is_pure_json

What is NOT tested here (deliberate):
  - Real filesystem permission errors (requires OS-level setup).
  - Interactive TTY detection (not implemented in M6).
  - Config files / env-var overrides (out of scope for M6).
  - transform / reconstruct actual execution (stubs only).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from mempalace_migrator.cli.main import (
    EXIT_CRITICAL_ANOMALY,
    EXIT_DETECTION_FAILED,
    EXIT_OK,
    EXIT_REPORT_FAILED,
    EXIT_REPORT_FILE_ERROR,
    EXIT_UNEXPECTED,
    EXIT_USAGE_ERROR,
    _decide_exit_code,
    cli,
)
from mempalace_migrator.core.errors import MigratorError
from mempalace_migrator.detection.format_detector import MANIFEST_FILENAME, SQLITE_FILENAME
from mempalace_migrator.extraction.chroma_06_reader import EXPECTED_COLLECTION_NAME

# ---------------------------------------------------------------------------
# Palace fixture helpers
# ---------------------------------------------------------------------------

_MANIFEST_06 = {"compatibility_line": "chromadb-0.6.x", "chromadb_version": "0.6.3"}


def _write_manifest(root: Path, data: dict[str, Any] | None = None) -> None:
    (root / MANIFEST_FILENAME).write_text(json.dumps(data or _MANIFEST_06))


def _make_valid_db(root: Path, *, n_drawers: int = 1) -> None:
    """Create a minimal valid chroma.sqlite3 that passes extraction."""
    db_path = root / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE collections (
            id   INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE embeddings (
            id            INTEGER PRIMARY KEY,
            collection_id INTEGER,
            embedding_id  TEXT
        );
        CREATE TABLE embedding_metadata (
            id           INTEGER NOT NULL,
            key          TEXT    NOT NULL,
            string_value TEXT,
            int_value    INTEGER,
            float_value  REAL,
            bool_value   INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO collections (id, name) VALUES (1, ?)",
        (EXPECTED_COLLECTION_NAME,),
    )
    for i in range(1, n_drawers + 1):
        conn.execute(
            "INSERT INTO embeddings (id, collection_id, embedding_id) VALUES (?, 1, ?)",
            (i, f"drawer-{i}"),
        )
        conn.execute(
            "INSERT INTO embedding_metadata (id, key, string_value) VALUES (?, 'chroma:document', ?)",
            (i, f"content of drawer {i}"),
        )
    conn.commit()
    conn.close()


def make_valid_palace(root: Path, *, n_drawers: int = 1) -> Path:
    """Return *root* configured as a minimal valid chroma_0_6 palace."""
    _write_manifest(root)
    _make_valid_db(root, n_drawers=n_drawers)
    return root


# ---------------------------------------------------------------------------
# _decide_exit_code unit tests (pure function; no I/O)
# ---------------------------------------------------------------------------


class TestDecideExitCode:
    def _report(self, outcome: str = "success", top_severity: str = "none") -> dict[str, Any]:
        return {
            "outcome": outcome,
            "anomaly_summary": {"top_severity": top_severity},
        }

    def test_success_no_anomalies_returns_0(self) -> None:
        assert _decide_exit_code(self._report(), None) == EXIT_OK

    def test_success_low_anomaly_returns_0(self) -> None:
        assert _decide_exit_code(self._report(top_severity="low"), None) == EXIT_OK

    def test_success_high_anomaly_returns_0(self) -> None:
        # HIGH alone does not warrant EXIT_CRITICAL_ANOMALY; only CRITICAL does.
        assert _decide_exit_code(self._report(top_severity="high"), None) == EXIT_OK

    def test_success_critical_anomaly_returns_8(self) -> None:
        assert _decide_exit_code(self._report(top_severity="critical"), None) == EXIT_CRITICAL_ANOMALY

    def test_raised_detect_returns_2(self) -> None:
        exc = MigratorError(stage="detect", code="x", summary="s")
        assert _decide_exit_code(self._report(), exc) == EXIT_DETECTION_FAILED

    def test_raised_extract_returns_3(self) -> None:
        from mempalace_migrator.cli.main import EXIT_EXTRACTION_FAILED

        exc = MigratorError(stage="extract", code="x", summary="s")
        assert _decide_exit_code(self._report(), exc) == EXIT_EXTRACTION_FAILED

    def test_raised_report_returns_6(self) -> None:
        exc = MigratorError(stage="report", code="x", summary="s")
        assert _decide_exit_code(self._report(), exc) == EXIT_REPORT_FAILED

    def test_raised_unknown_stage_returns_10(self) -> None:
        exc = MigratorError(stage="nonexistent_stage", code="x", summary="s")
        assert _decide_exit_code(self._report(), exc) == EXIT_UNEXPECTED

    def test_none_report_returns_unexpected(self) -> None:
        assert _decide_exit_code(None, None) == EXIT_UNEXPECTED

    def test_failure_outcome_without_raised_returns_unexpected(self) -> None:
        assert _decide_exit_code(self._report(outcome="failure"), None) == EXIT_UNEXPECTED

    def test_missing_anomaly_summary_treated_as_none_severity(self) -> None:
        report = {"outcome": "success"}  # no anomaly_summary key
        assert _decide_exit_code(report, None) == EXIT_OK


# ---------------------------------------------------------------------------
# analyze subcommand
# ---------------------------------------------------------------------------


class TestAnalyze:
    def test_valid_palace_exits_0(self, tmp_path: Path) -> None:
        make_valid_palace(tmp_path)
        result = CliRunner().invoke(cli, ["analyze", str(tmp_path)])
        assert result.exit_code == EXIT_OK

    def test_valid_palace_report_contains_outcome_success(self, tmp_path: Path) -> None:
        make_valid_palace(tmp_path)
        result = CliRunner().invoke(cli, ["--json-output", "analyze", str(tmp_path)])
        assert result.exit_code == EXIT_OK
        report = json.loads(result.output)
        assert report["outcome"] == "success"

    def test_valid_palace_json_schema_version_present(self, tmp_path: Path) -> None:
        make_valid_palace(tmp_path)
        result = CliRunner().invoke(cli, ["--json-output", "analyze", str(tmp_path)])
        report = json.loads(result.output)
        assert "schema_version" in report
        assert isinstance(report["schema_version"], int)

    def test_empty_dir_exits_2_detection_failed(self, tmp_path: Path) -> None:
        # No manifest → detection fails → exit 2.
        result = CliRunner().invoke(cli, ["analyze", str(tmp_path)])
        assert result.exit_code == EXIT_DETECTION_FAILED

    def test_empty_dir_stderr_contains_detect_stage(self, tmp_path: Path) -> None:
        # In Click 8.2+, CliRunner mixes stderr into result.output.
        # The banner is written with err=True, so it appears in combined output.
        result = CliRunner().invoke(cli, ["analyze", str(tmp_path)])
        assert "[detect]" in result.output

    def test_nonexistent_dir_click_rejects_before_pipeline(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        result = CliRunner().invoke(cli, ["analyze", str(missing)])
        # Click's exists=True guard; exits non-zero without entering the pipeline.
        # CliRunner bypasses main() so it sees Click's raw UsageError exit code (2).
        assert result.exit_code != EXIT_OK

    def test_nonexistent_dir_subprocess_exits_usage_error(self, tmp_path: Path) -> None:
        # Via the real entry point, main() remaps Click's UsageError exit(2) to
        # EXIT_USAGE_ERROR (1), keeping it distinct from EXIT_DETECTION_FAILED (2).
        missing = tmp_path / "does-not-exist"
        proc = _invoke_subprocess(["analyze", str(missing)])
        assert proc.returncode == EXIT_USAGE_ERROR

    def test_text_output_contains_run_id(self, tmp_path: Path) -> None:
        make_valid_palace(tmp_path)
        result = CliRunner().invoke(cli, ["analyze", str(tmp_path)])
        assert "run_id:" in result.output

    def test_text_output_contains_outcome(self, tmp_path: Path) -> None:
        make_valid_palace(tmp_path)
        result = CliRunner().invoke(cli, ["analyze", str(tmp_path)])
        assert "outcome: success" in result.output

    def test_quiet_flag_suppresses_output(self, tmp_path: Path) -> None:
        make_valid_palace(tmp_path)
        result = CliRunner().invoke(cli, ["--quiet", "analyze", str(tmp_path)])
        assert result.exit_code == EXIT_OK
        assert result.output.strip() == ""

    def test_quiet_flag_still_correct_exit_code_on_failure(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(cli, ["--quiet", "analyze", str(tmp_path)])
        assert result.exit_code == EXIT_DETECTION_FAILED

    def test_partial_report_emitted_on_extraction_failure(self, tmp_path: Path) -> None:
        # Palace detected but SQLite missing → extraction fails.
        # The report should still be emitted and be valid JSON.
        # Uses subprocess to capture stdout/stderr separately since Click 8.2+
        # CliRunner mixes them.
        _write_manifest(tmp_path)
        proc = _invoke_subprocess(["--json-output", "analyze", str(tmp_path)])
        assert proc.returncode != EXIT_OK
        report = json.loads(proc.stdout)
        assert "run_id" in report
        assert report["outcome"] == "failure"


# ---------------------------------------------------------------------------
# JSON output purity (stdout must be exclusively valid JSON)
# ---------------------------------------------------------------------------


def _invoke_subprocess(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the CLI via subprocess to capture stdout/stderr separately.

    Required because Click 8.2+ CliRunner mixes stderr into result.output,
    making stdout purity assertions impossible with CliRunner alone.
    """
    return subprocess.run(
        [sys.executable, "-m", "mempalace_migrator.cli.main"] + args,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).parent.parent / "src")},
    )


class TestJsonOutputPurity:
    def test_json_output_stdout_is_valid_json_on_success(self, tmp_path: Path) -> None:
        make_valid_palace(tmp_path)
        proc = _invoke_subprocess(["--json-output", "analyze", str(tmp_path)])
        assert proc.returncode == EXIT_OK
        rep = json.loads(proc.stdout)
        assert isinstance(rep, dict)

    def test_json_output_stdout_is_valid_json_on_failure(self, tmp_path: Path) -> None:
        # Empty dir → detection fails; report is still emitted as JSON to stdout.
        proc = _invoke_subprocess(["--json-output", "analyze", str(tmp_path)])
        assert proc.returncode != EXIT_OK
        rep = json.loads(proc.stdout)
        assert isinstance(rep, dict)

    def test_json_output_does_not_leak_banner_to_stdout(self, tmp_path: Path) -> None:
        proc = _invoke_subprocess(["--json-output", "analyze", str(tmp_path)])
        # stdout must be a JSON object; the error banner must be on stderr only.
        stripped = proc.stdout.strip()
        assert stripped.startswith("{"), f"stdout does not start with JSON object: {stripped[:80]!r}"
        assert "[detect]" in proc.stderr

    def test_json_output_serialisable_without_default(self, tmp_path: Path) -> None:
        """Report must be JSON-safe natively; no default= fallback required."""
        make_valid_palace(tmp_path)
        proc = _invoke_subprocess(["--json-output", "analyze", str(tmp_path)])
        rep = json.loads(proc.stdout)
        # Re-serialise without default= — must not raise.
        json.dumps(rep)


# ---------------------------------------------------------------------------
# inspect subcommand
# ---------------------------------------------------------------------------


class TestInspect:
    def test_valid_palace_exits_0(self, tmp_path: Path) -> None:
        make_valid_palace(tmp_path)
        result = CliRunner().invoke(cli, ["inspect", str(tmp_path)])
        assert result.exit_code == EXIT_OK

    def test_valid_palace_report_has_validation_section(self, tmp_path: Path) -> None:
        make_valid_palace(tmp_path)
        result = CliRunner().invoke(cli, ["--json-output", "inspect", str(tmp_path)])
        assert result.exit_code == EXIT_OK
        report = json.loads(result.output)
        # validation section is populated (not None) because extraction ran.
        assert report["validation"] is not None

    def test_empty_dir_exits_2(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(cli, ["inspect", str(tmp_path)])
        assert result.exit_code == EXIT_DETECTION_FAILED

    def test_stubs_surfaced_in_report_not_crashes(self, tmp_path: Path) -> None:
        make_valid_palace(tmp_path)
        result = CliRunner().invoke(cli, ["--json-output", "inspect", str(tmp_path)])
        report = json.loads(result.output)
        # Transform and reconstruct stubs emit NOT_IMPLEMENTED anomalies.
        anomaly_types = {a["type"] for a in report.get("anomalies", [])}
        assert "not_implemented" in anomaly_types

    def test_quiet_suppresses_output(self, tmp_path: Path) -> None:
        make_valid_palace(tmp_path)
        result = CliRunner().invoke(cli, ["--quiet", "inspect", str(tmp_path)])
        assert result.exit_code == EXIT_OK
        assert result.output.strip() == ""


# ---------------------------------------------------------------------------
# report subcommand (re-render JSON → text, no pipeline)
# ---------------------------------------------------------------------------


class TestReportSubcommand:
    def _get_json_report(self, tmp_path: Path) -> str:
        make_valid_palace(tmp_path)
        proc = _invoke_subprocess(["--json-output", "analyze", str(tmp_path)])
        assert proc.returncode == EXIT_OK
        return proc.stdout

    def test_round_trip_text_matches_direct_analyze_text(self, tmp_path: Path) -> None:
        """report sub-cmd text output must match direct render_text() on the same report."""
        make_valid_palace(tmp_path)

        # Capture JSON from a single analyze run.
        json_str = _invoke_subprocess(["--json-output", "analyze", str(tmp_path)]).stdout
        json_path = tmp_path / "report.json"
        json_path.write_text(json_str, encoding="utf-8")

        # Render via `report` subcommand.
        via_report = _invoke_subprocess(["report", str(json_path)])
        assert via_report.returncode == EXIT_OK

        # Render directly using render_text on the same dict.
        from mempalace_migrator.reporting.text_renderer import render_text

        expected = render_text(json.loads(json_str)) + "\n"
        assert via_report.stdout == expected

    def test_invalid_json_file_exits_report_failed(self, tmp_path: Path) -> None:
        bad = tmp_path / "not-json.json"
        bad.write_text("this is not json", encoding="utf-8")
        result = CliRunner().invoke(cli, ["report", str(bad)])
        assert result.exit_code == EXIT_REPORT_FILE_ERROR

    def test_report_with_critical_anomaly_exits_8(self, tmp_path: Path) -> None:
        # Construct a synthetic report with a CRITICAL anomaly.
        synthetic = {
            "run_id": "test-run",
            "outcome": "success",
            "anomaly_summary": {"top_severity": "critical"},
        }
        report_path = tmp_path / "report.json"
        report_path.write_text(json.dumps(synthetic), encoding="utf-8")
        result = CliRunner().invoke(cli, ["report", str(report_path)])
        assert result.exit_code == EXIT_CRITICAL_ANOMALY

    def test_report_on_failure_report_exits_unexpected(self, tmp_path: Path) -> None:
        # A "failure" outcome report fed to `report` should produce non-zero exit.
        synthetic = {
            "run_id": "test-run",
            "outcome": "failure",
            "anomaly_summary": {"top_severity": "critical"},
        }
        report_path = tmp_path / "report.json"
        report_path.write_text(json.dumps(synthetic), encoding="utf-8")
        result = CliRunner().invoke(cli, ["report", str(report_path)])
        assert result.exit_code != EXIT_OK

    def test_quiet_suppresses_text_but_exit_correct(self, tmp_path: Path) -> None:
        json_str = self._get_json_report(tmp_path)
        report_path = tmp_path / "r.json"
        report_path.write_text(json_str, encoding="utf-8")
        proc = _invoke_subprocess(["--quiet", "report", str(report_path)])
        assert proc.returncode == EXIT_OK
        assert proc.stdout.strip() == ""

    def test_minimal_report_does_not_raise(self, tmp_path: Path) -> None:
        """render_text must tolerate partial reports (no KeyError)."""
        minimal = {"run_id": "abc", "outcome": "failure"}
        report_path = tmp_path / "minimal.json"
        report_path.write_text(json.dumps(minimal), encoding="utf-8")
        result = CliRunner().invoke(cli, ["report", str(report_path)])
        # Should not crash; outcome!=success so non-zero exit is fine.
        assert result.exception is None or isinstance(result.exception, SystemExit)


# ---------------------------------------------------------------------------
# PIPELINES registry integrity
# ---------------------------------------------------------------------------


def test_pipelines_registry_contains_analyze_and_inspect() -> None:
    from mempalace_migrator.core.pipeline import ANALYZE_PIPELINE, FULL_PIPELINE, PIPELINES

    assert PIPELINES["analyze"] is ANALYZE_PIPELINE
    assert PIPELINES["inspect"] is FULL_PIPELINE


def test_pipelines_registry_values_are_non_empty_tuples() -> None:
    from mempalace_migrator.core.pipeline import PIPELINES

    for name, pipeline in PIPELINES.items():
        assert isinstance(pipeline, tuple), f"{name!r} pipeline is not a tuple"
        assert len(pipeline) > 0, f"{name!r} pipeline is empty"

    for name, pipeline in PIPELINES.items():
        assert isinstance(pipeline, tuple), f"{name!r} pipeline is not a tuple"
        assert len(pipeline) > 0, f"{name!r} pipeline is empty"
