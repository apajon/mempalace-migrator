"""Resilient extractor for ChromaDB 0.6.x palaces.

Failure model:

  CRITICAL  -> raise ExtractionError, pipeline aborts.
               Used for conditions that make the whole extraction
               meaningless: missing DB, integrity_check failure,
               WAL not checkpointed, missing required tables, no
               collection / multiple collections / wrong name.

  HIGH      -> the row cannot be reconstructed safely. The row is
               EXCLUDED from the parsed set, recorded in failed_rows,
               and an Anomaly is emitted. Extraction continues.

  MEDIUM    -> the row is included but flagged. Anomaly emitted.

  LOW       -> informational. Anomaly emitted, no row impact.

Read-only access is enforced via SQLite URI mode=ro. The source is never
modified, even on transient errors.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mempalace_migrator.core.context import MigrationContext
from mempalace_migrator.core.errors import ExtractionError

CHROMA_SQLITE_FILENAME = "chroma.sqlite3"
WAL_SUFFIX = "-wal"
EXPECTED_COLLECTION_NAME = "mempalace_drawers"

ID_FORBIDDEN_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

REQUIRED_TABLES = ("collections", "embeddings", "embedding_metadata")


@dataclass(frozen=True)
class DrawerRecord:
    id: str
    document: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class FailedRow:
    """A row that could not be promoted to a DrawerRecord."""

    embedding_pk: int | None
    embedding_id: str | None
    reason_type: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "embedding_pk": self.embedding_pk,
            "embedding_id": self.embedding_id,
            "reason_type": self.reason_type,
            "message": self.message,
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class ExtractionResult:
    palace_path: str
    sqlite_path: str
    drawers: tuple[DrawerRecord, ...]
    failed_rows: tuple[FailedRow, ...]
    sqlite_embedding_row_count: int
    pragma_integrity_check: str
    collection_name: str

    @property
    def parsed_count(self) -> int:
        return len(self.drawers)

    @property
    def failed_count(self) -> int:
        return len(self.failed_rows)

    @property
    def total_count(self) -> int:
        return self.sqlite_embedding_row_count


# --- Public entry ----------------------------------------------------------


def extract(palace_path: Path, ctx: MigrationContext) -> ExtractionResult:
    """Read a chroma_0_6 palace, tolerating per-row corruption.

    `ctx` is mutated to record structured anomalies. Pre-flight failures
    raise ExtractionError (CRITICAL). Per-row failures are collected.
    """
    palace_path = Path(palace_path)
    db_path = palace_path / CHROMA_SQLITE_FILENAME

    if not db_path.is_file():
        raise ExtractionError(
            stage="extract",
            code="sqlite_missing",
            summary=f"{CHROMA_SQLITE_FILENAME} missing in {palace_path}",
        )

    _check_wal_state(palace_path, db_path)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        raise ExtractionError(
            stage="extract",
            code="sqlite_open_failed",
            summary=f"failed to open {db_path}: {exc!r}",
        ) from exc

    try:
        _pragma_integrity_check(conn, db_path)
        _verify_required_tables(conn)
        collection_name = _verify_single_expected_collection(conn)
        embedding_row_count = _embedding_row_count(conn)
        drawers, failed = _read_drawers_resilient(conn, ctx)
    finally:
        conn.close()

    if (len(drawers) + len(failed)) != embedding_row_count:
        # Should not happen: every embeddings row is iterated exactly once.
        ctx.add_anomaly(
            type="extraction_arithmetic_mismatch",
            severity="high",
            stage="extract",
            message="parsed + failed != total sqlite rows",
            context={
                "parsed": len(drawers),
                "failed": len(failed),
                "total": embedding_row_count,
            },
        )

    return ExtractionResult(
        palace_path=str(palace_path),
        sqlite_path=str(db_path),
        drawers=tuple(drawers),
        failed_rows=tuple(failed),
        sqlite_embedding_row_count=embedding_row_count,
        pragma_integrity_check="ok",
        collection_name=collection_name,
    )


# --- Pre-flight (CRITICAL) -------------------------------------------------


def _check_wal_state(palace_path: Path, db_path: Path) -> None:
    wal = palace_path / (db_path.name + WAL_SUFFIX)
    if wal.exists() and wal.stat().st_size > 0:
        raise ExtractionError(
            stage="extract",
            code="wal_not_checkpointed",
            summary=f"palace has uncheckpointed WAL: {wal}",
            details=["stop the writer process and retry"],
        )


def _pragma_integrity_check(conn: sqlite3.Connection, db_path: Path) -> None:
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as exc:
        raise ExtractionError(
            stage="extract",
            code="pragma_integrity_check_failed_to_run",
            summary=f"PRAGMA integrity_check could not run on {db_path}: {exc!r}",
        ) from exc

    results = [row[0] for row in rows]
    if results != ["ok"]:
        raise ExtractionError(
            stage="extract",
            code="sqlite_integrity_check_failed",
            summary=f"SQLite integrity_check did not return ok for {db_path}",
            details=results[:50],
        )


def _verify_required_tables(conn: sqlite3.Connection) -> None:
    try:
        present = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    except sqlite3.DatabaseError as exc:
        raise ExtractionError(
            stage="extract",
            code="sqlite_master_unreadable",
            summary=f"sqlite_master unreadable: {exc!r}",
        ) from exc

    missing = [t for t in REQUIRED_TABLES if t not in present]
    if missing:
        raise ExtractionError(
            stage="extract",
            code="required_tables_missing",
            summary=f"required tables missing: {missing}",
        )


def _verify_single_expected_collection(conn: sqlite3.Connection) -> str:
    try:
        rows = conn.execute("SELECT name FROM collections ORDER BY name").fetchall()
    except sqlite3.Error as exc:
        raise ExtractionError(
            stage="extract",
            code="collections_query_failed",
            summary=f"could not query collections table: {exc!r}",
        ) from exc

    names = [row["name"] for row in rows]
    if not names:
        raise ExtractionError(
            stage="extract", code="no_collection",
            summary="palace contains no collection",
        )
    if len(names) > 1:
        raise ExtractionError(
            stage="extract", code="multiple_collections",
            summary=f"palace contains {len(names)} collections; expected exactly 1",
            details=names,
        )
    if names[0] != EXPECTED_COLLECTION_NAME:
        raise ExtractionError(
            stage="extract", code="unexpected_collection_name",
            summary=f"expected collection {EXPECTED_COLLECTION_NAME!r}, found {names[0]!r}",
        )
    return names[0]


def _embedding_row_count(conn: sqlite3.Connection) -> int:
    try:
        return conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    except sqlite3.Error as exc:
        raise ExtractionError(
            stage="extract", code="embeddings_query_failed",
            summary=f"could not count embeddings: {exc!r}",
        ) from exc


# --- Resilient row reader --------------------------------------------------


def _read_drawers_resilient(
    conn: sqlite3.Connection, ctx: MigrationContext
) -> tuple[list[DrawerRecord], list[FailedRow]]:
    """Iterate every embedding row. A single failure does not abort.

    Detected per-row issues (severity in parentheses):
      - blank/NULL embedding_id (HIGH) -> exclude
      - control chars in id (HIGH) -> exclude
      - duplicate embedding_id (HIGH) -> exclude all rows sharing the id
      - missing chroma:document (HIGH) -> exclude
      - chroma:document with NULL string_value (HIGH) -> exclude
      - duplicate metadata key on a row (MEDIUM) -> include, last wins
      - all-NULL typed metadata value (HIGH for the key) -> exclude row
      - metadata fetch sqlite error (HIGH) -> exclude
    """
    drawers: list[DrawerRecord] = []
    failed: list[FailedRow] = []

    # Pre-compute duplicate id set so we can exclude all sharing rows.
    duplicate_ids: set[str] = set()
    try:
        for row in conn.execute(
            """
            SELECT embedding_id FROM embeddings
            WHERE embedding_id IS NOT NULL AND TRIM(embedding_id) != ''
            GROUP BY embedding_id HAVING COUNT(*) > 1
            """
        ):
            duplicate_ids.add(row["embedding_id"])
    except sqlite3.DatabaseError as exc:
        # If we can't even compute duplicates, the table is too damaged
        # to read row by row safely.
        raise ExtractionError(
            stage="extract", code="embeddings_scan_failed",
            summary=f"cannot scan embeddings for duplicates: {exc!r}",
        ) from exc

    if duplicate_ids:
        ctx.add_anomaly(
            type="duplicate_embedding_ids",
            severity="high",
            stage="extract",
            message=f"{len(duplicate_ids)} embedding_id values appear more than once",
            context={"sample": sorted(duplicate_ids)[:20]},
        )

    try:
        cursor = conn.execute(
            "SELECT id, embedding_id FROM embeddings ORDER BY id"
        )
    except sqlite3.DatabaseError as exc:
        raise ExtractionError(
            stage="extract", code="embeddings_iter_failed",
            summary=f"cannot iterate embeddings: {exc!r}",
        ) from exc

    for row in cursor:
        emb_pk = row["id"]
        drawer_id = row["embedding_id"]

        # Blank / NULL id
        if drawer_id is None or not isinstance(drawer_id, str) or not drawer_id.strip():
            failed.append(
                FailedRow(
                    embedding_pk=emb_pk,
                    embedding_id=None if drawer_id is None else str(drawer_id),
                    reason_type="blank_embedding_id",
                    message="embedding_id is NULL or blank",
                )
            )
            ctx.add_anomaly(
                type="blank_embedding_id", severity="high", stage="extract",
                message=f"row pk={emb_pk} has blank/NULL id; excluded",
                context={"embedding_pk": emb_pk},
            )
            continue

        # Control chars
        if ID_FORBIDDEN_CHARS_RE.search(drawer_id):
            failed.append(
                FailedRow(
                    embedding_pk=emb_pk, embedding_id=drawer_id,
                    reason_type="control_chars_in_id",
                    message="embedding_id contains control characters",
                )
            )
            ctx.add_anomaly(
                type="control_chars_in_id", severity="high", stage="extract",
                message=f"row pk={emb_pk}: id contains control chars; excluded",
                context={"embedding_pk": emb_pk, "id_repr": repr(drawer_id)[:120]},
            )
            continue

        # Duplicate id
        if drawer_id in duplicate_ids:
            failed.append(
                FailedRow(
                    embedding_pk=emb_pk, embedding_id=drawer_id,
                    reason_type="duplicate_embedding_id",
                    message="this embedding_id is duplicated; row excluded",
                )
            )
            continue

        # Read metadata for this row.
        try:
            meta_rows = conn.execute(
                """
                SELECT key, string_value, int_value, float_value, bool_value
                FROM embedding_metadata
                WHERE id = ?
                ORDER BY key
                """,
                (emb_pk,),
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            failed.append(
                FailedRow(
                    embedding_pk=emb_pk, embedding_id=drawer_id,
                    reason_type="metadata_query_failed",
                    message=f"sqlite error reading metadata: {exc!r}",
                )
            )
            ctx.add_anomaly(
                type="metadata_query_failed", severity="high", stage="extract",
                message=f"row pk={emb_pk}: sqlite error on metadata read; excluded",
                context={"embedding_pk": emb_pk, "embedding_id": drawer_id, "error": repr(exc)},
            )
            continue

        if not meta_rows:
            failed.append(
                FailedRow(
                    embedding_pk=emb_pk, embedding_id=drawer_id,
                    reason_type="orphan_embedding",
                    message="no embedding_metadata rows for this embedding",
                )
            )
            ctx.add_anomaly(
                type="orphan_embedding", severity="high", stage="extract",
                message=f"row pk={emb_pk} id={drawer_id!r}: orphan; excluded",
                context={"embedding_pk": emb_pk, "embedding_id": drawer_id},
            )
            continue

        # Build document + metadata, recording per-row issues.
        doc_values: list[Any] = []
        metadata: dict[str, Any] = {}
        seen_user_keys: dict[str, int] = {}
        row_failed = False
        row_failure: FailedRow | None = None

        for m in meta_rows:
            key = m["key"]
            if key == "chroma:document":
                if m["string_value"] is None:
                    row_failure = FailedRow(
                        embedding_pk=emb_pk, embedding_id=drawer_id,
                        reason_type="document_string_value_null",
                        message="chroma:document has NULL string_value",
                    )
                    ctx.add_anomaly(
                        type="document_string_value_null", severity="high", stage="extract",
                        message=f"row id={drawer_id!r}: NULL document; excluded",
                        context={"embedding_id": drawer_id},
                    )
                    row_failed = True
                    break
                doc_values.append(m["string_value"])
                continue

            if key.startswith("chroma:"):
                continue

            seen_user_keys[key] = seen_user_keys.get(key, 0) + 1
            try:
                metadata[key] = _resolve_metadata_value(m)
            except _MetadataAllNull:
                row_failure = FailedRow(
                    embedding_pk=emb_pk, embedding_id=drawer_id,
                    reason_type="metadata_all_null",
                    message=f"metadata key {key!r} has all typed values NULL",
                    context={"key": key},
                )
                ctx.add_anomaly(
                    type="metadata_all_null", severity="high", stage="extract",
                    message=f"row id={drawer_id!r}: key {key!r} fully NULL; excluded",
                    context={"embedding_id": drawer_id, "key": key},
                )
                row_failed = True
                break

        if row_failed and row_failure is not None:
            failed.append(row_failure)
            continue

        # Document presence
        if len(doc_values) == 0:
            failed.append(
                FailedRow(
                    embedding_pk=emb_pk, embedding_id=drawer_id,
                    reason_type="document_missing",
                    message="no chroma:document entry for this row",
                )
            )
            ctx.add_anomaly(
                type="document_missing", severity="high", stage="extract",
                message=f"row id={drawer_id!r}: no document; excluded",
                context={"embedding_id": drawer_id},
            )
            continue

        if len(doc_values) > 1:
            failed.append(
                FailedRow(
                    embedding_pk=emb_pk, embedding_id=drawer_id,
                    reason_type="document_multiple",
                    message=f"{len(doc_values)} chroma:document entries for this row",
                )
            )
            ctx.add_anomaly(
                type="document_multiple", severity="high", stage="extract",
                message=f"row id={drawer_id!r}: {len(doc_values)} docs; excluded",
                context={"embedding_id": drawer_id, "count": len(doc_values)},
            )
            continue

        # Duplicate user metadata keys (MEDIUM, row kept, last value wins)
        dup_keys = [k for k, c in seen_user_keys.items() if c > 1]
        if dup_keys:
            ctx.add_anomaly(
                type="duplicate_metadata_keys", severity="medium", stage="extract",
                message=f"row id={drawer_id!r}: duplicate metadata keys; last value kept",
                context={"embedding_id": drawer_id, "keys": dup_keys},
            )

        drawers.append(
            DrawerRecord(id=drawer_id, document=doc_values[0], metadata=metadata)
        )

    return drawers, failed


class _MetadataAllNull(Exception):
    pass


def _resolve_metadata_value(row: sqlite3.Row) -> Any:
    if row["string_value"] is not None:
        return row["string_value"]
    if row["int_value"] is not None:
        return row["int_value"]
    if row["float_value"] is not None:
        return row["float_value"]
    if row["bool_value"] is not None:
        return bool(row["bool_value"])
    raise _MetadataAllNull
