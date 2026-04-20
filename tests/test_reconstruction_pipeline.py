"""M10 — step_reconstruct pipeline contract tests.

Covers:
  (a) target_path=None → stage skipped, no anomaly, no write, no exception
  (b) transformed_data=None → RECONSTRUCTION_INPUT_MISSING/CRITICAL + ReconstructionError raised
  (c) transformed_data.drawers empty → RECONSTRUCTION_INPUT_MISSING/CRITICAL + raise
  (d) success path → ctx.reconstruction_result populated, manifest exists,
      stage_skip_reasons does NOT contain 'reconstruct'
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mempalace_migrator.core.context import AnomalyType, MigrationContext, Severity
from mempalace_migrator.core.errors import ReconstructionError
from mempalace_migrator.core.pipeline import step_reconstruct
from mempalace_migrator.transformation._types import (
    LengthProfile,
    TransformedBundle,
    TransformedDrawer,
    TransformedSummary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _td(id: str) -> TransformedDrawer:
    return TransformedDrawer(id=id, document=f"doc {id}", metadata={})


def _bundle(n: int) -> TransformedBundle:
    drawers = tuple(_td(f"id{i}") for i in range(n))
    summary = TransformedSummary(
        drawer_count=n,
        dropped_count=0,
        coerced_count=0,
        sample_ids=(),
        metadata_keys=(),
        wing_room_counts=(),
        length_profile=LengthProfile(min=3, max=3, mean=3.0, p50=3, p95=3),
    )
    return TransformedBundle(collection_name="memory_palace", collection_metadata={}, drawers=drawers, summary=summary)


# ---------------------------------------------------------------------------
# (a) target_path=None → skipped
# ---------------------------------------------------------------------------


def test_no_target_path_is_skipped():
    ctx = MigrationContext(source_path=Path("/fake/source"))
    assert ctx.target_path is None
    step_reconstruct(ctx)  # must not raise
    assert "reconstruct" in ctx.stage_skip_reasons
    assert ctx.stage_skip_reasons["reconstruct"] == "no_target_path"


def test_no_target_path_no_anomaly():
    ctx = MigrationContext(source_path=Path("/fake/source"))
    step_reconstruct(ctx)
    reconstruct_anomalies = [a for a in ctx.anomalies if a.stage == "reconstruct"]
    assert reconstruct_anomalies == []


def test_no_target_path_no_write(tmp_path: Path):
    # Even if transformed_data is present, no write occurs when target_path is None.
    ctx = MigrationContext(source_path=Path("/fake/source"))
    ctx.transformed_data = _bundle(3)
    initial_children = list(tmp_path.iterdir())
    step_reconstruct(ctx)
    assert list(tmp_path.iterdir()) == initial_children


# ---------------------------------------------------------------------------
# (b) transformed_data=None → RECONSTRUCTION_INPUT_MISSING/CRITICAL + raise
# ---------------------------------------------------------------------------


def test_no_transformed_data_raises(tmp_path: Path):
    ctx = MigrationContext(source_path=Path("/fake/source"), target_path=tmp_path / "target")
    with pytest.raises(ReconstructionError) as exc_info:
        step_reconstruct(ctx)
    assert exc_info.value.code == "reconstruction_input_missing"


def test_no_transformed_data_emits_critical(tmp_path: Path):
    ctx = MigrationContext(source_path=Path("/fake/source"), target_path=tmp_path / "target")
    with pytest.raises(ReconstructionError):
        step_reconstruct(ctx)
    critical = [
        a
        for a in ctx.anomalies
        if a.type == AnomalyType.RECONSTRUCTION_INPUT_MISSING and a.severity == Severity.CRITICAL
    ]
    assert len(critical) >= 1


# ---------------------------------------------------------------------------
# (c) empty drawers tuple → RECONSTRUCTION_INPUT_MISSING/CRITICAL + raise
# ---------------------------------------------------------------------------


def test_empty_drawers_raises(tmp_path: Path):
    ctx = MigrationContext(source_path=Path("/fake/source"), target_path=tmp_path / "target")
    ctx.transformed_data = _bundle(0)
    with pytest.raises(ReconstructionError) as exc_info:
        step_reconstruct(ctx)
    assert exc_info.value.code == "reconstruction_input_missing"


def test_empty_drawers_emits_critical(tmp_path: Path):
    ctx = MigrationContext(source_path=Path("/fake/source"), target_path=tmp_path / "target")
    ctx.transformed_data = _bundle(0)
    with pytest.raises(ReconstructionError):
        step_reconstruct(ctx)
    critical = [
        a
        for a in ctx.anomalies
        if a.type == AnomalyType.RECONSTRUCTION_INPUT_MISSING and a.severity == Severity.CRITICAL
    ]
    assert len(critical) >= 1


# ---------------------------------------------------------------------------
# (d) success path → reconstruction_result populated
# ---------------------------------------------------------------------------


def test_success_path_result_populated(tmp_path: Path):
    ctx = MigrationContext(source_path=Path("/fake/source"), target_path=tmp_path / "target")
    ctx.transformed_data = _bundle(2)
    step_reconstruct(ctx)
    assert ctx.reconstruction_result is not None
    assert ctx.reconstruction_result.imported_count == 2


def test_success_path_not_in_skip_reasons(tmp_path: Path):
    ctx = MigrationContext(source_path=Path("/fake/source"), target_path=tmp_path / "target")
    ctx.transformed_data = _bundle(2)
    step_reconstruct(ctx)
    assert "reconstruct" not in ctx.stage_skip_reasons


def test_success_path_manifest_exists(tmp_path: Path):
    from mempalace_migrator.reconstruction._manifest import TARGET_MANIFEST_FILENAME  # noqa

    ctx = MigrationContext(source_path=Path("/fake/source"), target_path=tmp_path / "target")
    ctx.transformed_data = _bundle(2)
    step_reconstruct(ctx)
    assert (tmp_path / "target" / TARGET_MANIFEST_FILENAME).exists()


def test_success_path_no_reconstruct_anomaly(tmp_path: Path):
    ctx = MigrationContext(source_path=Path("/fake/source"), target_path=tmp_path / "target")
    ctx.transformed_data = _bundle(2)
    step_reconstruct(ctx)
    bad = [a for a in ctx.anomalies if a.stage == "reconstruct" and a.severity in (Severity.CRITICAL, Severity.HIGH)]
    assert bad == []
