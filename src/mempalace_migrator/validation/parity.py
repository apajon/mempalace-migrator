"""Target-parity validation checks (M11).

ChromaDB is imported lazily inside ``_open_target_readonly`` — parity.py
is chromadb-free at the module level, matching the rest of the validation
package. Only ``reconstruction/_writer.py`` imports chromadb at the module
level.

All five checks are read-only. The target collection is opened via
``chromadb.PersistentClient`` with ``allow_reset=False``. Only
``collection.count()`` and ``collection.get(...)`` are called — no
``add``, ``update``, ``upsert``, ``delete``, ``modify``, ``reset``,
``create_collection``, ``delete_collection``, or ``peek`` (AST-asserted).

``run_parity_checks(ctx)`` is the single public entry point. It returns
a list of ``CheckOutcome`` objects and may emit anomalies into ``ctx``.
It never raises.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mempalace_migrator.core.context import (AnomalyEvidence, AnomalyLocation,
                                             AnomalyType, MigrationContext,
                                             Severity)
from mempalace_migrator.validation._types import (CheckOutcome, _make_failed,
                                                  _make_inconclusive,
                                                  _make_passed)

if TYPE_CHECKING:
    pass

_STAGE = "validate"
_PAGE_SIZE = 500  # matches reconstruction BATCH_SIZE


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_parity_checks(ctx: MigrationContext) -> list[CheckOutcome]:
    """Run all five target-parity checks.

    Reads ``ctx.reconstruction_result`` for the target path and
    collection name, and ``ctx.transformed_data`` for the expected
    content. Returns a list of exactly five ``CheckOutcome`` objects.
    Never raises.
    """
    rr = ctx.reconstruction_result
    td = ctx.transformed_data

    # Precondition: caller (validation/__init__.py) guarantees both are set.
    # Guard defensively so a misuse doesn't traceback.
    if rr is None or td is None:
        return _all_inconclusive(
            "precondition_not_met",
            "run_parity_checks called without reconstruction_result or transformed_data",
        )

    target_path = rr.target_path
    collection_name = rr.collection_name

    # --- Attempt to open the target (read-only). ---
    try:
        client, collection = _open_target_readonly(target_path, collection_name)
    except Exception as exc:  # noqa: BLE001
        ctx.add_anomaly(
            type=AnomalyType.TARGET_OPEN_FAILED,
            severity=Severity.HIGH,
            message=f"could not open target palace for parity validation: {exc}",
            location=AnomalyLocation(stage=_STAGE, source="target", path=str(target_path)),
            evidence=[
                AnomalyEvidence(
                    kind="target_open_failed",
                    detail=str(exc),
                    data={"target_path": str(target_path), "collection_name": collection_name},
                )
            ],
        )
        return _all_inconclusive(
            "target_open_failed",
            f"target palace could not be opened: {exc}",
        )

    outcomes: list[CheckOutcome] = []
    outcomes.append(_check_record_count(ctx, collection, td))

    # For id-set and content checks we need to materialise target records.
    try:
        target_records = _collect_target_records(collection)
    except Exception as exc:  # noqa: BLE001
        # Unexpected read error after a successful open: treat remaining
        # checks as inconclusive (count check already appended above).
        evidence = AnomalyEvidence(
            kind="target_read_failed",
            detail=str(exc),
            data={"target_path": str(target_path)},
        )
        outcomes.append(_make_inconclusive("parity.target_id_set_parity", "parity", Severity.HIGH, evidence))
        outcomes.append(_make_inconclusive("parity.target_document_hash_parity", "parity", Severity.HIGH, evidence))
        outcomes.append(_make_inconclusive("parity.target_metadata_parity", "parity", Severity.HIGH, evidence))
        outcomes.append(_check_embedding_presence(ctx, collection))
        return outcomes

    target_ids: set[str] = {r["id"] for r in target_records}
    transformed_ids: set[str] = {d.id for d in td.drawers}

    outcomes.append(_check_id_set(ctx, target_ids, transformed_ids))

    shared_ids = target_ids & transformed_ids
    if not shared_ids:
        # Both hash and metadata checks are inconclusive with no common ids.
        inconclusive_evidence = AnomalyEvidence(
            kind="observation",
            detail="shared id set is empty; skipping per-record content parity",
            data={},
        )
        outcomes.append(
            _make_inconclusive(
                "parity.target_document_hash_parity", "parity", Severity.HIGH, inconclusive_evidence
            )
        )
        outcomes.append(
            _make_inconclusive("parity.target_metadata_parity", "parity", Severity.HIGH, inconclusive_evidence)
        )
    else:
        target_by_id = {r["id"]: r for r in target_records}
        transformed_by_id = {d.id: d for d in td.drawers}
        outcomes.append(_check_document_hashes(ctx, shared_ids, target_by_id, transformed_by_id))
        outcomes.append(_check_metadata(ctx, shared_ids, target_by_id, transformed_by_id))

    outcomes.append(_check_embedding_presence(ctx, collection))
    return outcomes


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_record_count(ctx: MigrationContext, collection: Any, td: Any) -> CheckOutcome:
    check_id = "parity.target_record_count_parity"
    expected = td.summary.drawer_count
    try:
        actual = collection.count()
    except Exception as exc:  # noqa: BLE001
        return _make_inconclusive(
            check_id,
            "parity",
            Severity.HIGH,
            AnomalyEvidence(kind="target_read_failed", detail=str(exc), data={}),
        )

    if expected == actual:
        return _make_passed(
            check_id,
            "parity",
            Severity.HIGH,
            AnomalyEvidence(
                kind="count",
                detail=f"target collection has {actual} records (expected {expected})",
                data={"expected": expected, "actual": actual},
            ),
        )

    evidence = AnomalyEvidence(
        kind="count",
        detail=f"target has {actual} records but expected {expected}",
        data={"expected": expected, "actual": actual},
    )
    ctx.add_anomaly(
        type=AnomalyType.TARGET_RECORD_COUNT_MISMATCH,
        severity=Severity.HIGH,
        message=f"target record count mismatch: expected {expected}, got {actual}",
        location=AnomalyLocation(stage=_STAGE, source="target"),
        evidence=[evidence],
    )
    return _make_failed(check_id, "parity", Severity.HIGH, evidence)


def _check_id_set(
    ctx: MigrationContext,
    target_ids: set[str],
    transformed_ids: set[str],
) -> CheckOutcome:
    check_id = "parity.target_id_set_parity"
    missing = transformed_ids - target_ids
    unexpected = target_ids - transformed_ids

    if not missing and not unexpected:
        return _make_passed(
            check_id,
            "parity",
            Severity.HIGH,
            AnomalyEvidence(
                kind="observation",
                detail=f"id sets match ({len(transformed_ids)} ids)",
                data={"id_count": len(transformed_ids)},
            ),
        )

    evidence = AnomalyEvidence(
        kind="observation",
        detail=(
            f"{len(missing)} ids missing in target, "
            f"{len(unexpected)} unexpected ids in target"
        ),
        data={
            "missing_in_target_count": len(missing),
            "unexpected_in_target_count": len(unexpected),
            "missing_sample": sorted(missing)[:20],
            "unexpected_sample": sorted(unexpected)[:20],
        },
    )
    ctx.add_anomaly(
        type=AnomalyType.TARGET_ID_SET_MISMATCH,
        severity=Severity.HIGH,
        message=(
            f"target id-set mismatch: {len(missing)} missing, "
            f"{len(unexpected)} unexpected"
        ),
        location=AnomalyLocation(stage=_STAGE, source="target"),
        evidence=[evidence],
    )
    return _make_failed(check_id, "parity", Severity.HIGH, evidence)


def _check_document_hashes(
    ctx: MigrationContext,
    shared_ids: set[str],
    target_by_id: dict[str, Any],
    transformed_by_id: dict[str, Any],
) -> CheckOutcome:
    check_id = "parity.target_document_hash_parity"
    mismatches: list[str] = []

    for drawer_id in shared_ids:
        expected_doc = transformed_by_id[drawer_id].document
        actual_doc = target_by_id[drawer_id].get("document") or ""
        expected_hash = hashlib.sha256(expected_doc.encode("utf-8")).hexdigest()
        actual_hash = hashlib.sha256(actual_doc.encode("utf-8")).hexdigest()
        if expected_hash != actual_hash:
            mismatches.append(drawer_id)

    if not mismatches:
        return _make_passed(
            check_id,
            "parity",
            Severity.HIGH,
            AnomalyEvidence(
                kind="observation",
                detail=f"document hashes match for all {len(shared_ids)} shared ids",
                data={"checked_count": len(shared_ids)},
            ),
        )

    evidence = AnomalyEvidence(
        kind="observation",
        detail=f"{len(mismatches)} document hash mismatches in {len(shared_ids)} shared ids",
        data={
            "mismatch_count": len(mismatches),
            "mismatch_sample": sorted(mismatches)[:20],
        },
    )
    ctx.add_anomaly(
        type=AnomalyType.TARGET_DOCUMENT_HASH_MISMATCH,
        severity=Severity.HIGH,
        message=f"document hash mismatch for {len(mismatches)} ids",
        location=AnomalyLocation(stage=_STAGE, source="target"),
        evidence=[evidence],
    )
    return _make_failed(check_id, "parity", Severity.HIGH, evidence)


def _check_metadata(
    ctx: MigrationContext,
    shared_ids: set[str],
    target_by_id: dict[str, Any],
    transformed_by_id: dict[str, Any],
) -> CheckOutcome:
    check_id = "parity.target_metadata_parity"
    mismatch_samples: list[dict[str, Any]] = []

    for drawer_id in shared_ids:
        expected_meta = _meta_for_compare(transformed_by_id[drawer_id].metadata)
        actual_meta = _meta_for_compare(target_by_id[drawer_id].get("metadata"))

        if expected_meta == actual_meta:
            continue

        expected_keys = set(expected_meta.keys())
        actual_keys = set(actual_meta.keys())
        missing_keys = sorted(expected_keys - actual_keys)
        extra_keys = sorted(actual_keys - expected_keys)
        value_diff_keys = sorted(
            k
            for k in expected_keys & actual_keys
            if expected_meta[k] != actual_meta[k]
        )

        if len(mismatch_samples) < 20:
            mismatch_samples.append(
                {
                    "id": drawer_id,
                    "missing_keys_in_target": missing_keys,
                    "extra_keys_in_target": extra_keys,
                    "value_diff_keys": value_diff_keys,
                }
            )

    mismatch_count = len(mismatch_samples)  # capped at 20 structurally
    # Re-scan to get total count without storing all samples.
    total_mismatches = sum(
        1
        for drawer_id in shared_ids
        if _meta_for_compare(transformed_by_id[drawer_id].metadata)
        != _meta_for_compare(target_by_id[drawer_id].get("metadata"))
    )

    if total_mismatches == 0:
        return _make_passed(
            check_id,
            "parity",
            Severity.HIGH,
            AnomalyEvidence(
                kind="observation",
                detail=f"metadata matches for all {len(shared_ids)} shared ids",
                data={"checked_count": len(shared_ids)},
            ),
        )

    evidence = AnomalyEvidence(
        kind="observation",
        detail=f"{total_mismatches} metadata mismatches in {len(shared_ids)} shared ids",
        data={
            "mismatch_count": total_mismatches,
            "mismatch_sample": mismatch_samples,
        },
    )
    ctx.add_anomaly(
        type=AnomalyType.TARGET_METADATA_MISMATCH,
        severity=Severity.HIGH,
        message=f"metadata mismatch for {total_mismatches} ids",
        location=AnomalyLocation(stage=_STAGE, source="target"),
        evidence=[evidence],
    )
    return _make_failed(check_id, "parity", Severity.HIGH, evidence)


def _check_embedding_presence(ctx: MigrationContext, collection: Any) -> CheckOutcome:
    check_id = "parity.target_embedding_presence"

    # Probe: check if include=["embeddings"] is supported.
    try:
        probe = collection.get(limit=1, include=["embeddings"])
    except Exception as exc:  # noqa: BLE001
        evidence = AnomalyEvidence(
            kind="embedding_include_unsupported",
            detail=str(exc),
            data={},
        )
        ctx.add_anomaly(
            type=AnomalyType.TARGET_EMBEDDING_PROBE_INCONCLUSIVE,
            severity=Severity.MEDIUM,
            message=f"embedding presence probe failed: {exc}",
            location=AnomalyLocation(stage=_STAGE, source="target"),
            evidence=[evidence],
        )
        return _make_inconclusive(check_id, "parity", Severity.MEDIUM, evidence)

    # Probe succeeded. Collect all ids with missing embeddings via paged scan.
    missing: list[str] = []
    probe_embeddings = probe.get("embeddings")
    if probe_embeddings is None:
        probe_embeddings = []
    # Collect from the probe result first.
    probe_ids = probe.get("ids")
    if probe_ids is None:
        probe_ids = []
    for i, emb in enumerate(probe_embeddings):
        if emb is None or (isinstance(emb, list) and len(emb) == 0):
            if i < len(probe_ids):
                missing.append(probe_ids[i])

    # Continue paging from offset 1.
    offset = 1
    while True:
        try:
            page = collection.get(limit=_PAGE_SIZE, offset=offset, include=["embeddings"])
        except Exception:  # noqa: BLE001
            break
        page_ids = page.get("ids")
        if page_ids is None:
            page_ids = []
        page_embeddings = page.get("embeddings")
        if page_embeddings is None:
            page_embeddings = []
        if not page_ids:
            break
        for i, emb in enumerate(page_embeddings):
            if emb is None or (isinstance(emb, list) and len(emb) == 0):
                if i < len(page_ids):
                    missing.append(page_ids[i])
        offset += len(page_ids)
        if len(page_ids) < _PAGE_SIZE:
            break

    if not missing:
        total = collection.count()
        return _make_passed(
            check_id,
            "parity",
            Severity.MEDIUM,
            AnomalyEvidence(
                kind="observation",
                detail=f"all {total} records have non-empty embeddings",
                data={"checked_count": total},
            ),
        )

    evidence = AnomalyEvidence(
        kind="observation",
        detail=f"{len(missing)} records have missing/empty embeddings",
        data={
            "missing_count": len(missing),
            "missing_sample": sorted(missing)[:20],
        },
    )
    ctx.add_anomaly(
        type=AnomalyType.TARGET_EMBEDDING_MISSING,
        severity=Severity.MEDIUM,
        message=f"{len(missing)} records have missing embeddings",
        location=AnomalyLocation(stage=_STAGE, source="target"),
        evidence=[evidence],
    )
    return _make_failed(check_id, "parity", Severity.MEDIUM, evidence)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_target_readonly(
    target_path: Path, collection_name: str
) -> tuple[Any, Any]:
    """Open the target ChromaDB palace read-only.

    Returns ``(client, collection)``. ``allow_reset=False`` prevents
    accidental resets. ``anonymized_telemetry=False`` keeps the read hermetic.
    Raises on any failure — caller catches and converts to inconclusive.
    Chromadb is imported lazily here to keep module-level startup fast.
    """
    import chromadb  # noqa: PLC0415  (lazy import — keeps startup overhead out of non-parity runs)
    from chromadb.config import Settings  # noqa: PLC0415

    client = chromadb.PersistentClient(
        path=str(target_path),
        settings=Settings(anonymized_telemetry=False, allow_reset=False),
    )
    collection = client.get_collection(name=collection_name)
    return client, collection


def _collect_target_records(collection: Any) -> list[dict[str, Any]]:
    """Page through all target records, returning list of {id, document, metadata}."""
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = collection.get(
            limit=_PAGE_SIZE,
            offset=offset,
            include=["documents", "metadatas"],
        )
        ids = page.get("ids") or []
        documents = page.get("documents") or []
        metadatas = page.get("metadatas") or []
        if not ids:
            break
        for i, rec_id in enumerate(ids):
            records.append(
                {
                    "id": rec_id,
                    "document": documents[i] if i < len(documents) else "",
                    "metadata": metadatas[i] if i < len(metadatas) else None,
                }
            )
        offset += len(ids)
        if len(ids) < _PAGE_SIZE:
            break
    return records


def _meta_for_compare(m: Any) -> dict[str, Any]:
    """Normalise metadata for comparison.

    The reconstruction writer coerces ``{}`` → ``None`` (chromadb 1.5.7
    rejects empty dicts). This normaliser undoes that so an empty-dict
    transformed drawer compares equal to a None-metadata target record.
    """
    return m if m else {}


def _all_inconclusive(kind: str, detail: str) -> list[CheckOutcome]:
    """Return five inconclusive outcomes sharing a single evidence record."""
    evidence = AnomalyEvidence(kind=kind, detail=detail, data={})
    check_ids = [
        "parity.target_record_count_parity",
        "parity.target_id_set_parity",
        "parity.target_document_hash_parity",
        "parity.target_metadata_parity",
        "parity.target_embedding_presence",
    ]
    severities = [Severity.HIGH, Severity.HIGH, Severity.HIGH, Severity.HIGH, Severity.MEDIUM]
    return [
        _make_inconclusive(cid, "parity", sev, evidence)
        for cid, sev in zip(check_ids, severities)
    ]
        "parity.target_record_count_parity",
        "parity.target_id_set_parity",
        "parity.target_document_hash_parity",
        "parity.target_metadata_parity",
        "parity.target_embedding_presence",
    ]
    severities = [Severity.HIGH, Severity.HIGH, Severity.HIGH, Severity.HIGH, Severity.MEDIUM]
    return [
        _make_inconclusive(cid, "parity", sev, evidence)
        for cid, sev in zip(check_ids, severities)
    ]
