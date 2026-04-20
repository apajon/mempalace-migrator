"""ChromaDB write operations for the reconstruction stage.

This is the **only** module in the entire package that imports chromadb.
Importing chromadb elsewhere is a regression caught by
``tests/test_reconstruction_purity.py``.

All functions here are called by ``reconstructor.py``. They do not touch
``MigrationContext`` directly — they accept plain Python values so the
orchestrator can mock them cleanly in unit tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings

BATCH_SIZE = 500


def open_client(target_path: Path) -> chromadb.PersistentClient:
    """Open a new PersistentClient at *target_path*.

    ``anonymized_telemetry=False`` makes the writer hermetic (no network
    calls). ``allow_reset=False`` prevents accidental database deletion.
    """
    return chromadb.PersistentClient(
        path=str(target_path),
        settings=Settings(anonymized_telemetry=False, allow_reset=False),
    )


def create_collection(
    client: chromadb.PersistentClient,
    name: str,
    metadata: dict[str, Any],
) -> Any:
    """Create (or fail if exists) the named collection.

    Passes ``metadata`` as collection-level metadata. On chromadb 1.x the
    ``get_or_create_collection`` exists but we intentionally use
    ``create_collection`` so that any stale leftover collection is a hard
    error rather than silent state corruption.
    """
    return client.create_collection(name=name, metadata=metadata or None)


def add_in_batches(
    collection: Any,
    drawers: tuple,  # tuple[TransformedDrawer, ...]
) -> int:
    """Insert *drawers* via ``collection.add()`` in batches of ``BATCH_SIZE``.

    Returns the total number of inserted records on success.
    Raises ``_BatchInsertError`` on any ``collection.add`` exception,
    carrying the failing batch index and chromadb exception for the
    orchestrator to surface.

    No ``embeddings=`` kwarg: chromadb 1.x re-derives embeddings from
    documents on insert (per ROADMAP non-goal on embedding re-computation).
    """
    items = list(drawers)
    total = 0
    for batch_index in range(0, len(items), BATCH_SIZE):
        batch = items[batch_index : batch_index + BATCH_SIZE]
        ids = [d.id for d in batch]
        documents = [d.document for d in batch]
        # chromadb 1.x rejects empty-dict metadata; coerce {} → None
        metadatas = [d.metadata if d.metadata else None for d in batch]
        try:
            collection.add(ids=ids, documents=documents, metadatas=metadatas)
        except Exception as exc:
            raise _BatchInsertError(
                batch_index=batch_index // BATCH_SIZE,
                first_id=ids[0] if ids else "",
                last_id=ids[-1] if ids else "",
                cause=exc,
            ) from exc
        total += len(batch)
    return total


class _BatchInsertError(Exception):
    """Internal: raised by add_in_batches on any collection.add failure.

    Carries structured context for the orchestrator to build an anomaly.
    Never escapes reconstruction/; caught and converted to ReconstructionError
    in reconstructor.py.
    """

    def __init__(self, *, batch_index: int, first_id: str, last_id: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.batch_index = batch_index
        self.first_id = first_id
        self.last_id = last_id
        self.cause = cause
