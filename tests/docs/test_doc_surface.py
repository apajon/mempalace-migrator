"""M14 — Doc surface parity tests (phase 17, task 17.6).

Five assertions that keep README.md and ARCHITECTURE.md aligned with the
implementation after M14:

  1. exit-code-table parity: every EXIT_* constant in cli/main.py appears in
     the README §8 exit-code table, and every table row matches a constant.
  2. report-key parity: every key in reporting.report_builder.REPORT_TOP_LEVEL_KEYS
     is mentioned (backtick-quoted) in README.md.
  3. forbidden-vocabulary scan: README.md and ARCHITECTURE.md contain none of
     the words in FORBIDDEN_VOCABULARY (mirrors M7's report-level ban).
  4. version-pin consistency: the concrete chromadb version in pyproject.toml
     appears in README §4 "Supported scope".
  5. CLI help consistency: each subcommand's click help first line is present
     verbatim in README §7 "CLI reference".

If any assertion cannot be evaluated without touching production code, it is
dropped; see tests/docs/AUDIT.md §"Dropped assertions". No assertions were
dropped in this implementation.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from mempalace_migrator.cli.main import (
    EXIT_CRITICAL_ANOMALY,
    EXIT_DETECTION_FAILED,
    EXIT_EXTRACTION_FAILED,
    EXIT_OK,
    EXIT_RECONSTRUCT_FAILED,
    EXIT_REPORT_FAILED,
    EXIT_REPORT_FILE_ERROR,
    EXIT_TRANSFORM_FAILED,
    EXIT_UNEXPECTED,
    EXIT_USAGE_ERROR,
    EXIT_VALIDATE_FAILED,
    cli,
)
from mempalace_migrator.reporting.report_builder import REPORT_TOP_LEVEL_KEYS

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parents[2]
_README = _REPO_ROOT / "README.md"
_ARCHITECTURE = _REPO_ROOT / "ARCHITECTURE.md"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

# ---------------------------------------------------------------------------
# Assertion 1 — exit-code table parity
# ---------------------------------------------------------------------------

_ALL_EXIT_CODES: frozenset[int] = frozenset(
    {
        EXIT_OK,
        EXIT_USAGE_ERROR,
        EXIT_DETECTION_FAILED,
        EXIT_EXTRACTION_FAILED,
        EXIT_TRANSFORM_FAILED,
        EXIT_RECONSTRUCT_FAILED,
        EXIT_REPORT_FAILED,
        EXIT_VALIDATE_FAILED,
        EXIT_CRITICAL_ANOMALY,
        EXIT_REPORT_FILE_ERROR,
        EXIT_UNEXPECTED,
    }
)


def _parse_readme_exit_codes(readme_text: str) -> frozenset[int]:
    """Extract integer exit-code values from the '### Exit codes' table in README §8."""
    codes: set[int] = set()
    in_exit_codes_section = False
    for line in readme_text.splitlines():
        # Enter the exit codes subsection
        if re.match(r"^\s*###\s+Exit\s+codes\s*$", line, re.IGNORECASE):
            in_exit_codes_section = True
            continue
        if not in_exit_codes_section:
            continue
        # Exit when we hit another heading (any level)
        if re.match(r"^\s*#{1,6}\s+", line) and "exit" not in line.lower():
            break
        # Match table rows like: | `0`  | description |
        m = re.match(r"^\|\s*`(\d+)`\s*\|", line)
        if m:
            codes.add(int(m.group(1)))
    return frozenset(codes)


def test_exit_code_table_parity() -> None:
    """Every EXIT_* constant in cli/main.py appears in the README §8 exit-code
    table, and every row in that table corresponds to a real EXIT_* constant."""
    readme = _README.read_text(encoding="utf-8")
    readme_codes = _parse_readme_exit_codes(readme)

    missing_from_readme = _ALL_EXIT_CODES - readme_codes
    extra_in_readme = readme_codes - _ALL_EXIT_CODES

    assert not missing_from_readme, (
        f"EXIT_* constants missing from README exit-code table: " f"{sorted(missing_from_readme)}"
    )
    assert not extra_in_readme, (
        f"README exit-code table rows with no matching EXIT_* constant: " f"{sorted(extra_in_readme)}"
    )


# ---------------------------------------------------------------------------
# Assertion 2 — report-key parity
# ---------------------------------------------------------------------------


def test_report_key_parity() -> None:
    """Every key in REPORT_TOP_LEVEL_KEYS is mentioned (as a backtick-quoted
    word) somewhere in README.md."""
    readme = _README.read_text(encoding="utf-8")
    missing = [key for key in REPORT_TOP_LEVEL_KEYS if not re.search(r"`" + re.escape(key) + r"`", readme)]
    assert not missing, f"REPORT_TOP_LEVEL_KEYS entries not found (backtick-quoted) in README: " f"{missing}"


# ---------------------------------------------------------------------------
# Assertion 3 — forbidden-vocabulary scan
# ---------------------------------------------------------------------------

# Mirrors tests/adversarial/_invariants.py::_FORBIDDEN_WORDS.
# Named FORBIDDEN_VOCABULARY here for clarity.  Keep these lists in sync.
FORBIDDEN_VOCABULARY: tuple[str, ...] = ("correct", "verified", "guaranteed", "valid")

_FORBIDDEN_DOC_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in FORBIDDEN_VOCABULARY) + r")\b",
    re.IGNORECASE,
)


def test_no_forbidden_vocabulary_readme() -> None:
    """README.md contains none of the forbidden correctness-vocabulary words
    (same ban M7 places on report payloads, extended to docs by M14)."""
    text = _README.read_text(encoding="utf-8")
    matches = _FORBIDDEN_DOC_RE.findall(text)
    assert not matches, (
        f"Forbidden correctness vocabulary in README.md: {sorted(set(matches))}\n"
        "Allowed replacements: 'completed without raising a critical error', "
        "'no CRITICAL anomalies recorded', 'structurally consistent with the "
        "transformed bundle', 'reopenable by the pinned chromadb client'."
    )


def test_no_forbidden_vocabulary_architecture() -> None:
    """ARCHITECTURE.md contains none of the forbidden correctness-vocabulary words."""
    text = _ARCHITECTURE.read_text(encoding="utf-8")
    matches = _FORBIDDEN_DOC_RE.findall(text)
    assert not matches, (
        f"Forbidden correctness vocabulary in ARCHITECTURE.md: {sorted(set(matches))}\n"
        "Allowed replacements: 'completed without raising a critical error', "
        "'no CRITICAL anomalies recorded', 'structurally consistent'."
    )


# ---------------------------------------------------------------------------
# Assertion 4 — version-pin consistency
# ---------------------------------------------------------------------------


def test_version_pin_consistency() -> None:
    """The concrete chromadb version number in pyproject.toml also appears in
    README §4 'Supported scope'.  A stricter derivation check (matching the
    detected chromadb_version set) is an M15 task."""
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    deps: list[str] = pyproject.get("project", {}).get("dependencies", [])
    chromadb_spec = next((d for d in deps if d.startswith("chromadb")), None)
    assert chromadb_spec is not None, "chromadb not found in pyproject.toml dependencies"

    concrete = re.search(r"(\d+\.\d+\.\d+)", chromadb_spec)
    assert concrete is not None, f"No concrete version number (x.y.z) found in chromadb dep: {chromadb_spec!r}"
    pin_version = concrete.group(1)  # e.g. "1.5.7"

    readme = _README.read_text(encoding="utf-8")
    # Locate §4 "Supported scope"
    section_match = re.search(r"##\s+4\.\s+Supported scope.*?(?=##\s+5\.)", readme, re.DOTALL)
    assert section_match is not None, "README §4 'Supported scope' section not found"
    section_text = section_match.group(0)

    assert pin_version in section_text, (
        f"chromadb version {pin_version!r} from pyproject.toml not found in "
        f"README §4.\nSection excerpt:\n{section_text[:400]}"
    )


# ---------------------------------------------------------------------------
# Assertion 5 — CLI help ↔ README §7 CLI reference consistency
# ---------------------------------------------------------------------------


def _get_readme_cli_section(readme: str) -> str:
    """Return the text of README §7 'CLI reference'."""
    m = re.search(r"##\s+7\.\s+CLI reference.*?(?=##\s+8\.)", readme, re.DOTALL)
    assert m is not None, "README §7 'CLI reference' section not found"
    return m.group(0)


def test_cli_help_readme_consistency() -> None:
    """Each subcommand's click help first line (the short description) appears
    verbatim in the README §7 CLI reference section for that command.

    Implementation note: we check the full first paragraph of the click
    help (first non-empty line of the docstring) against the CLI reference
    section text.  String-prefix match is not used; substring containment is
    sufficient and avoids fragility from minor tense variation.
    """
    from click.testing import CliRunner

    runner = CliRunner()
    readme = _README.read_text(encoding="utf-8")
    cli_ref = _get_readme_cli_section(readme)

    subcommands = ["analyze", "inspect", "migrate", "report"]
    missing: list[tuple[str, str]] = []

    for name in subcommands:
        cmd = cli.commands[name]
        # First non-empty line of the full help text is the short summary
        help_text = cmd.help or ""
        first_line = next(
            (line.strip() for line in help_text.splitlines() if line.strip()),
            "",
        )
        if not first_line:
            missing.append((name, "<empty help>"))
        elif first_line not in cli_ref:
            missing.append((name, first_line))

    assert not missing, "CLI subcommand first help line not found in README §7 CLI reference:\n" + "\n".join(
        f"  {name!r}: {line!r}" for name, line in missing
    )


# ---------------------------------------------------------------------------
# Assertion 6 — TODO.json current_focus is not stale (M14 guard)
# ---------------------------------------------------------------------------


def test_current_focus_not_stale() -> None:
    """TODO.json::current_focus.next_milestone must not name a milestone whose
    phase is already fully done.

    This prevents a recurrence of the M14 bookkeeping inconsistency where
    current_focus still claimed 'implementation pending' while phase 17 was
    entirely done.  The check is intentionally narrow: it only fails when the
    next_milestone field literally matches the milestone of an already-done
    phase.
    """
    import json

    todo = json.loads((_REPO_ROOT / "TODO.json").read_text(encoding="utf-8"))
    current_focus = todo.get("current_focus") or {}
    next_milestone = current_focus.get("next_milestone", "")

    phases = todo.get("phases") or []
    done_milestones = {p["milestone"] for p in phases if p.get("status") == "done" and p.get("milestone")}

    assert next_milestone not in done_milestones, (
        f"TODO.json current_focus.next_milestone={next_milestone!r} names a milestone "
        f"whose phase is already 'done'. Update current_focus to point at the next "
        f"pending milestone. Done milestones: {sorted(done_milestones)}"
    )
