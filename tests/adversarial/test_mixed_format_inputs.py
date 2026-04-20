"""M7 task 10.3 — mixed-format / contradictory-signal inputs.

Per design §4: detection refuses (exit 2) with ``failure.stage="detect"``;
detection result has non-empty contradictions (or an evidence record
explaining the conflict); confidence band degrades from HIGH.
"""

from __future__ import annotations

import pytest

from .conftest import EXIT_DETECTION_FAILED, build_and_run, corpus_by_category

_CORPUS_10_3 = corpus_by_category("10.3")


@pytest.mark.parametrize("entry", _CORPUS_10_3, ids=lambda e: e.cid)
def test_mixed_format_is_rejected_at_detect(entry, tmp_path):
    palace, result = build_and_run(entry, tmp_path)
    assert result.returncode == EXIT_DETECTION_FAILED, (
        f"[{entry.cid}] expected detection rejection (exit 2), got {result.returncode}.\n" f"stderr={result.stderr!r}"
    )
    report = result.parse_report()
    failure = report.get("failure") or {}
    assert failure.get("stage") == "detect", f"[{entry.cid}] failure.stage={failure.get('stage')!r} != 'detect'"


@pytest.mark.parametrize("entry", _CORPUS_10_3, ids=lambda e: e.cid)
def test_detection_records_the_conflict(entry, tmp_path):
    """The detection result must show *some* structured evidence of
    uncertainty: contradictions, an inconsistency evidence record, or a
    confidence band below HIGH. A bare HIGH classification on
    contradictory input is the silent-pick bug.
    """
    palace, result = build_and_run(entry, tmp_path)
    report = result.parse_report()
    detection = report.get("detection") or {}
    contradictions = detection.get("contradictions") or []
    evidence = detection.get("evidence") or []
    inconsistency_evidence = [e for e in evidence if e.get("kind") == "inconsistency"]
    band = detection.get("confidence_band")
    assert contradictions or inconsistency_evidence or band != "HIGH", (
        f"[{entry.cid}] detection has no contradictions, no inconsistency "
        f"evidence, and band=HIGH on adversarial input; detection={detection}"
    )


@pytest.mark.parametrize("entry", _CORPUS_10_3, ids=lambda e: e.cid)
def test_detection_confidence_is_not_high(entry, tmp_path):
    """A contradictory or unparseable signal must NOT yield a HIGH
    classification — that would mean detection silently picked a side.
    """
    palace, result = build_and_run(entry, tmp_path)
    report = result.parse_report()
    detection = report.get("detection") or {}
    band = detection.get("confidence_band")
    # The detection refused, so band must reflect uncertainty.
    assert band != "HIGH", (
        f"[{entry.cid}] detection band=HIGH on contradictory input "
        f"(detection silently picked a side); detection={detection}"
    )
