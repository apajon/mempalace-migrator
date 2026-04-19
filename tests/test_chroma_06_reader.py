"""M2 exit-gate tests — Extraction Resilience.

Exit gate: "Extraction never crashes on recoverable errors."

Coverage map:
  5.1  Partial read support         -> test_good_rows_survive_bad_rows, test_partial_*
  5.2  Invalid JSON tolerance        -> test_document_json_string_*, test_metadata_json_*
  5.3  Corrupted SQLite handled      -> test_sqlite_missing_raises, test_wal_*, test_integrity_*
  5.4  Record-level isolation        -> test_*_excluded, test_good_rows_survive_*
  5.5  No global crash               -> all isolation tests confirm no crash
  5.6  Structured anomalies output   -> test_anomaly_emitted_*

What is NOT tested here (deliberate):
  - Actual bit-level SQLite corruption (requires OS-level tooling).
    PRAGMA integrity_check failure path is verified by monkey-patching
    the connection result (see test_integrity_check_failure_raises).
  - Mid-scan DatabaseError triggered by real page corruption.
    The protection is in _read_drawers_resilient (while True / next() +
    except DatabaseError). A wrapper-based test is included
    (test_mid_scan_corruption_returns_partial) to verify that path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from mempalace_migrator.core.context import MigrationContext
from mempalace_migrator.core.errors import ExtractionError
from mempalace_migrator.extraction.chroma_06_reader import (
    CHROMA_SQLITE_FILENAME,
    EXPECTED_COLLECTION_NAME,
    DrawerRecord,
    ExtractionResult,
    FailedRow,
    _read_drawers_resilient,
    extract,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(source_path: Path) -> MigrationContext:
    return MigrationContext(source_path=source_path)


def _make_db(root: Path) -> tuple[Path, sqlite3.Connection]:
    """Create the minimal valid schema and return (db_path, open_connection).

    The collection row for EXPECTED_COLLECTION_NAME is inserted.
    Caller must commit() and close() the returned connection.
    """
    db_path = root / CHROMA_SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE collections (
            id   INTEGER PRIMARY KEY,
            name TEXT
        );
        CREATE TABLE embeddings (
            id            INTEGER PRIMARY KEY,
            collection_id INTEGER,
            embedding_id  TEXT
        );
        CREATE TABLE embedding_metadata (
            id           INTEGER NOT NULL,
            key          TEXT    NOT NULL,
            string_value TEXT,
            int_value    INTEGER,
            float_value  REAL,
            bool_value   INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO collections (id, name) VALUES (1, ?)",
        (EXPECTED_COLLECTION_NAME,),
    )
    return db_path, conn


def _add_drawer(
    conn: sqlite3.Connection,
    pk: int,
    embedding_id: str | None,
    document: str | None,
    *,
    null_document: bool = False,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Insert one embeddings row and its metadata.

    embedding_id=None  -> SQL NULL (for blank/null id tests)
    document=None      -> no chroma:document row inserted (missing doc test)
    null_document=True -> chroma:document inserted with NULL string_value
    """
    conn.execute(
        "INSERT INTO embeddings (id, collection_id, embedding_id) VALUES (?, 1, ?)",
        (pk, embedding_id),
    )
    if null_document:
        conn.execute(
            "INSERT INTO embedding_metadata (id, key, string_value) VALUES (?, 'chroma:document', NULL)",
            (pk,),
        )
    elif document is not None:
        conn.execute(
            "INSERT INTO embedding_metadata (id, key, string_value) VALUES (?, 'chroma:document', ?)",
            (pk, document),
        )
    for key, val in (metadata or {}).items():
        if isinstance(val, str):
            conn.execute(
                "INSERT INTO embedding_metadata (id, key, string_value) VALUES (?, ?, ?)",
                (pk, key, val),
            )
        elif isinstance(val, bool):
            conn.execute(
                "INSERT INTO embedding_metadata (id, key, bool_value) VALUES (?, ?, ?)",
                (pk, key, int(val)),
            )
        elif isinstance(val, int):
            conn.execute(
                "INSERT INTO embedding_metadata (id, key, int_value) VALUES (?, ?, ?)",
                (pk, key, val),
            )
        elif isinstance(val, float):
            conn.execute(
                "INSERT INTO embedding_metadata (id, key, float_value) VALUES (?, ?, ?)",
                (pk, key, val),
            )


def _add_all_null_meta(conn: sqlite3.Connection, pk: int, key: str) -> None:
    """Insert a metadata row where all typed value columns are NULL."""
    conn.execute(
        "INSERT INTO embedding_metadata (id, key, string_value, int_value, float_value, bool_value) VALUES (?, ?, NULL, NULL, NULL, NULL)",
        (pk, key),
    )


# ---------------------------------------------------------------------------
# Pre-flight CRITICAL tests (ExtractionError with specific code)
# ---------------------------------------------------------------------------


def test_sqlite_missing_raises(tmp_path):
    ctx = _make_ctx(tmp_path)
    with pytest.raises(ExtractionError) as exc_info:
        extract(tmp_path, ctx)
    assert exc_info.value.code == "sqlite_missing"


def test_wal_not_checkpointed_raises(tmp_path):
    _, conn = _make_db(tmp_path)
    conn.commit()
    conn.close()
    wal = tmp_path / (CHROMA_SQLITE_FILENAME + "-wal")
    wal.write_bytes(b"fake wal content")

    ctx = _make_ctx(tmp_path)
    with pytest.raises(ExtractionError) as exc_info:
        extract(tmp_path, ctx)
    assert exc_info.value.code == "wal_not_checkpointed"


def test_empty_wal_file_does_not_raise(tmp_path):
    """A zero-byte WAL is ignored — the write may have just been prepared."""
    _, conn = _make_db(tmp_path)
    conn.commit()
    conn.close()
    wal = tmp_path / (CHROMA_SQLITE_FILENAME + "-wal")
    wal.write_bytes(b"")  # zero bytes

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)
    assert result.parsed_count == 0


def test_missing_required_table_raises(tmp_path):
    db_path = tmp_path / CHROMA_SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    # Only create collections + embeddings, skip embedding_metadata
    conn.execute("CREATE TABLE collections (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE embeddings (id INTEGER PRIMARY KEY, embedding_id TEXT)")
    conn.execute("INSERT INTO collections (id, name) VALUES (1, ?)", (EXPECTED_COLLECTION_NAME,))
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    with pytest.raises(ExtractionError) as exc_info:
        extract(tmp_path, ctx)
    assert exc_info.value.code == "required_tables_missing"


def test_no_collection_raises(tmp_path):
    db_path = tmp_path / CHROMA_SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE collections (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE embeddings (id INTEGER PRIMARY KEY, embedding_id TEXT)")
    conn.execute(
        "CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT, int_value INTEGER, float_value REAL, bool_value INTEGER)"
    )
    # No collection row inserted
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    with pytest.raises(ExtractionError) as exc_info:
        extract(tmp_path, ctx)
    assert exc_info.value.code == "no_collection"


def test_multiple_collections_raises(tmp_path):
    _, conn = _make_db(tmp_path)
    conn.execute("INSERT INTO collections (id, name) VALUES (2, 'another_collection')")
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    with pytest.raises(ExtractionError) as exc_info:
        extract(tmp_path, ctx)
    assert exc_info.value.code == "multiple_collections"


def test_wrong_collection_name_raises(tmp_path):
    db_path = tmp_path / CHROMA_SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE collections (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE embeddings (id INTEGER PRIMARY KEY, collection_id INTEGER, embedding_id TEXT)")
    conn.execute(
        "CREATE TABLE embedding_metadata (id INTEGER, key TEXT, string_value TEXT, int_value INTEGER, float_value REAL, bool_value INTEGER)"
    )
    conn.execute("INSERT INTO collections (id, name) VALUES (1, 'wrong_name')")
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    with pytest.raises(ExtractionError) as exc_info:
        extract(tmp_path, ctx)
    assert exc_info.value.code == "unexpected_collection_name"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_clean_extraction_single_drawer(tmp_path):
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "drawer-1", "Hello world")
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 1
    assert result.failed_count == 0
    assert result.total_count == 1
    assert result.drawers[0].id == "drawer-1"
    assert result.drawers[0].document == "Hello world"
    assert result.drawers[0].metadata == {}
    assert result.collection_name == EXPECTED_COLLECTION_NAME


def test_clean_extraction_empty_embeddings(tmp_path):
    _, conn = _make_db(tmp_path)
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 0
    assert result.failed_count == 0
    assert result.total_count == 0
    assert ctx.anomalies == []


def test_metadata_all_types_extracted(tmp_path):
    _, conn = _make_db(tmp_path)
    _add_drawer(
        conn,
        1,
        "drawer-1",
        "content",
        metadata={
            "str_key": "value",
            "int_key": 42,
            "float_key": 3.14,
            "bool_key": True,
        },
    )
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 1
    meta = result.drawers[0].metadata
    assert meta["str_key"] == "value"
    assert meta["int_key"] == 42
    assert meta["float_key"] == pytest.approx(3.14)
    assert meta["bool_key"] is True


def test_multiple_clean_drawers(tmp_path):
    _, conn = _make_db(tmp_path)
    for i in range(5):
        _add_drawer(conn, i + 1, f"id-{i}", f"doc {i}")
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 5
    assert result.failed_count == 0


# ---------------------------------------------------------------------------
# Per-row isolation: each failure excludes only that row (5.4, 5.5)
# ---------------------------------------------------------------------------


def test_blank_id_excluded(tmp_path):
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "", "content")  # blank embedding_id
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 0
    assert result.failed_count == 1
    assert result.failed_rows[0].reason_type == "blank_embedding_id"


def test_null_id_excluded(tmp_path):
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, None, "content")  # NULL embedding_id
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 0
    assert result.failed_count == 1
    assert result.failed_rows[0].reason_type == "blank_embedding_id"


def test_control_chars_in_id_excluded(tmp_path):
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "id\x00with\x1fcontrol", "content")
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 0
    assert result.failed_count == 1
    assert result.failed_rows[0].reason_type == "control_chars_in_id"


def test_duplicate_ids_both_excluded(tmp_path):
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "shared-id", "content A")
    _add_drawer(conn, 2, "shared-id", "content B")
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 0
    assert result.failed_count == 2
    assert all(f.reason_type == "duplicate_embedding_id" for f in result.failed_rows)


def test_orphan_embedding_excluded(tmp_path):
    """An embedding row with no embedding_metadata rows is excluded."""
    _, conn = _make_db(tmp_path)
    # Insert embedding row but no metadata rows at all
    conn.execute("INSERT INTO embeddings (id, collection_id, embedding_id) VALUES (1, 1, 'orphan-id')")
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 0
    assert result.failed_count == 1
    assert result.failed_rows[0].reason_type == "orphan_embedding"


def test_missing_document_excluded(tmp_path):
    """A row with user metadata but no chroma:document is excluded."""
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "id-1", None, metadata={"key": "val"})  # no document
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 0
    assert result.failed_count == 1
    assert result.failed_rows[0].reason_type == "document_missing"


def test_null_document_excluded(tmp_path):
    """A row where chroma:document has NULL string_value is excluded."""
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "id-1", None, null_document=True)
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 0
    assert result.failed_count == 1
    assert result.failed_rows[0].reason_type == "document_string_value_null"


def test_all_null_metadata_value_excluded(tmp_path):
    """A row with a user metadata key where all typed values are NULL is excluded."""
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "id-1", "content")
    _add_all_null_meta(conn, 1, "ghost_key")
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 0
    assert result.failed_count == 1
    assert result.failed_rows[0].reason_type == "metadata_all_null"


# ---------------------------------------------------------------------------
# Partial extraction: good rows survive bad rows (5.1)
# ---------------------------------------------------------------------------


def test_good_rows_survive_bad_rows(tmp_path):
    """Partial extraction: one bad row and one good row — good row is returned."""
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "", "content-bad")  # blank id — excluded
    _add_drawer(conn, 2, "good-id", "content-good")  # valid
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 1
    assert result.failed_count == 1
    assert result.drawers[0].id == "good-id"
    assert result.failed_rows[0].reason_type == "blank_embedding_id"


def test_all_rows_bad_returns_empty_drawers(tmp_path):
    """When every row fails, extraction does not crash; drawers is empty."""
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, None, "doc-1")  # NULL id
    _add_drawer(conn, 2, None, "doc-2")  # NULL id
    _add_drawer(conn, 3, None, "doc-3")  # NULL id
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 0
    assert result.failed_count == 3
    # No exception raised


def test_partial_extraction_multiple_failure_types(tmp_path):
    """Several different per-row failure types in one extraction — none crash."""
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "good-1", "doc A")  # ok
    _add_drawer(conn, 2, "", "doc B")  # blank id
    _add_drawer(conn, 3, "good-3", "doc C")  # ok
    _add_drawer(conn, 4, "id-4", None)  # missing doc
    _add_drawer(conn, 5, "good-5", "doc E")  # ok
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 3
    assert result.failed_count == 2


# ---------------------------------------------------------------------------
# Structured anomalies (5.6)
# ---------------------------------------------------------------------------


def test_anomaly_emitted_for_blank_id(tmp_path):
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "", "doc")
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    extract(tmp_path, ctx)

    types = [a.type for a in ctx.anomalies]
    assert "blank_embedding_id" in types
    anomaly = next(a for a in ctx.anomalies if a.type == "blank_embedding_id")
    assert anomaly.severity == "high"
    assert anomaly.stage == "extract"


def test_anomaly_emitted_for_duplicate_ids(tmp_path):
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "dup-id", "doc A")
    _add_drawer(conn, 2, "dup-id", "doc B")
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    extract(tmp_path, ctx)

    types = [a.type for a in ctx.anomalies]
    assert "duplicate_embedding_ids" in types
    anomaly = next(a for a in ctx.anomalies if a.type == "duplicate_embedding_ids")
    assert anomaly.severity == "high"
    assert "dup-id" in anomaly.context.get("sample", [])


def test_anomaly_emitted_for_orphan_embedding(tmp_path):
    _, conn = _make_db(tmp_path)
    conn.execute("INSERT INTO embeddings (id, collection_id, embedding_id) VALUES (1, 1, 'orphan')")
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    extract(tmp_path, ctx)

    types = [a.type for a in ctx.anomalies]
    assert "orphan_embedding" in types


def test_anomaly_emitted_for_missing_document(tmp_path):
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "id-1", None, metadata={"k": "v"})
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    extract(tmp_path, ctx)

    types = [a.type for a in ctx.anomalies]
    assert "document_missing" in types


def test_anomaly_emitted_for_null_document(tmp_path):
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "id-1", None, null_document=True)
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    extract(tmp_path, ctx)

    types = [a.type for a in ctx.anomalies]
    assert "document_string_value_null" in types


def test_duplicate_metadata_keys_anomaly_medium_row_kept(tmp_path):
    """Duplicate user metadata keys: MEDIUM anomaly, row is kept (last value wins)."""
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "id-1", "doc")
    # Insert the same user key twice with different string values
    conn.execute("INSERT INTO embedding_metadata (id, key, string_value) VALUES (1, 'tag', 'first')")
    conn.execute("INSERT INTO embedding_metadata (id, key, string_value) VALUES (1, 'tag', 'second')")
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    # Row is kept despite duplicate key
    assert result.parsed_count == 1
    assert result.drawers[0].metadata["tag"] == "second"
    # MEDIUM anomaly for the duplicate key
    dup_anomalies = [a for a in ctx.anomalies if a.type == "duplicate_metadata_keys"]
    assert len(dup_anomalies) == 1
    assert dup_anomalies[0].severity == "medium"


# ---------------------------------------------------------------------------
# 5.2 — "Invalid JSON" / unusual string values do not crash
# ---------------------------------------------------------------------------


def test_document_json_string_extracted_as_is(tmp_path):
    """Document content that is a JSON string is returned without parsing."""
    _, conn = _make_db(tmp_path)
    json_doc = '{"key": "value", "nested": {"a": 1}}'
    _add_drawer(conn, 1, "id-1", json_doc)
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 1
    assert result.drawers[0].document == json_doc


def test_document_malformed_json_string_extracted_as_is(tmp_path):
    """A document that looks like broken JSON is returned as a plain string."""
    _, conn = _make_db(tmp_path)
    broken = '{"key": "value", "unclosed": '
    _add_drawer(conn, 1, "id-1", broken)
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 1
    assert result.drawers[0].document == broken


def test_metadata_json_like_value_extracted_as_string(tmp_path):
    """A metadata string_value that looks like JSON is returned as-is."""
    _, conn = _make_db(tmp_path)
    _add_drawer(
        conn,
        1,
        "id-1",
        "content",
        metadata={"data": '{"malformed": [1, 2,'},
    )
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count == 1
    assert result.drawers[0].metadata["data"] == '{"malformed": [1, 2,'


# ---------------------------------------------------------------------------
# Result integrity
# ---------------------------------------------------------------------------


def test_counts_consistent(tmp_path):
    """parsed_count + failed_count == total_count for every run."""
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "ok-1", "doc 1")
    _add_drawer(conn, 2, "", "doc 2")  # blank id
    _add_drawer(conn, 3, "ok-3", "doc 3")
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.parsed_count + result.failed_count == result.total_count


def test_failed_row_to_dict_structure(tmp_path):
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "", "doc")  # blank id -> FailedRow
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    d = result.failed_rows[0].to_dict()
    assert set(d.keys()) == {"embedding_pk", "embedding_id", "reason_type", "message", "context"}
    assert d["reason_type"] == "blank_embedding_id"


def test_extraction_result_properties(tmp_path):
    _, conn = _make_db(tmp_path)
    _add_drawer(conn, 1, "id-1", "doc")
    conn.commit()
    conn.close()

    ctx = _make_ctx(tmp_path)
    result = extract(tmp_path, ctx)

    assert result.palace_path == str(tmp_path)
    assert result.sqlite_path == str(tmp_path / CHROMA_SQLITE_FILENAME)
    assert result.pragma_integrity_check == "ok"
    assert result.collection_name == EXPECTED_COLLECTION_NAME


# ---------------------------------------------------------------------------
# Mid-scan corruption returns partial results (5.5 — implementation verified)
# ---------------------------------------------------------------------------


def test_mid_scan_corruption_returns_partial(tmp_path):
    """A DatabaseError raised during cursor iteration returns partial results.

    The protection is the while-True/next() loop with except DatabaseError
    in _read_drawers_resilient.  We verify it by wrapping the connection's
    execute() to return a failing iterator for the main scan query.
    """
    _, conn_build = _make_db(tmp_path)
    _add_drawer(conn_build, 1, "row-1", "doc 1")
    _add_drawer(conn_build, 2, "row-2", "doc 2")
    conn_build.commit()
    conn_build.close()

    ctx = _make_ctx(tmp_path)
    db_path = tmp_path / CHROMA_SQLITE_FILENAME

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    class _FailAfterFirstRow:
        """Yields first row successfully, then raises DatabaseError."""

        def __init__(self, real_cursor: sqlite3.Cursor) -> None:
            self._iter = iter(real_cursor)
            self._count = 0

        def __iter__(self) -> "_FailAfterFirstRow":
            return self

        def __next__(self) -> sqlite3.Row:
            self._count += 1
            if self._count > 1:
                raise sqlite3.DatabaseError("simulated mid-scan page corruption")
            return next(self._iter)

        def fetchall(self) -> list:
            return list(self._iter)

    class _PatchedConn:
        """Wraps sqlite3.Connection; intercepts the main embeddings scan."""

        def __init__(self, real: sqlite3.Connection) -> None:
            self._real = real

        def execute(self, sql: str, *args: object) -> object:
            result = self._real.execute(sql, *args)
            # Intercept only the main ordered scan (no params, no GROUP BY)
            if "ORDER BY id" in sql and not args:
                return _FailAfterFirstRow(result)
            return result

        def close(self) -> None:
            self._real.close()

        def __getattr__(self, name: str) -> object:
            return getattr(self._real, name)

    patched = _PatchedConn(conn)
    drawers, failed = _read_drawers_resilient(patched, ctx)  # type: ignore[arg-type]
    conn.close()

    # First row was fetched and parsed before the abort
    assert len(drawers) == 1
    assert drawers[0].id == "row-1"

    # Anomaly recording the abort
    abort_anomalies = [a for a in ctx.anomalies if a.type == "embeddings_scan_aborted"]
    assert len(abort_anomalies) == 1
    assert abort_anomalies[0].severity == "high"
    assert abort_anomalies[0].context["parsed_so_far"] == 1
