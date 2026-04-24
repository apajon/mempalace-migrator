"""M7 adversarial corpus: builders + subprocess runner.

The corpus is a registry of named adversarial inputs. Each entry is a
``CorpusEntry`` declaring:

* ``cid``                 : stable corpus id (used as pytest parameter id);
* ``builder(tmp_path)``    : pure factory that materialises the source dir;
* ``pipeline``             : ``analyze`` | ``inspect``;
* ``allowed_exit_codes``   : non-empty set of ints; the cross-cutting
                             invariants test rejects any exit code outside
                             this set, and explicitly rejects exit 10
                             unless the entry says so on purpose.

Every adversarial test imports its inputs from ``CORPUS`` so the
invariants file (10.6) parametrises over the **exact same** set. There
is intentionally no other place where adversarial bytes are produced.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from mempalace_migrator.detection.format_detector import (
    MANIFEST_FILENAME,
    SQLITE_FILENAME,
)
from mempalace_migrator.extraction.chroma_06_reader import EXPECTED_COLLECTION_NAME

# ---------------------------------------------------------------------------
# Exit codes (re-exported for clarity in adversarial tests)
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_USAGE_ERROR = 1
EXIT_DETECTION_FAILED = 2
EXIT_EXTRACTION_FAILED = 3
EXIT_TRANSFORM_FAILED = 4
EXIT_RECONSTRUCT_FAILED = 5
EXIT_REPORT_FAILED = 6
EXIT_VALIDATE_FAILED = 7
EXIT_CRITICAL_ANOMALY = 8
EXIT_REPORT_FILE_ERROR = 9
EXIT_UNEXPECTED = 10


# ---------------------------------------------------------------------------
# Subprocess runner — required because Click >=8.2 CliRunner mixes streams.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CliResult:
    returncode: int
    stdout: str
    stderr: str

    def parse_report(self) -> dict:
        if not self.stdout.strip():
            raise AssertionError(f"expected JSON report on stdout but stdout is empty; " f"stderr={self.stderr!r}")
        return json.loads(self.stdout)


def run_cli(args: list[str]) -> CliResult:
    """Invoke the CLI in a real subprocess so stdout/stderr stay separate."""
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "mempalace_migrator.cli.main", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=90,
    )
    return CliResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

MANIFEST_06_VALID = {
    "compatibility_line": "chromadb-0.6.x",
    "chromadb_version": "0.6.3",
}


def write_manifest(root: Path, data: dict | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_FILENAME).write_text(
        json.dumps(MANIFEST_06_VALID if data is None else data),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# SQLite helpers — minimal valid Chroma 0.6 baseline.
# Every corruption builder mutates the output of this baseline.
# ---------------------------------------------------------------------------


def _create_06_schema(conn: sqlite3.Connection) -> None:
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


def _insert_collection(conn: sqlite3.Connection, name: str = EXPECTED_COLLECTION_NAME) -> None:
    conn.execute("INSERT INTO collections (id, name) VALUES (1, ?)", (name,))


def _insert_drawer(
    conn: sqlite3.Connection,
    pk: int,
    embedding_id: str | None,
    document: str | None,
) -> None:
    conn.execute(
        "INSERT INTO embeddings (id, collection_id, embedding_id) VALUES (?, 1, ?)",
        (pk, embedding_id),
    )
    if document is not None:
        conn.execute(
            "INSERT INTO embedding_metadata (id, key, string_value) VALUES (?, 'chroma:document', ?)",
            (pk, document),
        )


def build_minimal_valid_chroma_06(root: Path, *, n_drawers: int = 2) -> Path:
    """Baseline: a directory that should run cleanly through ``analyze``.

    All other builders below mutate the output of this function. The
    baseline itself is part of the corpus to confirm the test harness is
    not over-eager.
    """
    root.mkdir(parents=True, exist_ok=True)
    write_manifest(root)
    db_path = root / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        for i in range(1, n_drawers + 1):
            _insert_drawer(conn, pk=i, embedding_id=f"drawer-{i}", document=f"content {i}")
        conn.commit()
    finally:
        conn.close()
    return root


# ---------------------------------------------------------------------------
# Corruption / pathology builders. Each takes ``tmp_path`` and returns it.
# ---------------------------------------------------------------------------

# --- 10.1 / 10.4 per-row pathologies ---------------------------------------


def build_blank_embedding_id(tmp_path: Path) -> Path:
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        _insert_drawer(conn, pk=1, embedding_id="ok", document="ok-doc")
        _insert_drawer(conn, pk=2, embedding_id=None, document="orphan-doc")
        _insert_drawer(conn, pk=3, embedding_id="   ", document="ws-doc")
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_control_chars_in_id(tmp_path: Path) -> Path:
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        _insert_drawer(conn, pk=1, embedding_id="ok", document="ok-doc")
        _insert_drawer(conn, pk=2, embedding_id="bad\x01id", document="bad-doc")
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_duplicate_embedding_ids(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        _insert_drawer(conn, pk=1, embedding_id="dup", document="first")
        _insert_drawer(conn, pk=2, embedding_id="dup", document="second")
        _insert_drawer(conn, pk=3, embedding_id="ok", document="kept")
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_document_missing(tmp_path: Path) -> Path:
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        _insert_drawer(conn, pk=1, embedding_id="no-doc", document=None)
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_document_null_string_value(tmp_path: Path) -> Path:
    """chroma:document row exists but its string_value is SQL NULL."""
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        conn.execute("INSERT INTO embeddings (id, collection_id, embedding_id) VALUES (1, 1, 'null-doc')")
        conn.execute("INSERT INTO embedding_metadata (id, key, string_value) VALUES (1, 'chroma:document', NULL)")
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_document_multiple(tmp_path: Path) -> Path:
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        conn.execute("INSERT INTO embeddings (id, collection_id, embedding_id) VALUES (1, 1, 'two-docs')")
        conn.execute("INSERT INTO embedding_metadata (id, key, string_value) VALUES (1, 'chroma:document', 'first')")
        conn.execute("INSERT INTO embedding_metadata (id, key, string_value) VALUES (1, 'chroma:document', 'second')")
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_metadata_all_null(tmp_path: Path) -> Path:
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        _insert_drawer(conn, pk=1, embedding_id="all-null", document="ok")
        conn.execute(
            "INSERT INTO embedding_metadata "
            "(id, key, string_value, int_value, float_value, bool_value) "
            "VALUES (1, 'k', NULL, NULL, NULL, NULL)"
        )
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_unparseable_metadata_string_value(tmp_path: Path) -> Path:
    """``string_value`` contains a JSON-shaped byte payload.

    The reader treats string_value as opaque (no JSON parsing), so this
    row is **expected** to succeed. M7 asserts the input does not crash
    the pipeline and that the value lands in ``drawers`` without
    interpretation. Any future JSON-aware code path must surface this
    case structurally rather than crash.
    """
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        _insert_drawer(conn, pk=1, embedding_id="json-shaped", document="doc")
        conn.execute(
            "INSERT INTO embedding_metadata (id, key, string_value) " "VALUES (1, 'payload', '{not valid json,,,')"
        )
        conn.commit()
    finally:
        conn.close()
    return tmp_path


# --- 10.2 broken SQLite -----------------------------------------------------


def build_sqlite_missing(tmp_path: Path) -> Path:
    """Manifest present, no SQLite at all → extraction pre-flight fails."""
    write_manifest(tmp_path)
    return tmp_path


def build_zeroed_sqlite_header(tmp_path: Path) -> Path:
    """Valid baseline, then zero out the first 100 bytes (SQLite header).

    This is real bit-level corruption: PRAGMA integrity_check or
    sqlite_master read is expected to fail, raising ExtractionError.
    """
    build_minimal_valid_chroma_06(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    raw = db_path.read_bytes()
    db_path.write_bytes(b"\x00" * 100 + raw[100:])
    return tmp_path


def build_truncated_sqlite(tmp_path: Path) -> Path:
    """Truncate the DB mid-page so reads break."""
    build_minimal_valid_chroma_06(tmp_path, n_drawers=4)
    db_path = tmp_path / SQLITE_FILENAME
    raw = db_path.read_bytes()
    if len(raw) < 200:
        # baseline always > 200 bytes; guard against fixture drift
        raise RuntimeError("baseline sqlite unexpectedly small")
    db_path.write_bytes(raw[: len(raw) // 2])
    return tmp_path


def build_required_table_missing(tmp_path: Path) -> Path:
    """Schema is missing ``embedding_metadata``."""
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE collections (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE embeddings  (id INTEGER PRIMARY KEY, embedding_id TEXT);
            """
        )
        _insert_collection(conn)
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_no_collection(tmp_path: Path) -> Path:
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_multiple_collections(tmp_path: Path) -> Path:
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn, name=EXPECTED_COLLECTION_NAME)
        conn.execute("INSERT INTO collections (id, name) VALUES (2, 'extra')")
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_unexpected_collection_name(tmp_path: Path) -> Path:
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn, name="not_the_expected_name")
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_wal_not_checkpointed(tmp_path: Path) -> Path:
    build_minimal_valid_chroma_06(tmp_path)
    (tmp_path / (SQLITE_FILENAME + "-wal")).write_bytes(b"non-empty wal payload")
    return tmp_path


# --- 10.3 mixed format / contradictions -------------------------------------


def build_manifest_says_1x_db_is_06(tmp_path: Path) -> Path:
    write_manifest(
        tmp_path,
        {"compatibility_line": "chromadb-1.x", "chromadb_version": "1.5.0"},
    )
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        _insert_drawer(conn, pk=1, embedding_id="d1", document="x")
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_manifest_internal_conflict(tmp_path: Path) -> Path:
    write_manifest(
        tmp_path,
        {"compatibility_line": "chromadb-1.x", "chromadb_version": "0.6.3"},
    )
    build_minimal_valid_chroma_06_inplace_db(tmp_path)
    return tmp_path


def build_minimal_valid_chroma_06_inplace_db(tmp_path: Path) -> None:
    """Create only the SQLite (no manifest)."""
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        _insert_drawer(conn, pk=1, embedding_id="d1", document="x")
        conn.commit()
    finally:
        conn.close()


def build_typed_marker_present(tmp_path: Path) -> Path:
    """Add a 1.x ``config_json`` column on top of a 0.6 manifest."""
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE collections (
                id INTEGER PRIMARY KEY,
                name TEXT,
                config_json TEXT
            );
            CREATE TABLE embeddings (
                id INTEGER PRIMARY KEY,
                collection_id INTEGER,
                embedding_id TEXT
            );
            CREATE TABLE embedding_metadata (
                id INTEGER NOT NULL,
                key TEXT NOT NULL,
                string_value TEXT,
                int_value INTEGER,
                float_value REAL,
                bool_value INTEGER
            );
            """
        )
        conn.execute(
            "INSERT INTO collections (id, name, config_json) VALUES (1, ?, '{}')",
            (EXPECTED_COLLECTION_NAME,),
        )
        _insert_drawer(conn, pk=1, embedding_id="d1", document="x")
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_manifest_invalid_json(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")
    build_minimal_valid_chroma_06_inplace_db(tmp_path)
    return tmp_path


def build_unsupported_version(tmp_path: Path) -> Path:
    """0.5.0 is not in SUPPORTED_VERSION_PAIRS."""
    write_manifest(
        tmp_path,
        {"compatibility_line": "chromadb-0.6.x", "chromadb_version": "0.5.0"},
    )
    build_minimal_valid_chroma_06_inplace_db(tmp_path)
    return tmp_path


# --- 10.5 extreme edge cases -----------------------------------------------


def build_empty_dir(tmp_path: Path) -> Path:
    """No manifest, no SQLite → detection floor."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    return tmp_path


def build_palace_with_no_embeddings(tmp_path: Path) -> Path:
    """Schema valid, collection present, embeddings table empty.

    Extraction should succeed with parsed=0, failed=0; the report should
    drop validation confidence band to LOW (parse_rate==0 plausibility).
    """
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_all_rows_unparseable(tmp_path: Path) -> Path:
    """Every embedding row fails extraction (orphan: no document)."""
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        for pk in range(1, 6):
            _insert_drawer(conn, pk=pk, embedding_id=f"orphan-{pk}", document=None)
        conn.commit()
    finally:
        conn.close()
    return tmp_path


# ---------------------------------------------------------------------------
# Corpus registry
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# M12 write-path builders (15.1, 15.2, 15.3, 15.9, 15.10, 15.11)
# All builders return the source path.  Where a *target* is required the test
# sets it up itself (cannot be embedded in a source builder).
# ---------------------------------------------------------------------------


def build_all_blank_ids(tmp_path: Path) -> Path:
    """Every drawer has a blank / whitespace-only embedding_id.

    Transformation should drop each row with TRANSFORM_DRAWER_DROPPED.
    The resulting TransformedBundle.drawers is empty, so migrate returns
    EXIT_RECONSTRUCT_FAILED (step_reconstruct detects the empty bundle).
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        for pk in range(1, 4):
            _insert_drawer(conn, pk=pk, embedding_id="   ", document=f"doc-{pk}")
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_all_nonstring_documents(tmp_path: Path) -> Path:
    """Every drawer has a NULL document (mapping to ORPHAN_EMBEDDING on extraction).

    The extraction emits ORPHAN_EMBEDDING for each row; no drawer passes
    into the TransformedBundle, so migrate returns EXIT_RECONSTRUCT_FAILED.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        for pk in range(1, 4):
            _insert_drawer(conn, pk=pk, embedding_id=f"id-{pk}", document=None)
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_large_valid_source(tmp_path: Path, *, n_rows: int) -> Path:
    """Build a valid source with *n_rows* drawers for batch-size stress testing."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    write_manifest(tmp_path)
    db_path = tmp_path / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    try:
        _create_06_schema(conn)
        _insert_collection(conn)
        for pk in range(1, n_rows + 1):
            _insert_drawer(conn, pk=pk, embedding_id=f"row-{pk}", document=f"document content {pk}")
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def build_duplicate_ids_for_writer(tmp_path: Path) -> Path:
    """Two rows share the same embedding_id — reuses the existing builder."""
    return build_duplicate_embedding_ids(tmp_path)


# ---------------------------------------------------------------------------
# M12 run_migrate_cli helper
# ---------------------------------------------------------------------------


def run_migrate_cli(source: Path, target: Path, *, json_output: bool = True) -> "CliResult":
    """Run ``migrate <source> --target <target>`` as a real subprocess.

    Mirrors run_cli but handles the mandatory ``--target`` flag required by
    the migrate subcommand.
    """
    args: list[str] = []
    if json_output:
        args.append("--json-output")
    args.extend(["migrate", str(source), "--target", str(target)])
    return run_cli(args)


# ---------------------------------------------------------------------------
# CorpusEntry (unchanged shape; pipeline field now accepts "migrate" too)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CorpusEntry:
    cid: str
    builder: Callable[[Path], Path]
    pipeline: str  # "analyze" | "inspect" | "migrate"
    allowed_exit_codes: frozenset[int]
    category: str  # "10.1" | "10.2" | "10.3" | "10.4" | "10.5" | "baseline" | "15.x"
    extra_tags: tuple[str, ...] = field(default_factory=tuple)
    # For migrate entries only: extra CLI args beyond the source path.
    # run_cli does not forward these; use run_migrate_cli in migrate-specific tests.
    extra_cli_args: tuple[str, ...] = field(default_factory=tuple)


# Allowed-exit-code rationale per entry. Anything else from the CLI is a
# defect to fix in production code, not in the test.
CORPUS: tuple[CorpusEntry, ...] = (
    # baseline (sanity)
    CorpusEntry(
        "baseline_valid_06_palace", build_minimal_valid_chroma_06, "analyze", frozenset({EXIT_OK}), "baseline"
    ),
    CorpusEntry(
        "baseline_valid_06_palace_inspect", build_minimal_valid_chroma_06, "inspect", frozenset({EXIT_OK}), "baseline"
    ),
    # 10.1 per-row / value-shape pathologies (extraction continues; report success)
    CorpusEntry("blank_embedding_id", build_blank_embedding_id, "analyze", frozenset({EXIT_OK}), "10.1"),
    CorpusEntry("control_chars_in_id", build_control_chars_in_id, "analyze", frozenset({EXIT_OK}), "10.1"),
    CorpusEntry("document_missing", build_document_missing, "analyze", frozenset({EXIT_OK}), "10.1"),
    CorpusEntry(
        "document_null_string_value", build_document_null_string_value, "analyze", frozenset({EXIT_OK}), "10.1"
    ),
    CorpusEntry("document_multiple", build_document_multiple, "analyze", frozenset({EXIT_OK}), "10.1"),
    CorpusEntry("metadata_all_null", build_metadata_all_null, "analyze", frozenset({EXIT_OK}), "10.1"),
    CorpusEntry(
        "unparseable_metadata_string_value",
        build_unparseable_metadata_string_value,
        "analyze",
        frozenset({EXIT_OK}),
        "10.1",
    ),
    # 10.2 broken SQLite. Either detection (gate) or extraction (deeper scan)
    # may surface the defect; both are structured rejections.
    CorpusEntry(
        "sqlite_missing",
        build_sqlite_missing,
        "analyze",
        frozenset({EXIT_DETECTION_FAILED, EXIT_EXTRACTION_FAILED}),
        "10.2",
    ),
    CorpusEntry(
        "zeroed_sqlite_header",
        build_zeroed_sqlite_header,
        "analyze",
        frozenset({EXIT_DETECTION_FAILED, EXIT_EXTRACTION_FAILED}),
        "10.2",
    ),
    CorpusEntry("truncated_sqlite", build_truncated_sqlite, "analyze", frozenset({EXIT_DETECTION_FAILED}), "10.2"),
    CorpusEntry(
        "required_table_missing",
        build_required_table_missing,
        "analyze",
        frozenset({EXIT_DETECTION_FAILED, EXIT_EXTRACTION_FAILED}),
        "10.2",
    ),
    CorpusEntry(
        "no_collection",
        build_no_collection,
        "analyze",
        frozenset({EXIT_DETECTION_FAILED, EXIT_EXTRACTION_FAILED}),
        "10.2",
    ),
    CorpusEntry(
        "multiple_collections",
        build_multiple_collections,
        "analyze",
        frozenset({EXIT_DETECTION_FAILED, EXIT_EXTRACTION_FAILED}),
        "10.2",
    ),
    CorpusEntry(
        "unexpected_collection_name",
        build_unexpected_collection_name,
        "analyze",
        frozenset({EXIT_DETECTION_FAILED, EXIT_EXTRACTION_FAILED}),
        "10.2",
    ),
    CorpusEntry(
        "wal_not_checkpointed",
        build_wal_not_checkpointed,
        "analyze",
        frozenset({EXIT_DETECTION_FAILED, EXIT_EXTRACTION_FAILED}),
        "10.2",
    ),
    # 10.3 mixed-format / contradictory-signal inputs (detection rejects)
    CorpusEntry(
        "manifest_says_1x_db_is_06",
        build_manifest_says_1x_db_is_06,
        "analyze",
        frozenset({EXIT_DETECTION_FAILED}),
        "10.3",
    ),
    CorpusEntry(
        "manifest_internal_conflict",
        build_manifest_internal_conflict,
        "analyze",
        frozenset({EXIT_DETECTION_FAILED}),
        "10.3",
    ),
    CorpusEntry(
        "typed_marker_present", build_typed_marker_present, "analyze", frozenset({EXIT_DETECTION_FAILED}), "10.3"
    ),
    CorpusEntry(
        "manifest_invalid_json", build_manifest_invalid_json, "analyze", frozenset({EXIT_DETECTION_FAILED}), "10.3"
    ),
    CorpusEntry(
        "unsupported_version", build_unsupported_version, "analyze", frozenset({EXIT_DETECTION_FAILED}), "10.3"
    ),
    # 10.4 inconsistent data — duplicates trigger HIGH at extract; validation
    #      via inspect surfaces the resulting band downgrade.
    CorpusEntry(
        "duplicate_embedding_ids_inspect", build_duplicate_embedding_ids, "inspect", frozenset({EXIT_OK}), "10.4"
    ),
    CorpusEntry("all_rows_unparseable_inspect", build_all_rows_unparseable, "inspect", frozenset({EXIT_OK}), "10.4"),
    # 10.5 extreme edge cases
    CorpusEntry("empty_dir", build_empty_dir, "analyze", frozenset({EXIT_DETECTION_FAILED}), "10.5"),
    CorpusEntry(
        "palace_with_no_embeddings_inspect",
        build_palace_with_no_embeddings,
        "inspect",
        frozenset({EXIT_DETECTION_FAILED, EXIT_OK}),
        "10.5",
    ),
    # 15.x M12 write-path entries
    # 15.3 pathological transformation inputs (migrate)
    CorpusEntry(
        "migrate_all_blank_ids",
        build_all_blank_ids,
        "migrate",
        frozenset({EXIT_RECONSTRUCT_FAILED}),
        "15.3",
        extra_tags=("write_path",),
    ),
    CorpusEntry(
        "migrate_all_nonstring_documents",
        build_all_nonstring_documents,
        "migrate",
        frozenset({EXIT_RECONSTRUCT_FAILED}),
        "15.3",
        extra_tags=("write_path",),
    ),
    # 15.4 / 15.5 migrate-success baseline (minimal valid source)
    CorpusEntry(
        "migrate_baseline_valid",
        build_minimal_valid_chroma_06,
        "migrate",
        frozenset({EXIT_OK}),
        "baseline",
        extra_tags=("write_path",),
    ),
    # 15.10 duplicate-ids source reaching the writer
    CorpusEntry(
        "migrate_duplicate_ids",
        build_duplicate_ids_for_writer,
        "migrate",
        frozenset({EXIT_OK, EXIT_RECONSTRUCT_FAILED}),
        "15.10",
        extra_tags=("write_path",),
    ),
    # 20.1 — M17 trust & safety: source fixtures used by 20.2 / 20.3 / 20.5.
    # In the invariant sweep these all run cleanly (valid source → exit 0).
    # Failure injection and idempotence / parity-trust assertions are in the
    # dedicated 20.x test files; here we only register the source fixture so
    # that Inv. 1–9 apply to the plain (uninjected) run.
    CorpusEntry(
        "m17_valid_source_for_injection",
        build_minimal_valid_chroma_06,
        "migrate",
        frozenset({EXIT_OK}),
        "20.1",
        extra_tags=("m17", "write_path"),
    ),
)


def corpus_by_category(category: str) -> tuple[CorpusEntry, ...]:
    return tuple(e for e in CORPUS if e.category == category)


def corpus_by_id(cid: str) -> CorpusEntry:
    for e in CORPUS:
        if e.cid == cid:
            return e
    raise KeyError(f"unknown corpus id: {cid!r}")


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


def build_and_run(entry: CorpusEntry, tmp_path: Path, *, json_output: bool = True) -> tuple[Path, CliResult]:
    """Materialise the corpus entry at ``tmp_path`` and run the CLI once.

    Used by category-specific tests that want to assert on the report
    JSON without re-implementing the build/run dance.
    """
    palace = entry.builder(tmp_path)
    args: list[str] = []
    if json_output:
        args.append("--json-output")
    args.extend([entry.pipeline, str(palace)])
    return palace, run_cli(args)


@pytest.fixture
def adversarial_palace(request, tmp_path: Path) -> tuple[CorpusEntry, Path]:
    """Materialise a corpus entry at ``tmp_path``.

    Used by ``@pytest.mark.parametrize("entry", CORPUS, ids=lambda e: e.cid)``.
    """
    entry: CorpusEntry = request.param if hasattr(request, "param") else None
    if entry is None:
        raise RuntimeError("adversarial_palace must be parametrized with a CorpusEntry")
    palace = entry.builder(tmp_path)
    return entry, palace
