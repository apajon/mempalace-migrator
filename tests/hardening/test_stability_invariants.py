"""M8 task 11.4 — stability invariants exit gate.

Runs all nine M7 structural invariants over the *baseline corpus* (entries
whose ``allowed_exit_codes ⊆ {0, 8}``) and additionally checks:

  * **Report-signature stability** — the report produced by each baseline
    entry matches the committed ``report_signatures.json`` (modulo the volatile
    fields ``run_id``, ``started_at``, ``completed_at``).
  * **Determinism** — two consecutive CLI invocations for the same entry
    produce identical signatures.

Import note: check functions live in ``tests/adversarial/_invariants.py`` so
the same logic guards both the full adversarial corpus (M7) and this hardened
baseline subset (M8).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.adversarial._invariants import (
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
from tests.adversarial.conftest import CorpusEntry, run_cli
from tests.hardening.conftest import BASELINE_CORPUS, extract_report_signature, load_report_signatures

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ids(entries: tuple[CorpusEntry, ...]) -> list[str]:
    return [e.cid for e in entries]


def _run_baseline_entry(
    entry: CorpusEntry, palace, *, json_output: bool = True
) -> tuple[int, str, str, dict[str, Any] | None]:
    """Run the CLI for *entry* and return (rc, stdout, stderr, report_or_None)."""
    args: list[str] = []
    if json_output:
        args.append("--json-output")
    args.extend([entry.pipeline, str(palace)])
    result = run_cli(args)
    report: dict[str, Any] | None = None
    if result.stdout.strip():
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError:
            pass
    return result.returncode, result.stdout, result.stderr, report


# ---------------------------------------------------------------------------
# Module-scoped run cache — build palace + run CLI once per corpus entry.
# ---------------------------------------------------------------------------

_baseline_runs: dict[str, dict[str, Any]] = {}


@pytest.fixture(scope="module")
def _run_cache():
    return {}


@pytest.fixture
def baseline_run(request, tmp_path, _run_cache):
    """Fixture: materialise the corpus entry and run the CLI (cached per cid)."""
    entry: CorpusEntry = request.param
    if entry.cid in _run_cache:
        return _run_cache[entry.cid]
    palace = entry.builder(tmp_path)
    rc, stdout, stderr, report = _run_baseline_entry(entry, palace)
    record = {
        "entry": entry,
        "palace": palace,
        "returncode": rc,
        "stdout": stdout,
        "stderr": stderr,
        "report": report,
    }
    _run_cache[entry.cid] = record
    return record


# ---------------------------------------------------------------------------
# Nine M7 structural invariants applied to the baseline corpus
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("baseline_run", BASELINE_CORPUS, ids=_ids(BASELINE_CORPUS), indirect=True)
def test_baseline_no_unexpected_exit_code(baseline_run):
    """Inv. 1 — no exit 10 on baseline entries."""
    check_no_unexpected_exit_code(baseline_run["entry"].cid, baseline_run["returncode"], baseline_run["stderr"])


@pytest.mark.parametrize("baseline_run", BASELINE_CORPUS, ids=_ids(BASELINE_CORPUS), indirect=True)
def test_baseline_exit_code_in_allowed_set(baseline_run):
    """Inv. ent. — exit code is in the per-entry allowed set."""
    e = baseline_run["entry"]
    check_exit_code_in_allowed_set(e.cid, baseline_run["returncode"], e.allowed_exit_codes, baseline_run["stderr"])


@pytest.mark.parametrize("baseline_run", BASELINE_CORPUS, ids=_ids(BASELINE_CORPUS), indirect=True)
def test_baseline_no_traceback_on_stderr(baseline_run):
    """Inv. 2 — no Python traceback on stderr."""
    check_no_traceback_on_stderr(baseline_run["entry"].cid, baseline_run["stderr"])


@pytest.mark.parametrize("baseline_run", BASELINE_CORPUS, ids=_ids(BASELINE_CORPUS), indirect=True)
def test_baseline_no_silent_critical(baseline_run):
    """Inv. 3 — no silent CRITICAL."""
    check_no_silent_critical(baseline_run["entry"].cid, baseline_run["report"], baseline_run["returncode"])


@pytest.mark.parametrize("baseline_run", BASELINE_CORPUS, ids=_ids(BASELINE_CORPUS), indirect=True)
def test_baseline_schema_stability(baseline_run):
    """Inv. 4 — schema_version and top-level keys."""
    check_schema_stability(baseline_run["entry"].cid, baseline_run["report"])


@pytest.mark.parametrize("baseline_run", BASELINE_CORPUS, ids=_ids(BASELINE_CORPUS), indirect=True)
def test_baseline_json_safety(baseline_run):
    """Inv. 5 — report round-trips through json.dumps."""
    check_json_safety(baseline_run["entry"].cid, baseline_run["report"])


@pytest.mark.parametrize("baseline_run", BASELINE_CORPUS, ids=_ids(BASELINE_CORPUS), indirect=True)
def test_baseline_anomaly_well_formedness(baseline_run):
    """Inv. 6 — each anomaly has registered type, non-empty stage, ≥1 evidence."""
    check_anomaly_well_formedness(baseline_run["entry"].cid, baseline_run["report"])


@pytest.mark.parametrize("baseline_run", BASELINE_CORPUS, ids=_ids(BASELINE_CORPUS), indirect=True)
def test_baseline_no_forbidden_vocabulary(baseline_run):
    """Inv. 7 — no forbidden correctness vocabulary in report."""
    check_no_forbidden_vocabulary(baseline_run["entry"].cid, baseline_run["report"])


@pytest.mark.parametrize("baseline_run", BASELINE_CORPUS, ids=_ids(BASELINE_CORPUS), indirect=True)
def test_baseline_failure_stage_is_known(baseline_run):
    """Inv. 8 — failure.stage ∈ known stages."""
    check_failure_stage_is_known(baseline_run["entry"].cid, baseline_run["report"])


# ---------------------------------------------------------------------------
# Inv. 9b — --quiet suppresses stdout on baseline entries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", BASELINE_CORPUS, ids=_ids(BASELINE_CORPUS))
def test_baseline_quiet_suppresses_stdout(entry: CorpusEntry, tmp_path):
    """Inv. 9b — under --quiet, stdout is empty."""
    palace = entry.builder(tmp_path)
    result = run_cli(["--quiet", entry.pipeline, str(palace)])
    assert result.stdout == "", f"[{entry.cid}] --quiet leaked output to stdout: {result.stdout!r}"
    assert result.returncode != 10, f"[{entry.cid}] --quiet returned EXIT_UNEXPECTED"
    assert (
        result.returncode in entry.allowed_exit_codes
    ), f"[{entry.cid}] --quiet exit {result.returncode} not in {sorted(entry.allowed_exit_codes)}"


# ---------------------------------------------------------------------------
# Report-signature stability (compare against committed baselines)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("baseline_run", BASELINE_CORPUS, ids=_ids(BASELINE_CORPUS), indirect=True)
def test_report_signature_matches_baseline(baseline_run):
    """The live report signature must match the committed report_signatures.json."""
    committed = load_report_signatures()
    committed_by_cid: dict[str, dict[str, Any]] = {
        rec["cid"]: rec["signature"] for rec in committed.get("entries", [])
    }
    entry = baseline_run["entry"]
    report = baseline_run["report"]
    if report is None:
        pytest.skip(reason="no_report: stdout was empty for this entry")

    if entry.cid not in committed_by_cid:
        pytest.skip(reason=f"baseline_missing: no committed signature for {entry.cid!r}")

    live_sig = extract_report_signature(report, baseline_run["returncode"])
    committed_sig = committed_by_cid[entry.cid]
    assert live_sig == committed_sig, (
        f"[{entry.cid}] report signature drifted from baseline.\n"
        f"committed: {json.dumps(committed_sig, indent=2)}\n"
        f"    live : {json.dumps(live_sig, indent=2)}"
    )


# ---------------------------------------------------------------------------
# Determinism — two consecutive runs produce identical signatures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", BASELINE_CORPUS, ids=_ids(BASELINE_CORPUS))
def test_report_is_deterministic(entry: CorpusEntry, tmp_path):
    """Two consecutive CLI invocations for the same palace produce the same signature."""
    palace = entry.builder(tmp_path)
    rc1, stdout1, stderr1, report1 = _run_baseline_entry(entry, palace)
    rc2, stdout2, stderr2, report2 = _run_baseline_entry(entry, palace)

    assert rc1 == rc2, f"[{entry.cid}] exit codes differ between runs: {rc1} vs {rc2}"
    if report1 is None or report2 is None:
        # Both should be None (or both non-None) when the exit code matches.
        assert (report1 is None) == (report2 is None), f"[{entry.cid}] one run produced a report and the other did not"
        return

    sig1 = extract_report_signature(report1, rc1)
    sig2 = extract_report_signature(report2, rc2)
    assert sig1 == sig2, (
        f"[{entry.cid}] report signatures differ between two consecutive runs.\n"
        f"run1: {json.dumps(sig1, indent=2)}\n"
        f"run2: {json.dumps(sig2, indent=2)}"
    )
