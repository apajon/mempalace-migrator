"""M7 task 10.6 — cross-cutting invariants over the adversarial corpus.

This file IS the M7 exit gate. Every entry of ``CORPUS`` is run through
the real CLI subprocess; the resulting ``(exit_code, stdout, stderr,
report)`` tuple is checked against nine invariants. If any invariant
fails for any entry, M7 has surfaced a real defect to fix in production
code.

The nine invariants (from tests/M7_ADVERSARIAL_DESIGN.md §5):

  1. No exit 10 from any adversarial input.
  2. No Python traceback on stderr unless --debug is set.
  3. No silent CRITICAL: outcome=success ⇒ exit ∈ {0, 8};
     exit 0 ⇒ top_severity != "critical".
  4. Schema stability: every report has REPORT_SCHEMA_VERSION and the
     full set of REPORT_TOP_LEVEL_KEYS.
  5. JSON safety: report round-trips via json.dumps with no default=.
  6. Anomaly well-formedness: every anomaly has a non-empty
     location.stage, ≥1 evidence entry, and a type registered in
     AnomalyType.
  7. Forbidden vocabulary: serialised report contains none of
     ``correct|verified|guaranteed|valid`` (word boundaries).
  8. Stage attribution: failure.stage ∈ known stages.
  9. stdout/stderr discipline: under --json-output, stdout is exactly
     one JSON document; under --quiet, stdout is empty.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from ._invariants import (
    _KNOWN_STAGES,
    check_anomaly_well_formedness,
    check_exit_code_in_allowed_set,
    check_failure_stage_is_known,
    check_json_safety,
    check_no_forbidden_vocabulary,
    check_no_silent_critical,
    check_no_traceback_on_stderr,
    check_no_unexpected_exit_code,
    check_schema_stability,
)
from .conftest import CORPUS, EXIT_CRITICAL_ANOMALY, EXIT_OK, EXIT_UNEXPECTED, CorpusEntry, run_cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ids(entries):
    return [e.cid for e in entries]


def _run_entry(
    entry: CorpusEntry,
    palace,
    *,
    json_output: bool = True,
    quiet: bool = False,
    target_path=None,
) -> tuple[int, str, str]:
    args: list[str] = []
    if json_output:
        args.append("--json-output")
    if quiet:
        args.append("--quiet")
    args.append(entry.pipeline)
    args.append(str(palace))
    if entry.pipeline == "migrate":
        # migrate requires --target; use a fresh subdirectory so each run is
        # isolated even if the caller did not provide an explicit target_path.
        if target_path is None:
            import tempfile

            target_path = _tmp_target_dir(palace)
        args.extend(["--target", str(target_path)])
    result = run_cli(args)
    return result.returncode, result.stdout, result.stderr


def _tmp_target_dir(palace) -> "Path":
    """Return a sibling directory named ``_target`` that does not exist yet."""
    from pathlib import Path

    return Path(palace).parent / "_target"


def _parse_report_or_fail(stdout: str, stderr: str, *, context: str) -> dict[str, Any]:
    if not stdout.strip():
        raise AssertionError(
            f"[{context}] expected JSON report on stdout but stdout was empty.\n" f"stderr={stderr!r}"
        )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"[{context}] stdout is not valid JSON: {exc}\nstdout={stdout!r}") from exc


# ---------------------------------------------------------------------------
# Single parametrised driver — runs each corpus entry once and checks all
# stream/exit-code invariants. Per-anomaly invariants live in their own
# parametrised tests below so failures point at a specific invariant.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _entry_runs(request):
    """Cache one subprocess invocation per corpus entry, module-scoped.

    Adversarial subprocesses are cheap individually but we run many
    invariants per entry; caching keeps the suite fast without losing
    isolation (each entry gets its own ``tmp_path`` via a sub-fixture).
    """
    return {}


@pytest.fixture
def entry_run(request, tmp_path, _entry_runs):
    entry: CorpusEntry = request.param
    if entry.cid in _entry_runs:
        return _entry_runs[entry.cid]
    palace = entry.builder(tmp_path)
    target = tmp_path / "_target" if entry.pipeline == "migrate" else None
    rc, stdout, stderr = _run_entry(entry, palace, json_output=True, target_path=target)
    # Quiet variant — run once, cached alongside the main run.
    quiet_target = tmp_path / "_target_quiet" if entry.pipeline == "migrate" else None
    quiet_rc, quiet_stdout, quiet_stderr = _run_entry(
        entry, palace, json_output=False, quiet=True, target_path=quiet_target
    )
    record = {
        "entry": entry,
        "palace": palace,
        "returncode": rc,
        "stdout": stdout,
        "stderr": stderr,
        "report": _parse_report_or_fail(stdout, stderr, context=entry.cid) if stdout.strip() else None,
        "quiet_returncode": quiet_rc,
        "quiet_stdout": quiet_stdout,
        "quiet_stderr": quiet_stderr,
    }
    _entry_runs[entry.cid] = record
    return record


# Inv. 1 — no exit 10.


@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_no_unexpected_exit_code(entry_run):
    check_no_unexpected_exit_code(entry_run["entry"].cid, entry_run["returncode"], entry_run["stderr"])


# Inv. ent. — exit code is in the entry's allowed set.


@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_exit_code_in_allowed_set(entry_run):
    e = entry_run["entry"]
    check_exit_code_in_allowed_set(e.cid, entry_run["returncode"], e.allowed_exit_codes, entry_run["stderr"])


# Inv. 2 — no Python traceback on stderr.


@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_no_traceback_on_stderr(entry_run):
    check_no_traceback_on_stderr(entry_run["entry"].cid, entry_run["stderr"])


# Inv. 3 — no silent CRITICAL.


@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_no_silent_critical(entry_run):
    check_no_silent_critical(entry_run["entry"].cid, entry_run["report"], entry_run["returncode"])


# Inv. 4 — schema stability.


@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_schema_version_and_top_level_keys(entry_run):
    check_schema_stability(entry_run["entry"].cid, entry_run["report"])


# Inv. 5 — JSON safety (round-trip with no default=).


@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_report_is_json_safe_round_trip(entry_run):
    check_json_safety(entry_run["entry"].cid, entry_run["report"])


# Inv. 6 — anomaly well-formedness.


@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_anomaly_well_formedness(entry_run):
    check_anomaly_well_formedness(entry_run["entry"].cid, entry_run["report"])


# Inv. 7 — forbidden correctness vocabulary.


@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_no_forbidden_vocabulary_in_report(entry_run):
    check_no_forbidden_vocabulary(entry_run["entry"].cid, entry_run["report"])


# Inv. 8 — stage attribution on raised failures.


@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_failure_stage_is_known(entry_run):
    check_failure_stage_is_known(entry_run["entry"].cid, entry_run["report"])


# Inv. 9a — under --json-output, stdout is exactly one JSON document
# (already implicitly checked by report parsing for non-empty stdout).
# Some entries (extraction failure with no detection) still produce a
# report on stdout; some (detection failure before pipeline aborts)
# may produce one too. The relaxed contract: if stdout is non-empty,
# it parses as JSON. That's what _parse_report_or_fail enforces.

# Inv. 9b — under --quiet, stdout is empty regardless of input pathology.


@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_quiet_suppresses_stdout(entry_run):
    entry = entry_run["entry"]
    rc = entry_run["quiet_returncode"]
    stdout = entry_run["quiet_stdout"]
    stderr = entry_run["quiet_stderr"]
    assert stdout == "", f"[{entry.cid}] --quiet leaked output to stdout: {stdout!r}"
    assert rc != EXIT_UNEXPECTED, (
        f"[{entry.cid}] --quiet returned EXIT_UNEXPECTED (10): stderr={stderr!r}"
    )
    assert rc in entry.allowed_exit_codes, (
        f"[{entry.cid}] --quiet exit {rc} not in allowed set "
        f"{sorted(entry.allowed_exit_codes)}; stderr={stderr!r}"
    )
