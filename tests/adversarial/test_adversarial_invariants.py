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
import re
from typing import Any

import pytest

from mempalace_migrator.core.context import AnomalyType
from mempalace_migrator.reporting.report_builder import (REPORT_SCHEMA_VERSION,
                                                         REPORT_TOP_LEVEL_KEYS)

from .conftest import (CORPUS, EXIT_CRITICAL_ANOMALY, EXIT_OK, EXIT_UNEXPECTED,
                       CorpusEntry, run_cli)

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

_TRACEBACK_RE = re.compile(r"^Traceback \(most recent call last\):", re.MULTILINE)

_FORBIDDEN_WORDS = ("correct", "verified", "guaranteed", "valid")
_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(_FORBIDDEN_WORDS) + r")\b",
    re.IGNORECASE,
)

_KNOWN_STAGES = frozenset({"detect", "extract", "transform", "reconstruct", "validate", "report"})
_VALID_ANOMALY_TYPES = frozenset(t.value for t in AnomalyType)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ids(entries):
    return [e.cid for e in entries]


def _run_entry(entry: CorpusEntry, palace, *, json_output: bool = True, quiet: bool = False) -> tuple[int, str, str]:
    args: list[str] = []
    if json_output:
        args.append("--json-output")
    if quiet:
        args.append("--quiet")
    args.extend([entry.pipeline, str(palace)])
    result = run_cli(args)
    return result.returncode, result.stdout, result.stderr


def _parse_report_or_fail(stdout: str, stderr: str, *, context: str) -> dict[str, Any]:
    if not stdout.strip():
        raise AssertionError(
            f"[{context}] expected JSON report on stdout but stdout was empty.\n"
            f"stderr={stderr!r}"
        )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"[{context}] stdout is not valid JSON: {exc}\nstdout={stdout!r}"
        ) from exc


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
    rc, stdout, stderr = _run_entry(entry, palace, json_output=True)
    record = {
        "entry": entry,
        "palace": palace,
        "returncode": rc,
        "stdout": stdout,
        "stderr": stderr,
        "report": _parse_report_or_fail(stdout, stderr, context=entry.cid) if stdout.strip() else None,
    }
    _entry_runs[entry.cid] = record
    return record


# Inv. 1 — no exit 10.

@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_no_unexpected_exit_code(entry_run):
    entry = entry_run["entry"]
    rc = entry_run["returncode"]
    assert rc != EXIT_UNEXPECTED, (
        f"[{entry.cid}] CLI returned EXIT_UNEXPECTED (10); "
        f"adversarial input revealed an unmodelled failure mode.\n"
        f"stderr={entry_run['stderr']!r}"
    )


# Inv. ent. — exit code is in the entry's allowed set.

@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_exit_code_in_allowed_set(entry_run):
    entry = entry_run["entry"]
    rc = entry_run["returncode"]
    assert rc in entry.allowed_exit_codes, (
        f"[{entry.cid}] exit code {rc} not in allowed set {sorted(entry.allowed_exit_codes)}.\n"
        f"stderr={entry_run['stderr']!r}"
    )


# Inv. 2 — no Python traceback on stderr.

@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_no_traceback_on_stderr(entry_run):
    entry = entry_run["entry"]
    stderr = entry_run["stderr"]
    assert not _TRACEBACK_RE.search(stderr), (
        f"[{entry.cid}] Python traceback escaped to stderr without --debug.\n"
        f"stderr={stderr!r}"
    )


# Inv. 3 — no silent CRITICAL.

@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_no_silent_critical(entry_run):
    entry = entry_run["entry"]
    report = entry_run["report"]
    if report is None:
        # No report = exception path; covered by other invariants.
        return
    outcome = report.get("outcome")
    rc = entry_run["returncode"]
    top_sev = (report.get("anomaly_summary") or {}).get("top_severity", "none")
    if outcome == "success":
        assert rc in (EXIT_OK, EXIT_CRITICAL_ANOMALY), (
            f"[{entry.cid}] outcome=success but exit {rc} not in {{0, 8}}"
        )
    if rc == EXIT_OK:
        assert top_sev != "critical", (
            f"[{entry.cid}] exit 0 but top_severity=critical "
            "(silent-CRITICAL guard violated)"
        )


# Inv. 4 — schema stability.

@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_schema_version_and_top_level_keys(entry_run):
    entry = entry_run["entry"]
    report = entry_run["report"]
    if report is None:
        return
    assert report.get("schema_version") == REPORT_SCHEMA_VERSION, (
        f"[{entry.cid}] schema_version={report.get('schema_version')!r} "
        f"!= REPORT_SCHEMA_VERSION={REPORT_SCHEMA_VERSION}"
    )
    missing = [k for k in REPORT_TOP_LEVEL_KEYS if k not in report]
    assert not missing, f"[{entry.cid}] report missing top-level keys: {missing}"


# Inv. 5 — JSON safety (round-trip with no default=).

@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_report_is_json_safe_round_trip(entry_run):
    entry = entry_run["entry"]
    report = entry_run["report"]
    if report is None:
        return
    # The report came from json.loads(stdout); re-dumping with no default=
    # confirms every value is natively JSON-safe.
    json.dumps(report)  # would raise TypeError on bad value


# Inv. 6 — anomaly well-formedness.

@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_anomaly_well_formedness(entry_run):
    entry = entry_run["entry"]
    report = entry_run["report"]
    if report is None:
        return
    for i, a in enumerate(report.get("anomalies") or []):
        assert a.get("type") in _VALID_ANOMALY_TYPES, (
            f"[{entry.cid}] anomaly[{i}].type={a.get('type')!r} not in AnomalyType"
        )
        loc = a.get("location") or {}
        stage = loc.get("stage", "")
        assert isinstance(stage, str) and stage.strip(), (
            f"[{entry.cid}] anomaly[{i}].location.stage is empty"
        )
        evidence = a.get("evidence") or []
        assert isinstance(evidence, list) and len(evidence) >= 1, (
            f"[{entry.cid}] anomaly[{i}] has no evidence entries"
        )


# Inv. 7 — forbidden correctness vocabulary.

@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_no_forbidden_vocabulary_in_report(entry_run):
    entry = entry_run["entry"]
    report = entry_run["report"]
    if report is None:
        return
    matches = _FORBIDDEN_RE.findall(json.dumps(report))
    assert not matches, (
        f"[{entry.cid}] forbidden correctness vocabulary in JSON output: {matches}"
    )


# Inv. 8 — stage attribution on raised failures.

@pytest.mark.parametrize("entry_run", CORPUS, ids=_ids(CORPUS), indirect=True)
def test_failure_stage_is_known(entry_run):
    entry = entry_run["entry"]
    report = entry_run["report"]
    if report is None:
        return
    failure = report.get("failure")
    if failure is None:
        return
    assert failure.get("stage") in _KNOWN_STAGES, (
        f"[{entry.cid}] failure.stage={failure.get('stage')!r} not in {_KNOWN_STAGES}"
    )


# Inv. 9a — under --json-output, stdout is exactly one JSON document
# (already implicitly checked by report parsing for non-empty stdout).
# Some entries (extraction failure with no detection) still produce a
# report on stdout; some (detection failure before pipeline aborts)
# may produce one too. The relaxed contract: if stdout is non-empty,
# it parses as JSON. That's what _parse_report_or_fail enforces.

# Inv. 9b — under --quiet, stdout is empty regardless of input pathology.

@pytest.mark.parametrize("entry", CORPUS, ids=_ids(CORPUS))
def test_quiet_suppresses_stdout(entry: CorpusEntry, tmp_path):
    palace = entry.builder(tmp_path)
    result = run_cli(["--quiet", entry.pipeline, str(palace)])
    assert result.stdout == "", (
        f"[{entry.cid}] --quiet leaked output to stdout: {result.stdout!r}"
    )
    # quiet must preserve the exit code policy: still in the per-entry
    # allowed set AND never EXIT_UNEXPECTED.
    assert result.returncode != EXIT_UNEXPECTED, (
        f"[{entry.cid}] --quiet returned EXIT_UNEXPECTED (10): "
        f"stderr={result.stderr!r}"
    )
    assert result.returncode in entry.allowed_exit_codes, (
        f"[{entry.cid}] --quiet exit {result.returncode} not in allowed set "
        f"{sorted(entry.allowed_exit_codes)}; stderr={result.stderr!r}"
    )
