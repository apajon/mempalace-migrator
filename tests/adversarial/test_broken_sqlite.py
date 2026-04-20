"""M7 task 10.2 — broken SQLite (pre-flight or scan failure).

Per design §4: detection or extraction must reject; failure.stage ∈
{detect, extract}; at least one CRITICAL anomaly must explain the
rejection. Forbidden: partial result + exit 0; bare ``sqlite3.DatabaseError``
escaping to the user.
"""

from __future__ import annotations

import pytest

from .conftest import EXIT_DETECTION_FAILED, EXIT_EXTRACTION_FAILED, build_and_run, corpus_by_category

_CORPUS_10_2 = corpus_by_category("10.2")
_REJECT_STAGES = {"detect", "extract"}


@pytest.mark.parametrize("entry", _CORPUS_10_2, ids=lambda e: e.cid)
def test_broken_sqlite_is_rejected_with_known_stage(entry, tmp_path):
    palace, result = build_and_run(entry, tmp_path)
    rc = result.returncode
    assert rc in (EXIT_DETECTION_FAILED, EXIT_EXTRACTION_FAILED), (
        f"[{entry.cid}] expected detection or extraction rejection, got exit {rc}.\n" f"stderr={result.stderr!r}"
    )
    report = result.parse_report()
    failure = report.get("failure")
    assert failure is not None, f"[{entry.cid}] outcome=failure must populate report.failure"
    assert (
        failure.get("stage") in _REJECT_STAGES
    ), f"[{entry.cid}] failure.stage={failure.get('stage')!r} not in {_REJECT_STAGES}"


@pytest.mark.parametrize("entry", _CORPUS_10_2, ids=lambda e: e.cid)
def test_critical_anomaly_explains_the_rejection(entry, tmp_path):
    palace, result = build_and_run(entry, tmp_path)
    report = result.parse_report()
    failure_stage = (report.get("failure") or {}).get("stage")
    anomalies = report.get("anomalies") or []
    critical_for_stage = [
        a
        for a in anomalies
        if a.get("severity") == "critical" and (a.get("location") or {}).get("stage") == failure_stage
    ]
    assert critical_for_stage, (
        f"[{entry.cid}] no CRITICAL anomaly for failing stage {failure_stage!r}; "
        f"anomalies={[(a.get('type'), a.get('severity'), (a.get('location') or {}).get('stage')) for a in anomalies]}"
    )


@pytest.mark.parametrize("entry", _CORPUS_10_2, ids=lambda e: e.cid)
def test_no_raw_sqlite_error_class_in_stderr(entry, tmp_path):
    """Production code must wrap ``sqlite3.DatabaseError`` in
    ``ExtractionError`` / structured anomalies, never let the class name
    leak via an uncaught exception summary on stderr.
    """
    palace, result = build_and_run(entry, tmp_path)
    # The class name may legitimately appear inside an evidence detail
    # string ("DatabaseError('database disk image is malformed')") — that
    # is structured. What we forbid is an uncaught traceback.
    assert "Traceback (most recent call last):" not in result.stderr, (
        f"[{entry.cid}] uncaught traceback escaped to stderr.\n" f"stderr={result.stderr!r}"
    )
