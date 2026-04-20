"""Frozen result dataclass for the reconstruction stage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReconstructionResult:
    """Stored on ``ctx.reconstruction_result`` on a successful reconstruct run.

    ``imported_count`` equals ``len(ctx.transformed_data.drawers)``; there is
    no partial-success state — any write failure triggers a full rollback.
    ``chromadb_version`` is captured via ``chromadb.__version__`` at write time.
    """

    target_path: Path
    collection_name: str
    imported_count: int
    batch_size: int
    chromadb_version: str
    target_manifest_path: Path
