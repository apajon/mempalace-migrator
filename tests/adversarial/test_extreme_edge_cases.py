"""M7 task 10.5 — extreme edge cases.

Per design §4: every edge case must produce a *named* anomaly type
(closed AnomalyType enum) — never exit 10, never an uncaught
``OSError``/``UnicodeDecodeError``. The cross-cutting invariants
(10.6) already enforce no-exit-10 and no-traceback; this file pins
the additional contract that the system reaches a structured
decision rather than an ad-hoc one.
"""

from __future__ import annotations

import pytest

from .conftest import EXIT_DETECTION_FAILED, EXIT_EXTRACTION_FAILED, EXIT_OK, build_and_run, corpus_by_category

_CORPUS_10_5 = corpus_by_category("10.5")
_ACCEPTED_EXIT_CODES = {EXIT_OK, EXIT_DETECTION_FAILED, EXIT_EXTRACTION_FAILED}


@pytest.mark.parametrize("entry", _CORPUS_10_5, ids=lambda e: e.cid)
def test_edge_case_reaches_a_modelled_decision(entry, tmp_path):
    palace, result = build_and_run(entry, tmp_path)
    assert result.returncode in _ACCEPTED_EXIT_CODES, (
        f"[{entry.cid}] exit {result.returncode} not in {sorted(_ACCEPTED_EXIT_CODES)}.\n" f"stderr={result.stderr!r}"
    )


@pytest.mark.parametrize("entry", _CORPUS_10_5, ids=lambda e: e.cid)
def test_edge_case_emits_at_least_one_anomaly_when_rejected(entry, tmp_path):
    palace, result = build_and_run(entry, tmp_path)
    if result.returncode == EXIT_OK:
        return  # nothing to assert: the input was tolerable
    report = result.parse_report()
    anomalies = report.get("anomalies") or []
    assert anomalies, (
        f"[{entry.cid}] non-zero exit {result.returncode} with empty anomalies list " f"(silent rejection)"
    )


@pytest.mark.parametrize("entry", _CORPUS_10_5, ids=lambda e: e.cid)
def test_edge_case_failure_stage_is_attributed(entry, tmp_path):
    palace, result = build_and_run(entry, tmp_path)
    if result.returncode == EXIT_OK:
        return
    report = result.parse_report()
    failure = report.get("failure")
    assert failure is not None, f"[{entry.cid}] non-zero exit but report.failure is null"
    stage = failure.get("stage", "")
    assert isinstance(stage, str) and stage.strip(), f"[{entry.cid}] failure.stage is empty — unattributed failure"
