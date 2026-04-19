"""Focused tests for the contradiction policy (DESIGN.md §10).

One test per grade (AGREE / BENIGN / SOFT / HARD / SEVERE) plus regressions
for R3 and the "removing manifest flips outcome" invariant.  Each test checks:
  - the final classification
  - confidence (exact value or cap)
  - whether a manifest_vs_structure evidence entry is present (when required)

No MigrationContext is needed; all tests call detect_palace_format(Path)
directly.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mempalace_migrator.detection.format_detector import (
    CAP_BENIGN,
    CAP_HARD,
    CAP_SEVERE,
    CAP_SOFT,
    CHROMA_0_6,
    CHROMA_1_X,
    MANIFEST_FILENAME,
    SQLITE_FILENAME,
    UNKNOWN,
    detect_palace_format,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MANIFEST_06 = {
    "compatibility_line": "chromadb-0.6.x",
    "chromadb_version": "0.6.3",
}
_MANIFEST_1X = {
    "compatibility_line": "chromadb-1.x",
    "chromadb_version": "1.5.7",
}


def _write_manifest(root: Path, data: dict) -> None:
    (root / MANIFEST_FILENAME).write_text(json.dumps(data))


def _make_db(
    root: Path,
    *,
    n_collections: int = 1,
    n_embeddings: int = 1,
    typed_marker: bool = False,
    skip_tables: bool = False,
) -> None:
    """Write a minimal SQLite file at root/chroma.sqlite3."""
    db_path = root / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    if skip_tables:
        # Force a non-zero-byte SQLite file with no user tables.
        conn.execute("PRAGMA user_version = 1")
    else:
        if typed_marker:
            conn.execute("CREATE TABLE collections " "(id INTEGER PRIMARY KEY, config_json TEXT)")
        else:
            conn.execute("CREATE TABLE collections " "(id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE embeddings " "(id INTEGER PRIMARY KEY, collection_id INTEGER)")
        conn.execute("CREATE TABLE embedding_metadata (id INTEGER PRIMARY KEY)")
        if typed_marker:
            for i in range(n_collections):
                conn.execute(
                    "INSERT INTO collections (id, config_json) VALUES (?, ?)",
                    (i, "{}"),
                )
        else:
            for i in range(n_collections):
                conn.execute(
                    "INSERT INTO collections (id, name) VALUES (?, ?)",
                    (i, f"col{i}"),
                )
        for i in range(n_embeddings):
            conn.execute(
                "INSERT INTO embeddings (id, collection_id) VALUES (?, ?)",
                (i, 0),
            )
    conn.commit()
    conn.close()


def _has_mvs_evidence(result, tag: str) -> bool:
    """Return True if any evidence entry has detail containing 'manifest_vs_structure: <tag>'."""
    needle = f"manifest_vs_structure: {tag}"
    return any(needle in e.detail for e in result.evidence)


def _has_evidence_kind(result, source: str, kind: str) -> bool:
    return any(e.source == source and e.kind == kind for e in result.evidence)


# ===========================================================================
# Grade: AGREE (§10.5 example 1)
# ===========================================================================


def test_agree_manifest_and_structure_coherent(tmp_path: Path) -> None:
    """Manifest 0.6.3 + DB has 0.6 tables and rows → AGREE, confidence 1.0."""
    _write_manifest(tmp_path, _MANIFEST_06)
    _make_db(tmp_path, n_collections=2, n_embeddings=4)

    result = detect_palace_format(tmp_path)

    assert result.classification == CHROMA_0_6
    assert result.confidence == 1.0
    assert result.source_version == "0.6.3"
    # No contradiction evidence should be emitted
    assert not any("manifest_vs_structure" in e.detail for e in result.evidence)


# ===========================================================================
# Grade: BENIGN (§10.5 example 2)
# ===========================================================================


def test_benign_manifest_present_db_missing(tmp_path: Path) -> None:
    """Manifest 0.6.3 + no DB → BENIGN, confidence capped at CAP_BENIGN (0.85)."""
    _write_manifest(tmp_path, _MANIFEST_06)
    # No DB written.

    result = detect_palace_format(tmp_path)

    assert result.classification == CHROMA_0_6
    assert result.confidence == pytest.approx(CAP_BENIGN)
    assert result.source_version == "0.6.3"
    # structure/missing evidence (from _classify_from_structure) must be present
    assert _has_evidence_kind(result, "structure", "missing")
    # No new reconciliation entry for BENIGN
    assert not any("manifest_vs_structure" in e.detail for e in result.evidence)


# ===========================================================================
# Grade: SOFT (§10.5 example 3)
# ===========================================================================


def test_soft_row_counts_inconsistent(tmp_path: Path) -> None:
    """Manifest 0.6.3 + DB with embeddings count=0, collections count>0 → SOFT, cap 0.80."""
    _write_manifest(tmp_path, _MANIFEST_06)
    _make_db(tmp_path, n_collections=3, n_embeddings=0)

    result = detect_palace_format(tmp_path)

    assert result.classification == CHROMA_0_6
    assert result.confidence == pytest.approx(CAP_SOFT)
    assert result.source_version == "0.6.3"
    # Both the structural inconsistency and the reconciliation tag must appear
    assert _has_evidence_kind(result, "structure", "inconsistency")
    assert _has_mvs_evidence(result, "row_counts_inconsistent")


# ===========================================================================
# Grade: HARD — typed marker (§10.5 example 4)
# ===========================================================================


def test_hard_typed_marker_with_06_manifest(tmp_path: Path) -> None:
    """Manifest 0.6.3 + DB has 0.6 tables AND typed-config column → HARD, cap 0.60.
    Classification must NOT flip to chroma_1_x (R3)."""
    _write_manifest(tmp_path, _MANIFEST_06)
    _make_db(tmp_path, typed_marker=True, n_collections=1, n_embeddings=1)

    result = detect_palace_format(tmp_path)

    assert result.classification == CHROMA_0_6, "R3: must not flip to chroma_1_x"
    assert result.confidence == pytest.approx(CAP_HARD)
    assert result.source_version == "0.6.3"
    assert _has_mvs_evidence(result, "typed_marker_present")


# ===========================================================================
# Grade: HARD — class clash (§10.5 example 5)
# ===========================================================================


def test_hard_manifest_1x_structure_06(tmp_path: Path) -> None:
    """Manifest says 1.x + DB is unmistakably 0.6 → HARD, classification follows manifest."""
    _write_manifest(tmp_path, _MANIFEST_1X)
    _make_db(tmp_path, n_collections=2, n_embeddings=2)

    result = detect_palace_format(tmp_path)

    assert result.classification == CHROMA_1_X, "manifest wins (R4)"
    assert result.confidence == pytest.approx(CAP_HARD)
    assert result.source_version == "1.5.7"
    assert _has_mvs_evidence(result, f"manifest={CHROMA_1_X} structure={CHROMA_0_6}")


# ===========================================================================
# Grade: SEVERE — required tables missing (§10.5 example 6)
# ===========================================================================


def test_severe_required_tables_missing(tmp_path: Path) -> None:
    """Manifest 0.6.3 + DB present but 0.6 tables absent → SEVERE, classification unknown."""
    _write_manifest(tmp_path, _MANIFEST_06)
    _make_db(tmp_path, skip_tables=True)  # empty schema

    result = detect_palace_format(tmp_path)

    assert result.classification == UNKNOWN, "SEVERE flips classification"
    assert result.confidence == pytest.approx(CAP_SEVERE)
    assert result.source_version == "0.6.3", "source_version preserved from manifest"
    assert _has_mvs_evidence(result, "required_tables_missing")


# ===========================================================================
# Grade: SEVERE — DB is empty (§10.5 example 7)
# ===========================================================================


def test_severe_db_empty_zero_bytes(tmp_path: Path) -> None:
    """Manifest 0.6.3 + chroma.sqlite3 is 0 bytes → SEVERE, classification unknown."""
    _write_manifest(tmp_path, _MANIFEST_06)
    (tmp_path / SQLITE_FILENAME).write_bytes(b"")

    result = detect_palace_format(tmp_path)

    assert result.classification == UNKNOWN
    assert result.confidence == pytest.approx(CAP_SEVERE)
    assert result.source_version == "0.6.3"
    assert _has_mvs_evidence(result, "db_empty")


# ===========================================================================
# Regression: removing the manifest from a HARD/SEVERE fixture (§10.7)
# ===========================================================================


def test_regression_removing_manifest_from_hard_yields_structural_only(
    tmp_path: Path,
) -> None:
    """Without a manifest, typed-config DB must not produce chroma_1_x (R3)."""
    # No manifest — just the DB with the 1.x marker
    _make_db(tmp_path, typed_marker=True, n_collections=1, n_embeddings=1)

    result = detect_palace_format(tmp_path)

    assert result.classification == UNKNOWN, "R3: no 1.x inference from structure alone"
    assert result.confidence < 0.9, "structural-only confidence must stay below gate"
    assert result.source_version is None


def test_regression_removing_manifest_from_severe_yields_structural_only(
    tmp_path: Path,
) -> None:
    """Without a manifest, a DB with no 0.6 tables → unknown with structural confidence."""
    _make_db(tmp_path, skip_tables=True)

    result = detect_palace_format(tmp_path)

    assert result.classification == UNKNOWN
    assert result.confidence < 0.9
    assert result.source_version is None


# ===========================================================================
# Regression: unsupported version does not affect grade (§10.7)
# ===========================================================================


def test_unsupported_version_grade_unchanged(tmp_path: Path) -> None:
    """A manifest with an unsupported version still produces AGREE if structure matches.
    Gate enforcement is the pipeline's job, not detection's."""
    _write_manifest(
        tmp_path,
        {
            "compatibility_line": "chromadb-0.6.x",
            "chromadb_version": "0.6.99",  # not in SUPPORTED_VERSION_PAIRS
        },
    )
    _make_db(tmp_path, n_collections=1, n_embeddings=1)

    result = detect_palace_format(tmp_path)

    assert result.classification == CHROMA_0_6
    assert result.confidence == 1.0  # AGREE: no cap
    assert result.source_version == "0.6.99"
    assert not any("manifest_vs_structure" in e.detail for e in result.evidence)
