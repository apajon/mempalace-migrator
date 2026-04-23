"""M15 — CI workflow surface tests (phase 18, task 18.6).

'CI tests CI': these assertions run inside the same ``pytest -q`` that the
workflow executes.  If ``.github/workflows/ci.yml`` drifts from the eight
assertions below, the CI run that introduced the drift fails before the
drift can be merged to main.

Eight assertions per design doc §7.1 (adjusted per operator decision):

  1. ci.yml exists and parses as YAML.
  2. Workflow triggers on ``pull_request`` against ``main``.
     Note: ``push`` trigger is intentionally absent to stay within
     action-minute budget (documented deviation from §5.1 which specifies
     both triggers).
  3. The single job runs on ``ubuntu-latest`` with ``python-version "3.12"``
     (matching ``pyproject.toml::requires-python = ">=3.12"``).
  4. Workflow contains a step whose ``run:`` string includes ``"pytest"``.
  5. Workflow contains a step whose ``run:`` string includes
     ``"mempalace-migrator --help"`` (catches packaging regressions that
     pytest-by-path cannot catch).
  6. Workflow contains a step whose ``run:`` string references
     ``"tests/test_migrate_e2e.py"`` (the migrate smoke fallback path per
     design §5.3).
  7. No step uses ``continue-on-error: true`` (would mask failures as green).
  8. No step name, ``run:`` line, or top-level workflow ``name:`` contains
     FORBIDDEN_VOCABULARY (extends M7/M14 ban to CI output surfaces).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parents[2]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Mirrors tests/adversarial/_invariants.py::_FORBIDDEN_WORDS.
# Kept as a local constant to avoid coupling to a private name; keep in sync.
_FORBIDDEN_WORDS: tuple[str, ...] = ("correct", "verified", "guaranteed", "valid")

_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _FORBIDDEN_WORDS) + r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _load_workflow() -> dict[str, Any]:
    """Load and return the parsed ci.yml as a dict."""
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))  # type: ignore[return-value]


def _iter_steps(workflow: dict[str, Any]):
    """Yield every step dict from every job in the workflow."""
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            yield step


# ---------------------------------------------------------------------------
# Assertion 1 — ci.yml exists and parses as YAML
# ---------------------------------------------------------------------------


def test_workflow_exists_and_parses() -> None:
    """ci.yml must exist at .github/workflows/ci.yml and parse as a YAML mapping."""
    assert _WORKFLOW.exists(), (
        f"CI workflow not found at {_WORKFLOW.relative_to(_REPO_ROOT)}. "
        "Task 18.1 requires this file to be committed."
    )
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "ci.yml top-level must be a YAML mapping, not a list or scalar."


# ---------------------------------------------------------------------------
# Assertion 2 — pull_request trigger on main
# ---------------------------------------------------------------------------


def test_workflow_triggers_on_pull_request() -> None:
    """Workflow must trigger on pull_request against main.

    push trigger is intentionally absent per operator decision (action-minute
    budget). This is a documented deviation from M15_CI_BASELINE_DESIGN.md §5.1.
    """
    wf = _load_workflow()
    # PyYAML parses the YAML key 'on' as Python bool True.
    triggers: dict[str, Any] = wf.get(True) or {}  # type: ignore[call-overload]
    assert "pull_request" in triggers, (
        f"ci.yml does not have a pull_request trigger. " f"Triggers found: {sorted(str(k) for k in triggers)}"
    )
    pr_cfg = triggers.get("pull_request") or {}
    pr_branches: list[str] = pr_cfg.get("branches") or []
    assert "main" in pr_branches, (
        f"pull_request trigger does not target 'main'. " f"Branches configured: {pr_branches}"
    )


# ---------------------------------------------------------------------------
# Assertion 3 — runs-on ubuntu-latest, python-version "3.12"
# ---------------------------------------------------------------------------


def test_job_uses_ubuntu_and_python_312() -> None:
    """The single job must use ubuntu-latest and python-version '3.12'."""
    wf = _load_workflow()
    jobs: dict[str, Any] = wf.get("jobs") or {}
    assert len(jobs) == 1, f"Expected exactly 1 job in ci.yml; found {len(jobs)}: {sorted(jobs)}"
    job = next(iter(jobs.values()))
    assert job.get("runs-on") == "ubuntu-latest", f"Job runs-on={job.get('runs-on')!r}; expected 'ubuntu-latest'."
    # Locate the setup-python step and verify python-version.
    python_version: str | None = None
    for step in job.get("steps") or []:
        if "setup-python" in (step.get("uses") or ""):
            raw = (step.get("with") or {}).get("python-version")
            python_version = str(raw) if raw is not None else None
            break
    assert python_version == "3.12", (
        f"setup-python step python-version={python_version!r}; expected '3.12'.\n"
        "This must match pyproject.toml::requires-python = '>=3.12'."
    )


# ---------------------------------------------------------------------------
# Assertion 4 — pytest step present
# ---------------------------------------------------------------------------


def test_workflow_contains_pytest_step() -> None:
    """At least one step's run: string must contain 'pytest'."""
    wf = _load_workflow()
    for step in _iter_steps(wf):
        if "pytest" in (step.get("run") or ""):
            return
    pytest.fail(
        "No step in ci.yml has a run: string containing 'pytest'. "
        "The full test suite must be invoked by the workflow."
    )


# ---------------------------------------------------------------------------
# Assertion 5 — CLI --help smoke step present
# ---------------------------------------------------------------------------


def test_workflow_contains_cli_help_step() -> None:
    """At least one step's run: string must include 'mempalace-migrator --help'.

    This catches packaging regressions (broken [project.scripts] entry, missing
    __init__.py, typo in the entry point) that pytest-by-path cannot catch.
    """
    wf = _load_workflow()
    for step in _iter_steps(wf):
        if "mempalace-migrator --help" in (step.get("run") or ""):
            return
    pytest.fail(
        "No step in ci.yml has a run: string containing 'mempalace-migrator --help'. "
        "Task 18.2 requires CLI entry-point smoke steps."
    )


# ---------------------------------------------------------------------------
# Assertion 6 — migrate smoke step present
# ---------------------------------------------------------------------------


def test_workflow_contains_migrate_smoke_step() -> None:
    """At least one step's run: string must reference 'tests/test_migrate_e2e.py'.

    Per design §5.3 fallback: when the smoke path reuses the existing e2e
    test module to avoid logic duplication, the workflow must call it
    explicitly so it is a named CI gate.
    """
    wf = _load_workflow()
    for step in _iter_steps(wf):
        if "tests/test_migrate_e2e.py" in (step.get("run") or ""):
            return
    pytest.fail(
        "No step in ci.yml references tests/test_migrate_e2e.py. "
        "Task 18.3 requires an explicit migrate smoke step calling this module."
    )


# ---------------------------------------------------------------------------
# Assertion 7 — no continue-on-error: true
# ---------------------------------------------------------------------------


def test_no_continue_on_error() -> None:
    """No step may use continue-on-error: true (masks failures as green)."""
    wf = _load_workflow()
    violations: list[str] = []
    for step in _iter_steps(wf):
        if step.get("continue-on-error") is True:
            label = step.get("name") or step.get("uses") or (step.get("run") or "")[:60]
            violations.append(label)
    assert not violations, (
        f"Steps with continue-on-error: true in ci.yml: {violations}\n"
        "A failing step must fail the job; masking is a contract violation per §5.5."
    )


# ---------------------------------------------------------------------------
# Assertion 8 — no forbidden vocabulary in step names or run strings
# ---------------------------------------------------------------------------


def test_no_forbidden_vocabulary_in_workflow() -> None:
    """No step name, run: line, or top-level workflow name may contain
    FORBIDDEN_VOCABULARY.

    Extends the M7/M14 vocabulary ban to CI output surfaces (design §4).
    The canonical forbidden word list mirrors _invariants.py::_FORBIDDEN_WORDS.
    """
    wf = _load_workflow()
    violations: list[str] = []

    # Workflow-level name
    wf_name: str = wf.get("name") or ""
    if _FORBIDDEN_RE.search(wf_name):
        violations.append(f"workflow name: {wf_name!r}")

    # Per-step: name and each run: line
    for step in _iter_steps(wf):
        step_name: str = step.get("name") or ""
        if _FORBIDDEN_RE.search(step_name):
            violations.append(f"step name: {step_name!r}")
        run: str = step.get("run") or ""
        for line in run.splitlines():
            if _FORBIDDEN_RE.search(line):
                violations.append(f"step run line: {line!r}")

    assert not violations, (
        "Forbidden correctness vocabulary found in ci.yml:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\nAllowed replacements: 'completed without raising a critical error', "
        "'no CRITICAL anomalies recorded'."
    )
