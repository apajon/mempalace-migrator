"""M18 task 21 — UX CLI & Developer Experience tests.

Covers:
  21.1-a  report subcommand help mentions "any subcommand"
  21.4-a  README contains python -m invocation for migrate
  21.4-b  README §7 contains a ### Quick start heading
  21.5-a  examples/make_sample_palace.py is importable (no syntax error)
  21.5-b  sample palace is readable by analyze (exit not in {2, 3, 10})
  21.5-c  sample palace survives full migrate pipeline (exit 0 or 8)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.adversarial.conftest import EXIT_DETECTION_FAILED, EXIT_EXTRACTION_FAILED, EXIT_OK, EXIT_UNEXPECTED, EXIT_CRITICAL_ANOMALY, run_cli

_REPO_ROOT = Path(__file__).resolve().parents[1]
_README = _REPO_ROOT / "README.md"
_SAMPLE_SCRIPT = _REPO_ROOT / "examples" / "make_sample_palace.py"


# ---------------------------------------------------------------------------
# 21.1-a — report subcommand help text
# ---------------------------------------------------------------------------


def test_report_help_mentions_any_subcommand() -> None:
    """The first help line of `report` must mention 'any subcommand', not name
    specific subcommands.  Guards that the gap identified in M18 §4.1 is fixed.
    """
    from mempalace_migrator.cli.main import cli

    cmd = cli.commands["report"]
    help_text = cmd.help or ""
    first_line = next(
        (line.strip() for line in help_text.splitlines() if line.strip()),
        "",
    )
    assert "any subcommand" in first_line, (
        f"report first help line does not mention 'any subcommand': {first_line!r}"
    )
    assert "analyze or inspect" not in first_line, (
        f"report first help line still says 'analyze or inspect': {first_line!r}"
    )


# ---------------------------------------------------------------------------
# 21.4-a — README quick-start python -m invocation
# ---------------------------------------------------------------------------


def test_readme_contains_python_m_migrate() -> None:
    """README must contain the python -m invocation for the migrate subcommand."""
    readme = _README.read_text(encoding="utf-8")
    assert "python -m mempalace_migrator.cli.main migrate" in readme, (
        "README does not contain 'python -m mempalace_migrator.cli.main migrate'. "
        "Add a Quick start section inside §7 with the python -m form."
    )


# ---------------------------------------------------------------------------
# 21.4-b — README §7 contains ### Quick start
# ---------------------------------------------------------------------------


def test_readme_cli_section_contains_quick_start() -> None:
    """README §7 CLI reference must contain a ### Quick start subsection."""
    import re

    readme = _README.read_text(encoding="utf-8")
    # Extract §7 content (between ## 7. and ## 8.)
    m = re.search(r"##\s+7\.\s+CLI reference.*?(?=##\s+8\.)", readme, re.DOTALL)
    assert m is not None, "README §7 'CLI reference' section not found"
    section = m.group(0)
    assert "### Quick start" in section, (
        "README §7 CLI reference does not contain a '### Quick start' subsection"
    )


# ---------------------------------------------------------------------------
# 21.5-a — examples/make_sample_palace.py exists and has no syntax error
# ---------------------------------------------------------------------------


def test_sample_script_importable() -> None:
    """examples/make_sample_palace.py must exist and compile without errors."""
    assert _SAMPLE_SCRIPT.exists(), f"examples/make_sample_palace.py not found at {_SAMPLE_SCRIPT}"
    source = _SAMPLE_SCRIPT.read_text(encoding="utf-8")
    try:
        compile(source, str(_SAMPLE_SCRIPT), "exec")
    except SyntaxError as exc:
        pytest.fail(f"examples/make_sample_palace.py has a syntax error: {exc}")


# ---------------------------------------------------------------------------
# 21.5-b — sample palace is readable by analyze
# ---------------------------------------------------------------------------


def test_sample_palace_analyze_succeeds(tmp_path: Path) -> None:
    """Running make_sample_palace.py then analyze must not exit with 2, 3, or 10."""
    result = subprocess.run(
        [sys.executable, str(_SAMPLE_SCRIPT), str(tmp_path / "sample")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"make_sample_palace.py failed (rc={result.returncode}):\n{result.stderr}"
    )

    cli_result = run_cli(["analyze", str(tmp_path / "sample")])
    forbidden = {EXIT_DETECTION_FAILED, EXIT_EXTRACTION_FAILED, EXIT_UNEXPECTED}
    assert cli_result.returncode not in forbidden, (
        f"analyze exited {cli_result.returncode} (forbidden: {forbidden})\n"
        f"stderr: {cli_result.stderr}"
    )


# ---------------------------------------------------------------------------
# 21.5-c — sample palace survives full migrate pipeline
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_sample_palace_migrate_succeeds(tmp_path: Path) -> None:
    """Running make_sample_palace.py then migrate must exit 0 or 8 (not 2/3/10)."""
    source = tmp_path / "sample"
    target = tmp_path / "target"

    result = subprocess.run(
        [sys.executable, str(_SAMPLE_SCRIPT), str(source)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"make_sample_palace.py failed (rc={result.returncode}):\n{result.stderr}"
    )

    from tests.adversarial.conftest import run_migrate_cli

    cli_result = run_migrate_cli(source, target, json_output=False)
    allowed = {EXIT_OK, EXIT_CRITICAL_ANOMALY}
    assert cli_result.returncode in allowed, (
        f"migrate exited {cli_result.returncode} (expected one of {allowed})\n"
        f"stderr: {cli_result.stderr}\nstdout: {cli_result.stdout}"
    )
