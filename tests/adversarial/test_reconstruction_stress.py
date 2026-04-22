"""M12 task 15.9 — batch-size boundary stress tests.

Parametrised over n_rows ∈ {BATCH_SIZE - 1, BATCH_SIZE, BATCH_SIZE + 1,
2*BATCH_SIZE + 1} to exercise all batch-boundary edges.

``BATCH_SIZE`` is imported from ``reconstruction/_writer.py`` — not
hard-coded — so a future tuning change is caught automatically.

Strategy: run the pipeline in-process (not subprocess) because chromadb
writes are slow enough that the subprocess 30s timeout fires on large
fixtures.  The in-process run verifies the same contracts:
  - pipeline raises no MigratorError (success)
  - reconstruction_result.imported_count == n_rows
  - All five parity checks pass (no parity anomaly types)
  - target is cleaned up after the test (teardown)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mempalace_migrator.core.context import MigrationContext
from mempalace_migrator.core.errors import MigratorError
from mempalace_migrator.core.pipeline import MIGRATE_PIPELINE, run_pipeline
from mempalace_migrator.reconstruction._writer import BATCH_SIZE
from tests.adversarial.conftest import build_large_valid_source

# ---------------------------------------------------------------------------
# Parametrised stress sizes
# ---------------------------------------------------------------------------

_STRESS_SIZES = [
    pytest.param(BATCH_SIZE - 1, id=f"n={BATCH_SIZE - 1}_below_boundary"),
    pytest.param(BATCH_SIZE, id=f"n={BATCH_SIZE}_exact_boundary"),
    pytest.param(BATCH_SIZE + 1, id=f"n={BATCH_SIZE + 1}_above_boundary"),
    pytest.param(2 * BATCH_SIZE + 1, id=f"n={2 * BATCH_SIZE + 1}_two_full_plus_one"),
]

# Parity anomaly types that must be absent for a "success" run.
_PARITY_ANOMALY_TYPES = frozenset(
    {
        "target_record_count_mismatch",
        "target_id_set_mismatch",
        "target_document_hash_mismatch",
        "target_metadata_mismatch",
        "target_embedding_missing",
        "target_open_failed",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_parity_clean(ctx: MigrationContext, *, cid: str) -> None:
    """Assert no parity-family anomaly is present in the context."""
    anomaly_types = {(a.type.value if hasattr(a.type, "value") else str(a.type)) for a in ctx.anomalies}
    parity_hits = anomaly_types & _PARITY_ANOMALY_TYPES
    assert not parity_hits, f"[{cid}] parity anomalies found in stress run: {parity_hits}"


def _assert_imported_count(ctx: MigrationContext, expected: int, *, cid: str) -> None:
    rr = ctx.reconstruction_result
    assert rr is not None, f"[{cid}] reconstruction_result is None"
    imported = getattr(rr, "imported_count", None)
    assert imported == expected, f"[{cid}] imported_count={imported!r}, expected {expected}"


# ---------------------------------------------------------------------------
# Stress tests (in-process pipeline)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_rows", _STRESS_SIZES)
def test_stress_batch_boundary(n_rows: int, tmp_path):
    """A successful migrate with *n_rows* drawers must import all rows and pass parity."""
    cid = f"stress_n{n_rows}"
    source = build_large_valid_source(tmp_path / "src", n_rows=n_rows)
    target = tmp_path / "target"

    ctx = MigrationContext(source_path=source, target_path=target)
    raised: MigratorError | None = None
    try:
        run_pipeline(ctx, MIGRATE_PIPELINE)
    except MigratorError as exc:
        raised = exc

    assert raised is None, (
        f"[{cid}] pipeline raised MigratorError at stage={getattr(raised, 'stage', '?')!r}: "
        f"{getattr(raised, 'summary', str(raised))}"
    )
    _assert_imported_count(ctx, n_rows, cid=cid)
    _assert_parity_clean(ctx, cid=cid)
