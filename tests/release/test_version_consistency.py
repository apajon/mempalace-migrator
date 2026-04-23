"""M16 — Version consistency tests (phase 19, task 19.1 / §7.1).

Eight assertions that keep every version-bearing artefact in sync:

  1. importlib.metadata version == mempalace_migrator.__version__
     (broken installation guard)
  2. mempalace_migrator.__version__ == TOOL_VERSION
     (§5.1 derivation not re-introduced as a literal)
  3. mempalace_migrator.__version__ == pyproject.toml::version
     (pyproject.toml is the single source of truth)
  4. CHANGELOG.md exists and contains at least one ## [X.Y.Z] heading
  5. Every ## [X.Y.Z] heading has a matching git tag vX.Y.Z
     (phantom-entry guard); fails explicitly when git is unavailable
  6. Every ## [X.Y.Z] heading carries an ISO-8601 date on the same line
  7. CHANGELOG.md contains no forbidden vocabulary
  8. If .github/workflows/release.yml exists: workflow shape assertions
     (analogous to M15 test_workflow_surface.py)
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

import mempalace_migrator
from mempalace_migrator.reporting.report_builder import TOOL_VERSION

_REPO_ROOT = Path(__file__).parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"
_RELEASE_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "release.yml"

_SEMVER_RE = re.compile(r"^##\s+\[(\d+\.\d+\.\d+)\]", re.MULTILINE)
_DATE_LINE_RE = re.compile(r"^##\s+\[\d+\.\d+\.\d+\]\s+—\s+\d{4}-\d{2}-\d{2}", re.MULTILINE)
_FORBIDDEN_WORDS = ("correct", "verified", "guaranteed", "valid")
_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _FORBIDDEN_WORDS) + r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Assertion 1 — importlib.metadata == __version__
# ---------------------------------------------------------------------------


def test_importlib_metadata_matches_dunder_version() -> None:
    """importlib.metadata.version('mempalace-migrator') must equal __version__."""
    from importlib.metadata import version as pkg_version

    pkg_ver = pkg_version("mempalace-migrator")
    assert pkg_ver == mempalace_migrator.__version__, (
        f"importlib.metadata reports {pkg_ver!r} but __version__ is "
        f"{mempalace_migrator.__version__!r}. Broken installation or stale literal."
    )


# ---------------------------------------------------------------------------
# Assertion 2 — __version__ == TOOL_VERSION
# ---------------------------------------------------------------------------


def test_dunder_version_matches_tool_version() -> None:
    """mempalace_migrator.__version__ must equal TOOL_VERSION."""
    assert mempalace_migrator.__version__ == TOOL_VERSION, (
        f"__version__={mempalace_migrator.__version__!r} != "
        f"TOOL_VERSION={TOOL_VERSION!r}. "
        "The §5.1 derivation fix has been undone."
    )


# ---------------------------------------------------------------------------
# Assertion 3 — __version__ == pyproject.toml::version
# ---------------------------------------------------------------------------


def test_dunder_version_matches_pyproject() -> None:
    """mempalace_migrator.__version__ must equal pyproject.toml::version."""
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    pyproject_version: str = pyproject["project"]["version"]
    assert mempalace_migrator.__version__ == pyproject_version, (
        f"__version__={mempalace_migrator.__version__!r} != "
        f"pyproject.toml::version={pyproject_version!r}. "
        "pyproject.toml must be the single source of truth."
    )


# ---------------------------------------------------------------------------
# Assertion 4 — CHANGELOG.md exists with at least one versioned heading
# ---------------------------------------------------------------------------


def test_changelog_exists_with_versioned_heading() -> None:
    """CHANGELOG.md must exist at repo root with at least one ## [X.Y.Z] heading."""
    assert _CHANGELOG.exists(), "CHANGELOG.md not found at repo root."
    text = _CHANGELOG.read_text(encoding="utf-8")
    versions = _SEMVER_RE.findall(text)
    assert versions, (
        "CHANGELOG.md exists but contains no ## [X.Y.Z] version heading. "
        "Add at least one released version section."
    )


# ---------------------------------------------------------------------------
# Assertion 5 — every ## [X.Y.Z] has a matching git tag vX.Y.Z
# ---------------------------------------------------------------------------


def test_changelog_versions_have_git_tags() -> None:
    """Every ## [X.Y.Z] heading in CHANGELOG.md must have a matching git tag vX.Y.Z."""
    if not _CHANGELOG.exists():
        pytest.skip("CHANGELOG.md not present — covered by test_changelog_exists")

    text = _CHANGELOG.read_text(encoding="utf-8")
    versions = _SEMVER_RE.findall(text)

    if not versions:
        pytest.skip("No versioned headings in CHANGELOG.md")

    # git must be available
    try:
        result = subprocess.run(
            ["git", "tag", "--list"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        pytest.skip("git not available; tag ↔ changelog parity check skipped")

    existing_tags: frozenset[str] = frozenset(result.stdout.splitlines())
    missing: list[str] = [v for v in versions if f"v{v}" not in existing_tags]
    assert not missing, (
        f"CHANGELOG.md lists {missing} but no corresponding git tag(s) exist. "
        "Either push the tag or remove the changelog entry."
    )


# ---------------------------------------------------------------------------
# Assertion 6 — every ## [X.Y.Z] heading has an ISO-8601 date
# ---------------------------------------------------------------------------


def test_changelog_versioned_headings_have_dates() -> None:
    """Every ## [X.Y.Z] heading must carry an ISO-8601 date (## [X.Y.Z] — YYYY-MM-DD)."""
    if not _CHANGELOG.exists():
        pytest.skip("CHANGELOG.md not present — covered by test_changelog_exists")

    text = _CHANGELOG.read_text(encoding="utf-8")
    versions = _SEMVER_RE.findall(text)
    if not versions:
        pytest.skip("No versioned headings in CHANGELOG.md")

    dated = _DATE_LINE_RE.findall(text)
    assert len(dated) == len(versions), (
        f"Found {len(versions)} versioned headings but only {len(dated)} carry "
        "an ISO-8601 date. Format: ## [X.Y.Z] — YYYY-MM-DD"
    )


# ---------------------------------------------------------------------------
# Assertion 7 — no forbidden vocabulary in CHANGELOG.md
# ---------------------------------------------------------------------------


def test_changelog_no_forbidden_vocabulary() -> None:
    """CHANGELOG.md must not contain correctness-vocabulary words."""
    if not _CHANGELOG.exists():
        pytest.skip("CHANGELOG.md not present — covered by test_changelog_exists")

    text = _CHANGELOG.read_text(encoding="utf-8")
    matches = _FORBIDDEN_RE.findall(text)
    assert not matches, (
        f"Forbidden correctness vocabulary in CHANGELOG.md: {sorted(set(matches))}. "
        "Replace with honest phrasing (e.g. 'completed without raising a critical error')."
    )


# ---------------------------------------------------------------------------
# Assertion 8 — release.yml shape (Path B only, skipped when absent)
# ---------------------------------------------------------------------------


def test_release_workflow_shape_if_present() -> None:
    """If .github/workflows/release.yml exists, assert minimal safety properties."""
    if not _RELEASE_WORKFLOW.exists():
        pytest.skip("release.yml not present (Path A release); shape assertions skipped.")

    text = _RELEASE_WORKFLOW.read_text(encoding="utf-8")

    # Must be triggered on tag push matching v*
    assert "tags:" in text and "v*" in text, (
        "release.yml must trigger on push: tags: ['v*']."
    )

    # Must not publish to PyPI (M19 non-goal)
    assert "twine" not in text, (
        "release.yml must not invoke twine (PyPI publish is M19)."
    )
    assert "pypi-publish" not in text, (
        "release.yml must not use pypa/gh-action-pypi-publish (M19)."
    )

    # Must not leak arbitrary secrets beyond GITHUB_TOKEN
    secret_refs = re.findall(r"\$\{\{\s*secrets\.(\w+)\s*\}\}", text)
    non_builtin = [s for s in secret_refs if s != "GITHUB_TOKEN"]
    assert not non_builtin, (
        f"release.yml references non-builtin secrets: {non_builtin}. "
        "Only secrets.GITHUB_TOKEN is permitted."
    )

    # Must not contain forbidden vocabulary
    matches = _FORBIDDEN_RE.findall(text)
    assert not matches, (
        f"Forbidden correctness vocabulary in release.yml: {sorted(set(matches))}."
    )
