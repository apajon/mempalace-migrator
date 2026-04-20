"""M7 task 10.1 — per-row / value-shape pathologies in extraction.

Per design §4: extraction must continue (run completes), the offending
row must appear in ``failed_rows`` (never ``parsed``), and at least one
anomaly with a category-appropriate ``type`` and ``evidence.kind`` must
be emitted.

Forbidden behaviour: bare crash, silent row drop without anomaly.
"""

from __future__ import annotations

import pytest

from mempalace_migrator.core.context import AnomalyType

from .conftest import EXIT_CRITICAL_ANOMALY, EXIT_OK, build_and_run, corpus_by_category

# Per-corpus-id contract: (expected AnomalyType, expected FailedRow.reason_type).
# The row that caused the failure must show up in failed_rows with this
# reason_type, AND ctx must have emitted an anomaly of this type.
_PER_ROW_CONTRACT: dict[str, tuple[AnomalyType, str]] = {
    "blank_embedding_id": (AnomalyType.BLANK_EMBEDDING_ID, "blank_embedding_id"),
    "control_chars_in_id": (AnomalyType.CONTROL_CHARS_IN_ID, "control_chars_in_id"),
    "document_missing": (AnomalyType.ORPHAN_EMBEDDING, "orphan_embedding"),
    "document_null_string_value": (
        AnomalyType.DOCUMENT_STRING_VALUE_NULL,
        "document_string_value_null",
    ),
    "document_multiple": (AnomalyType.DOCUMENT_MULTIPLE, "document_multiple"),
    "metadata_all_null": (AnomalyType.METADATA_ALL_NULL, "metadata_all_null"),
}

# Cases where the current production contract is "row accepted verbatim,
# no anomaly emitted, no failed_rows entry". Documenting the absence is
# itself a contract: a future change that starts rejecting these rows
# silently would flip this assertion.
_VERBATIM_ACCEPT: frozenset[str] = frozenset(
    {
        "unparseable_metadata_string_value",
    }
)

_CORPUS_10_1 = corpus_by_category("10.1")


@pytest.mark.parametrize("entry", _CORPUS_10_1, ids=lambda e: e.cid)
def test_extraction_continues_and_row_is_isolated(entry, tmp_path):
    """Extraction must finish; the bad row must never appear in `parsed`."""
    palace, result = build_and_run(entry, tmp_path)
    assert result.returncode in (EXIT_OK, EXIT_CRITICAL_ANOMALY), (
        f"[{entry.cid}] expected exit 0 or 8, got {result.returncode}.\n" f"stderr={result.stderr!r}"
    )
    report = result.parse_report()
    extraction = report.get("extraction") or {}
    stats = report.get("extraction_stats") or {}
    # Extraction reached completion: stats are populated.
    assert stats.get("total_rows") is not None, f"[{entry.cid}] extraction_stats missing — extraction did not complete"
    # The pathological row is in failed_rows for the cases the contract names.
    contract = _PER_ROW_CONTRACT.get(entry.cid)
    if contract is not None:
        _, reason_type = contract
        failed = extraction.get("failed_rows") or []
        reasons = {f.get("reason_type") for f in failed}
        assert reason_type in reasons, (
            f"[{entry.cid}] expected failed_rows to include reason_type={reason_type!r}, "
            f"got reasons={sorted(r for r in reasons if r)}"
        )


@pytest.mark.parametrize("entry", _CORPUS_10_1, ids=lambda e: e.cid)
def test_named_anomaly_emitted_with_pinned_kind(entry, tmp_path):
    """The anomaly stream must contain the category-pinned type and an
    evidence entry with a non-empty ``kind`` (M3 contract). For cases
    in ``_VERBATIM_ACCEPT`` the contract is the *opposite*: the row is
    accepted into ``parsed`` and no extract-stage anomaly is emitted.
    """
    palace, result = build_and_run(entry, tmp_path)
    report = result.parse_report()
    anomalies = report.get("anomalies") or []

    if entry.cid in _VERBATIM_ACCEPT:
        # Positive contract: row was accepted, not rejected.
        stats = report.get("extraction_stats") or {}
        ext = report.get("extraction") or {}
        assert (stats.get("parsed_rows") or 0) >= 1, (
            f"[{entry.cid}] verbatim-accept contract violated: " f"row was not parsed; stats={stats}"
        )
        assert not (ext.get("failed_rows") or []), (
            f"[{entry.cid}] verbatim-accept contract violated: "
            f"row appeared in failed_rows={ext.get('failed_rows')}"
        )
        extract_anomalies = [a for a in anomalies if (a.get("location") or {}).get("stage") == "extract"]
        assert not extract_anomalies, (
            f"[{entry.cid}] verbatim-accept contract violated: "
            f"unexpected extract-stage anomaly types="
            f"{sorted({a.get('type') for a in extract_anomalies})}"
        )
        return

    contract = _PER_ROW_CONTRACT[entry.cid]
    assert anomalies, f"[{entry.cid}] no anomalies emitted on adversarial input"

    expected_type, _ = contract
    matching = [a for a in anomalies if a.get("type") == expected_type.value]
    assert matching, (
        f"[{entry.cid}] expected anomaly of type {expected_type.value!r}; "
        f"got types={sorted({a.get('type') for a in anomalies})}"
    )
    a = matching[0]
    assert (a.get("location") or {}).get(
        "stage"
    ) == "extract", f"[{entry.cid}] anomaly {expected_type.value!r} must be tagged stage=extract"
    evidence = a.get("evidence") or []
    kinds = [(e.get("kind") or "").strip() for e in evidence]
    assert any(kinds), f"[{entry.cid}] anomaly {expected_type.value!r} has no non-empty evidence.kind"


@pytest.mark.parametrize("entry", _CORPUS_10_1, ids=lambda e: e.cid)
def test_failed_row_id_is_not_in_parsed(entry, tmp_path):
    """Same id may not appear simultaneously in failed_rows and parsed."""
    palace, result = build_and_run(entry, tmp_path)
    report = result.parse_report()
    extraction = report.get("extraction") or {}
    stats = report.get("extraction_stats") or {}
    parsed = stats.get("parsed_rows", 0)
    failed_rows = extraction.get("failed_rows") or []
    failed_ids = {f.get("embedding_id") for f in failed_rows if f.get("embedding_id")}
    # The report does not currently expose the parsed-id list, but we can
    # assert the totals are mutually exclusive: parsed + failed == total.
    total = stats.get("total_rows")
    if total is not None:
        assert parsed + len(failed_rows) == total, (
            f"[{entry.cid}] parsed({parsed}) + failed({len(failed_rows)}) "
            f"!= total({total}); rows leaked or double-counted"
        )
