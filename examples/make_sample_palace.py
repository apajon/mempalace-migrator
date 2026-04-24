#!/usr/bin/env python3
"""Build a minimal chroma_0_6 sample palace for testing mempalace-migrator.

Usage:
    python examples/make_sample_palace.py /path/to/output_dir

The script is self-contained and uses only Python stdlib.  It does NOT
import from mempalace_migrator or tests — it is designed to be readable
and runnable without the package installed.

The produced directory is a valid input for:
    python -m mempalace_migrator.cli.main analyze /path/to/output_dir
    python -m mempalace_migrator.cli.main migrate /path/to/output_dir --target /path/to/new

Exit 0 on success.  Prints the output path.  Exit 1 on bad arguments.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants — must match production modules exactly.
#   detection/format_detector.py : MANIFEST_FILENAME, SQLITE_FILENAME
#   extraction/chroma_06_reader.py : EXPECTED_COLLECTION_NAME
# ---------------------------------------------------------------------------

MANIFEST_FILENAME = "mempalace-bridge-manifest.json"
SQLITE_FILENAME = "chroma.sqlite3"
COLLECTION_NAME = "mempalace_drawers"  # EXPECTED_COLLECTION_NAME in production

MANIFEST = {
    "compatibility_line": "chromadb-0.6.x",
    "chromadb_version": "0.6.3",
}

DRAWERS = [
    {"embedding_id": "drawer-1", "document": "The library holds ancient maps."},
    {"embedding_id": "drawer-2", "document": "A forge lit by eternal flame."},
    {"embedding_id": "drawer-3", "document": "Silence in the empty gallery."},
]


def build(dest: Path) -> None:
    """Create a minimal valid chroma_0_6 palace at *dest*."""
    dest.mkdir(parents=True, exist_ok=True)

    (dest / MANIFEST_FILENAME).write_text(json.dumps(MANIFEST), encoding="utf-8")

    db_path = dest / SQLITE_FILENAME
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript("""
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
        """)
        conn.execute(
            "INSERT INTO collections (id, name) VALUES (1, ?)",
            (COLLECTION_NAME,),
        )
        for pk, drawer in enumerate(DRAWERS, start=1):
            conn.execute(
                "INSERT INTO embeddings (id, collection_id, embedding_id) VALUES (?, 1, ?)",
                (pk, drawer["embedding_id"]),
            )
            conn.execute(
                "INSERT INTO embedding_metadata"
                " (id, key, string_value, int_value, float_value, bool_value)"
                " VALUES (?, 'chroma:document', ?, NULL, NULL, NULL)",
                (pk, drawer["document"]),
            )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} OUTPUT_DIR", file=sys.stderr)
        sys.exit(1)
    out = Path(sys.argv[1])
    build(out)
    print(f"Sample palace written to: {out}")
