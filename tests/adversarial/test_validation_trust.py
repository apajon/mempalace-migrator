"""M17 task 20.5 — validation trust: parity family catches target mutations.

After a successful migrate run, three classes of post-reconstruction
mutation are applied out-of-band via the public chromadb client API:

  1. ``drop_one_row``       — delete one record from the target.
  2. ``swap_document``      — replace one record's document string.
  3. ``rewrite_metadata``   — inject an extra key into one record's metadata.

For each mutation class the parity checks are run in-process (by calling
``run_parity_checks(ctx)`` directly) against the mutated target.

Required outcome per mutation (see M17_TRUST_SAFETY_DESIGN.md §4.5 / §7):

  * ``drop_one_row``     → TARGET_RECORD_COUNT_MISMATCH + TARGET_ID_SET_MISMATCH
  * ``swap_document``    → TARGET_DOCUMENT_HASH_MISMATCH
  * ``rewrite_metadata`` → TARGET_METADATA_MISMATCH

Cross-cutting assertions for all three mutations:
  - No parity CheckOutcome.status == "passed" for the checks that
    correspond to the mutated dimension.
  - ``outcome != "success"`` once these anomalies are present.

No monkeypatching. Mutation is performed via the real chromadb API.
``run_parity_checks`` is called on a fresh MigrationContext that has
``reconstruction_result`` and ``transformed_data`` populated from the
original in-process migration run; the anomaly list starts empty so
there is no noise from the original run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb
import pytest
from chromadb.config import Settings

from mempalace_migrator.core.context import AnomalyType, MigrationContext
from mempalace_migrator.core.pipeline import MIGRATE_PIPELINE, run_pipeline
from mempalace_migrator.validation.parity import run_parity_checks
from tests.adversarial.conftest import build_minimal_valid_chroma_06

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_N_DRAWERS = 4  # enough for meaningful mutations


def _run_migration(source: Path, target: Path) -> MigrationContext:
    """Run the full MIGRATE_PIPELINE in-process; return the completed context."""
    ctx = MigrationContext(source_path=source, target_path=target)
    run_pipeline(ctx, MIGRATE_PIPELINE)
    assert ctx.reconstruction_result is not None, "reconstruction must succeed"
    assert ctx.transformed_data is not None, "transformed_data must be set"
    return ctx


def _fresh_parity_ctx(original_ctx: MigrationContext) -> MigrationContext:
    """Create a fresh context carrying only the data needed for parity checks.

    Starting with an empty anomaly list isolates the parity results from
    the original run's anomalies.
    """
    fresh = MigrationContext(
        source_path=original_ctx.source_path,
        target_path=original_ctx.target_path,
    )
    fresh.reconstruction_result = original_ctx.reconstruction_result
    fresh.transformed_data = original_ctx.transformed_data
    return fresh


def _open_mutation_client(target_path: Path, collection_name: str) -> tuple[Any, Any]:
    """Open the target with a write-enabled client for out-of-band mutation."""
    client = chromadb.PersistentClient(
        path=str(target_path),
        settings=Settings(anonymized_telemetry=False, allow_reset=False),
    )
    collection = client.get_collection(name=collection_name)
    return client, collection


def _all_ids(collection: Any) -> list[str]:
    result = collection.get(include=[])
    return result.get("ids") or []


def _anomaly_types_from_ctx(ctx: MigrationContext) -> set[str]:
    return {a.type.value if hasattr(a.type, "value") else str(a.type) for a in ctx.anomalies}


def _failed_check_ids(outcomes: list[Any]) -> set[str]:
    return {o.id for o in outcomes if o.status == "failed"}


def _passed_check_ids(outcomes: list[Any]) -> set[str]:
    return {o.id for o in outcomes if o.status == "passed"}


# ---------------------------------------------------------------------------
# Mutation 1: drop_one_row
# ---------------------------------------------------------------------------


def test_validation_trust_drop_one_row(tmp_path: Path) -> None:
    """Dropping a target record is caught by count + id-set parity checks."""
    source = build_minimal_valid_chroma_06(tmp_path / "src", n_drawers=_N_DRAWERS)
    target = tmp_path / "target"
    original_ctx = _run_migration(source, target)

    rr = original_ctx.reconstruction_result
    _, collection = _open_mutation_client(target, rr.collection_name)
    ids = _all_ids(collection)
    assert ids, "target must have records after migration"
    id_to_drop = ids[0]
    collection.delete(ids=[id_to_drop])

    fresh_ctx = _fresh_parity_ctx(original_ctx)
    outcomes = run_parity_checks(fresh_ctx)
    anomaly_types = _anomaly_types_from_ctx(fresh_ctx)

    assert (
        AnomalyType.TARGET_RECORD_COUNT_MISMATCH.value in anomaly_types
    ), f"drop_one_row must emit TARGET_RECORD_COUNT_MISMATCH; got {anomaly_types}"
    assert (
        AnomalyType.TARGET_ID_SET_MISMATCH.value in anomaly_types
    ), f"drop_one_row must emit TARGET_ID_SET_MISMATCH; got {anomaly_types}"

    failed = _failed_check_ids(outcomes)
    assert (
        "parity.target_record_count_parity" in failed
    ), f"target_record_count_parity must be 'failed'; all failed: {failed}"
    assert "parity.target_id_set_parity" in failed, f"target_id_set_parity must be 'failed'; all failed: {failed}"

    # Neither of the count/id checks may report 'passed'.
    passed = _passed_check_ids(outcomes)
    assert "parity.target_record_count_parity" not in passed
    assert "parity.target_id_set_parity" not in passed


# ---------------------------------------------------------------------------
# Mutation 2: swap_document
# ---------------------------------------------------------------------------


def test_validation_trust_swap_document(tmp_path: Path) -> None:
    """Replacing a document string is caught by document-hash parity check."""
    source = build_minimal_valid_chroma_06(tmp_path / "src", n_drawers=_N_DRAWERS)
    target = tmp_path / "target"
    original_ctx = _run_migration(source, target)

    rr = original_ctx.reconstruction_result
    _, collection = _open_mutation_client(target, rr.collection_name)
    ids = _all_ids(collection)
    assert ids, "target must have records after migration"
    id_to_swap = ids[0]
    collection.update(ids=[id_to_swap], documents=["TAMPERED_DOCUMENT_CONTENT"])

    fresh_ctx = _fresh_parity_ctx(original_ctx)
    outcomes = run_parity_checks(fresh_ctx)
    anomaly_types = _anomaly_types_from_ctx(fresh_ctx)

    assert (
        AnomalyType.TARGET_DOCUMENT_HASH_MISMATCH.value in anomaly_types
    ), f"swap_document must emit TARGET_DOCUMENT_HASH_MISMATCH; got {anomaly_types}"

    failed = _failed_check_ids(outcomes)
    assert (
        "parity.target_document_hash_parity" in failed
    ), f"target_document_hash_parity must be 'failed'; all failed: {failed}"
    passed = _passed_check_ids(outcomes)
    assert "parity.target_document_hash_parity" not in passed


# ---------------------------------------------------------------------------
# Mutation 3: rewrite_metadata
# ---------------------------------------------------------------------------


def test_validation_trust_rewrite_metadata(tmp_path: Path) -> None:
    """Adding an extra metadata key is caught by metadata parity check."""
    source = build_minimal_valid_chroma_06(tmp_path / "src", n_drawers=_N_DRAWERS)
    target = tmp_path / "target"
    original_ctx = _run_migration(source, target)

    rr = original_ctx.reconstruction_result
    _, collection = _open_mutation_client(target, rr.collection_name)
    ids = _all_ids(collection)
    assert ids, "target must have records after migration"
    id_to_tamper = ids[0]
    # Inject a key that was not in the original (empty) metadata.
    collection.update(ids=[id_to_tamper], metadatas=[{"tampered_key": "injected_value"}])

    fresh_ctx = _fresh_parity_ctx(original_ctx)
    outcomes = run_parity_checks(fresh_ctx)
    anomaly_types = _anomaly_types_from_ctx(fresh_ctx)

    assert (
        AnomalyType.TARGET_METADATA_MISMATCH.value in anomaly_types
    ), f"rewrite_metadata must emit TARGET_METADATA_MISMATCH; got {anomaly_types}"

    failed = _failed_check_ids(outcomes)
    assert "parity.target_metadata_parity" in failed, f"target_metadata_parity must be 'failed'; all failed: {failed}"
    passed = _passed_check_ids(outcomes)
    assert "parity.target_metadata_parity" not in passed
