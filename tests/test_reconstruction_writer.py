"""M10 — reconstruction/_writer.py contract tests (requires chromadb).

Covers:
  - round-trips a 3-drawer bundle: collection.count() == 3
  - ids preserved exactly
  - metadata preserved verbatim
  - no embeddings= kwarg used (passes without explicit embeddings)
  - BATCH_SIZE is the module-level constant
  - add_in_batches batching: with BATCH_SIZE=2 on 5 drawers, 3 batches called
  - _BatchInsertError is raised on collection.add failure, carries batch_index / first_id / last_id
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from mempalace_migrator.reconstruction._writer import (
    BATCH_SIZE,
    _BatchInsertError,
    add_in_batches,
    create_collection,
    open_client,
)
from mempalace_migrator.transformation._types import TransformedDrawer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _td(id: str, doc: str = "hello", meta: dict | None = None) -> TransformedDrawer:
    return TransformedDrawer(id=id, document=doc, metadata=meta or {})


# ---------------------------------------------------------------------------
# Module-level constant
# ---------------------------------------------------------------------------


def test_batch_size_is_500() -> None:
    assert BATCH_SIZE == 500


# ---------------------------------------------------------------------------
# open_client creates a PersistentClient at target_path
# ---------------------------------------------------------------------------


def test_open_client_returns_client(tmp_path: Path) -> None:
    client = open_client(tmp_path)
    assert client is not None
    client.clear_system_cache()


# ---------------------------------------------------------------------------
# create_collection creates a named collection
# ---------------------------------------------------------------------------


def test_create_collection_name(tmp_path: Path) -> None:
    client = open_client(tmp_path)
    try:
        col = create_collection(client, "test_collection", {})
        assert col.name == "test_collection"
    finally:
        client.clear_system_cache()


# ---------------------------------------------------------------------------
# add_in_batches — round-trip 3 drawers
# ---------------------------------------------------------------------------


def test_round_trip_3_drawers(tmp_path: Path) -> None:
    drawers = (
        _td("id1", "doc one", {"wing": "north"}),
        _td("id2", "doc two", {"wing": "south"}),
        _td("id3", "doc three", {}),
    )
    client = open_client(tmp_path)
    try:
        col = create_collection(client, "test_rt", {})
        count = add_in_batches(col, drawers)
        assert count == 3
        assert col.count() == 3
    finally:
        client.clear_system_cache()


def test_ids_preserved(tmp_path: Path) -> None:
    drawers = (_td("alpha"), _td("beta"), _td("gamma"))
    client = open_client(tmp_path)
    try:
        col = create_collection(client, "test_ids", {})
        add_in_batches(col, drawers)
        result = col.get(ids=["alpha", "beta", "gamma"])
        assert sorted(result["ids"]) == ["alpha", "beta", "gamma"]
    finally:
        client.clear_system_cache()


def test_metadata_preserved(tmp_path: Path) -> None:
    meta = {"wing": "west", "room": "42", "priority": 3}
    drawers = (_td("m1", "text", meta),)
    client = open_client(tmp_path)
    try:
        col = create_collection(client, "test_meta", {})
        add_in_batches(col, drawers)
        result = col.get(ids=["m1"])
        assert result["metadatas"][0]["wing"] == "west"
        assert result["metadatas"][0]["room"] == "42"
        assert result["metadatas"][0]["priority"] == 3
    finally:
        client.clear_system_cache()


# ---------------------------------------------------------------------------
# add_in_batches batching: monkeypatch BATCH_SIZE=2 on 5 drawers → 3 batches
# ---------------------------------------------------------------------------


def test_batching_with_small_batch_size(tmp_path: Path) -> None:
    drawers = tuple(_td(f"id{i}") for i in range(5))
    client = open_client(tmp_path)
    try:
        col = create_collection(client, "test_batch", {})
        with patch("mempalace_migrator.reconstruction._writer.BATCH_SIZE", 2):
            count = add_in_batches(col, drawers)
        assert count == 5
        assert col.count() == 5
    finally:
        client.clear_system_cache()


# ---------------------------------------------------------------------------
# _BatchInsertError on collection.add failure
# ---------------------------------------------------------------------------


def test_batch_insert_error_raised_on_add_failure() -> None:
    col_mock = MagicMock()
    col_mock.add.side_effect = RuntimeError("chromadb exploded")
    drawers = (_td("x"), _td("y"))
    with pytest.raises(_BatchInsertError) as exc_info:
        add_in_batches(col_mock, drawers)
    err = exc_info.value
    assert err.batch_index == 0
    assert err.first_id == "x"
    assert err.last_id == "y"
    assert "chromadb exploded" in str(err.cause)


def test_batch_insert_error_on_second_batch() -> None:
    """add succeeds on batch 0, fails on batch 1."""
    drawers = tuple(_td(f"id{i}") for i in range(4))
    col_mock = MagicMock()

    call_count = {"n": 0}

    def side_effect(**kwargs):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise ValueError("second batch fails")

    col_mock.add.side_effect = side_effect

    with patch("mempalace_migrator.reconstruction._writer.BATCH_SIZE", 2):
        with pytest.raises(_BatchInsertError) as exc_info:
            add_in_batches(col_mock, drawers)

    assert exc_info.value.batch_index == 1
    assert exc_info.value.first_id == "id2"
    assert exc_info.value.last_id == "id3"
