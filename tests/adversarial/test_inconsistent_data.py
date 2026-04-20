"""M7 task 10.4 — inconsistent data surfaced by validation.

Per design §4: validation in ``inspect`` mode must fail or be
inconclusive (never ``passed``) when extraction recorded a structural
inconsistency such as duplicate ids or all-rows-unparseable.
"""

from __future__ import annotations

import pytest

from .conftest import EXIT_CRITICAL_ANOMALY, EXIT_OK, build_and_run, corpus_by_category

_CORPUS_10_4 = corpus_by_category("10.4")


@pytest.mark.parametrize("entry", _CORPUS_10_4, ids=lambda e: e.cid)
def test_validation_does_not_silently_pass(entry, tmp_path):
    palace, result = build_and_run(entry, tmp_path)
    assert result.returncode in (
        EXIT_OK,
        EXIT_CRITICAL_ANOMALY,
    ), f"[{entry.cid}] expected exit 0 or 8, got {result.returncode}"
    report = result.parse_report()
    validation = report.get("validation")
    assert validation is not None, f"[{entry.cid}] inspect run must populate report.validation"
    counts = validation.get("summary_counts") or {}
    failed = counts.get("failed", 0)
    inconclusive = counts.get("inconclusive", 0)
    # The deliberately-broken palace must surface as at least one failed
    # or inconclusive check; if every check passes we have a silent-success
    # bug in validation.
    assert (failed + inconclusive) > 0, (
        f"[{entry.cid}] validation summary_counts={counts!r} on broken input — " f"all checks passed silently"
    )


@pytest.mark.parametrize("entry", _CORPUS_10_4, ids=lambda e: e.cid)
def test_validation_band_is_not_high(entry, tmp_path):
    palace, result = build_and_run(entry, tmp_path)
    report = result.parse_report()
    validation = report.get("validation") or {}
    band = validation.get("confidence_band")
    assert band != "HIGH", f"[{entry.cid}] validation confidence_band=HIGH on inconsistent input"


@pytest.mark.parametrize("entry", _CORPUS_10_4, ids=lambda e: e.cid)
def test_extraction_recorded_a_structural_anomaly(entry, tmp_path):
    """The validation outcome must be backed by an extract-stage anomaly
    (i.e. the inconsistency was actually noticed, not inferred later).
    """
    palace, result = build_and_run(entry, tmp_path)
    report = result.parse_report()
    anomalies = report.get("anomalies") or []
    extract_anomalies = [a for a in anomalies if (a.get("location") or {}).get("stage") == "extract"]
    assert extract_anomalies, f"[{entry.cid}] no extract-stage anomaly explains the validation downgrade"
