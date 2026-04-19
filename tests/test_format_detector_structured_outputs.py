"""M1 exit-gate tests for structured detection outputs.

Covers the additions made for M1 — Detection Reliability:
  * `confidence_band` (LOW / MEDIUM / HIGH) is exposed and consistent
    with the numeric `confidence` and the pipeline gate.
  * `contradictions` is a structured first-class field whose entries
    are emitted whenever manifest and structure disagree (or when one
    actively disproves the other).
  * `unknowns` aggregates every "missing" signal so consumers do not
    have to filter the evidence list themselves.
  * `to_dict()` exposes the new fields.

These tests are intentionally narrow: they only verify the structured
output contract added for M1, not the underlying grading logic
(already covered by ``test_format_detector_contradictions``).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mempalace_migrator.detection.format_detector import (
    BAND_HIGH_MIN,
    BAND_MEDIUM_MIN,
    CAP_BENIGN,
    CAP_HARD,
    CAP_SEVERE,
    CAP_SOFT,
    CHROMA_0_6,
    CHROMA_1_X,
    MANIFEST_FILENAME,
    MIN_ACCEPT_CONFIDENCE,
    SQLITE_FILENAME,
    UNKNOWN,
    Contradiction,
    detect_palace_format,
)

# ---------------------------------------------------------------------------
# Fixture helpers (kept local so this file is self-contained)
# ---------------------------------------------------------------------------


_MANIFEST_06 = {"compatibility_line": "chromadb-0.6.x", "chromadb_version": "0.6.3"}
_MANIFEST_1X = {"compatibility_line": "chromadb-1.x", "chromadb_version": "1.5.7"}


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
    db_path = root / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    if skip_tables:
        conn.execute("PRAGMA user_version = 1")
    else:
        if typed_marker:
            conn.execute("CREATE TABLE collections (id INTEGER PRIMARY KEY, config_json TEXT)")
        else:
            conn.execute("CREATE TABLE collections (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE embeddings (id INTEGER PRIMARY KEY, collection_id INTEGER)")
        conn.execute("CREATE TABLE embedding_metadata (id INTEGER PRIMARY KEY)")
        if typed_marker:
            for i in range(n_collections):
                conn.execute("INSERT INTO collections (id, config_json) VALUES (?, ?)", (i, "{}"))
        else:
            for i in range(n_collections):
                conn.execute("INSERT INTO collections (id, name) VALUES (?, ?)", (i, f"col{i}"))
        for i in range(n_embeddings):
            conn.execute("INSERT INTO embeddings (id, collection_id) VALUES (?, ?)", (i, 0))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# confidence_band
# ---------------------------------------------------------------------------


def test_confidence_band_high_when_manifest_and_structure_agree(tmp_path: Path) -> None:
    _write_manifest(tmp_path, _MANIFEST_06)
    _make_db(tmp_path, n_collections=1, n_embeddings=1)

    result = detect_palace_format(tmp_path)

    assert result.confidence >= BAND_HIGH_MIN
    assert result.confidence_band == "HIGH"
    # HIGH must coincide with pipeline acceptance.
    assert result.confidence >= MIN_ACCEPT_CONFIDENCE


def test_confidence_band_medium_for_hard_contradiction(tmp_path: Path) -> None:
    """HARD grade caps at CAP_HARD (0.60), which lands in MEDIUM band."""
    _write_manifest(tmp_path, _MANIFEST_1X)
    _make_db(tmp_path, n_collections=1, n_embeddings=1)

    result = detect_palace_format(tmp_path)

    assert result.confidence == pytest.approx(CAP_HARD)
    assert BAND_MEDIUM_MIN <= result.confidence < BAND_HIGH_MIN
    assert result.confidence_band == "MEDIUM"


def test_confidence_band_low_for_severe_contradiction(tmp_path: Path) -> None:
    """SEVERE grade caps at CAP_SEVERE (0.40), which lands in LOW band."""
    _write_manifest(tmp_path, _MANIFEST_06)
    (tmp_path / SQLITE_FILENAME).write_bytes(b"")

    result = detect_palace_format(tmp_path)

    assert result.confidence == pytest.approx(CAP_SEVERE)
    assert result.confidence < BAND_MEDIUM_MIN
    assert result.confidence_band == "LOW"


def test_confidence_band_low_when_path_missing(tmp_path: Path) -> None:
    result = detect_palace_format(tmp_path / "does-not-exist")

    assert result.classification == UNKNOWN
    assert result.confidence == 0.0
    assert result.confidence_band == "LOW"


# ---------------------------------------------------------------------------
# contradictions (structured)
# ---------------------------------------------------------------------------


def test_contradictions_empty_when_signals_agree(tmp_path: Path) -> None:
    _write_manifest(tmp_path, _MANIFEST_06)
    _make_db(tmp_path, n_collections=1, n_embeddings=1)

    result = detect_palace_format(tmp_path)

    assert result.contradictions == ()


def test_contradictions_empty_when_only_structural_signal(tmp_path: Path) -> None:
    """Structure alone never produces a contradiction — there is nothing
    to disagree with."""
    _make_db(tmp_path, typed_marker=True, n_collections=1, n_embeddings=1)

    result = detect_palace_format(tmp_path)

    assert result.classification == UNKNOWN
    assert result.contradictions == ()


def test_contradictions_benign_when_db_missing(tmp_path: Path) -> None:
    _write_manifest(tmp_path, _MANIFEST_06)

    result = detect_palace_format(tmp_path)

    assert result.confidence == pytest.approx(CAP_BENIGN)
    assert len(result.contradictions) == 1
    c = result.contradictions[0]
    assert isinstance(c, Contradiction)
    assert c.grade == "BENIGN"
    assert c.manifest_class == CHROMA_0_6
    assert c.structural_class == UNKNOWN


def test_contradictions_soft_for_inconsistent_row_counts(tmp_path: Path) -> None:
    _write_manifest(tmp_path, _MANIFEST_06)
    _make_db(tmp_path, n_collections=3, n_embeddings=0)

    result = detect_palace_format(tmp_path)

    assert result.confidence == pytest.approx(CAP_SOFT)
    assert [c.grade for c in result.contradictions] == ["SOFT"]
    assert result.contradictions[0].reason == "row_counts_inconsistent"


def test_contradictions_hard_typed_marker(tmp_path: Path) -> None:
    _write_manifest(tmp_path, _MANIFEST_06)
    _make_db(tmp_path, typed_marker=True, n_collections=1, n_embeddings=1)

    result = detect_palace_format(tmp_path)

    assert [c.grade for c in result.contradictions] == ["HARD"]
    assert result.contradictions[0].reason == "typed_marker_present"
    assert result.contradictions[0].manifest_class == CHROMA_0_6
    # Classification not flipped (R3): structural still recorded as 0.6 schema
    assert result.classification == CHROMA_0_6


def test_contradictions_hard_class_clash(tmp_path: Path) -> None:
    _write_manifest(tmp_path, _MANIFEST_1X)
    _make_db(tmp_path, n_collections=1, n_embeddings=1)

    result = detect_palace_format(tmp_path)

    assert [c.grade for c in result.contradictions] == ["HARD"]
    assert result.contradictions[0].manifest_class == CHROMA_1_X
    assert result.contradictions[0].structural_class == CHROMA_0_6


def test_contradictions_severe_when_required_tables_missing(tmp_path: Path) -> None:
    _write_manifest(tmp_path, _MANIFEST_06)
    _make_db(tmp_path, skip_tables=True)

    result = detect_palace_format(tmp_path)

    assert result.classification == UNKNOWN  # SEVERE flips
    assert [c.grade for c in result.contradictions] == ["SEVERE"]
    assert result.contradictions[0].reason == "required_tables_missing"


def test_contradictions_severe_when_db_empty(tmp_path: Path) -> None:
    _write_manifest(tmp_path, _MANIFEST_06)
    (tmp_path / SQLITE_FILENAME).write_bytes(b"")

    result = detect_palace_format(tmp_path)

    assert result.classification == UNKNOWN
    assert [c.grade for c in result.contradictions] == ["SEVERE"]
    assert result.contradictions[0].reason == "db_empty"


# ---------------------------------------------------------------------------
# unknowns
# ---------------------------------------------------------------------------


def test_unknowns_lists_missing_path(tmp_path: Path) -> None:
    result = detect_palace_format(tmp_path / "nope")

    assert any("filesystem" in u for u in result.unknowns)


def test_unknowns_lists_missing_manifest_and_db(tmp_path: Path) -> None:
    """Empty directory: manifest and DB both missing → both surfaced."""
    result = detect_palace_format(tmp_path)

    sources = {u.split(":", 1)[0] for u in result.unknowns}
    assert "manifest" in sources
    assert "structure" in sources


def test_unknowns_empty_when_signals_complete(tmp_path: Path) -> None:
    _write_manifest(tmp_path, _MANIFEST_06)
    _make_db(tmp_path, n_collections=1, n_embeddings=1)

    result = detect_palace_format(tmp_path)

    # No "missing" evidence is produced when both signals are coherent
    # and complete.
    assert result.unknowns == ()


# ---------------------------------------------------------------------------
# to_dict() contract
# ---------------------------------------------------------------------------


def test_to_dict_exposes_new_fields(tmp_path: Path) -> None:
    _write_manifest(tmp_path, _MANIFEST_06)
    _make_db(tmp_path, typed_marker=True, n_collections=1, n_embeddings=1)

    payload = detect_palace_format(tmp_path).to_dict()

    # Required keys must be present and JSON-serialisable.
    for key in ("confidence_band", "contradictions", "unknowns", "evidence"):
        assert key in payload

    json.dumps(payload)  # must not raise

    assert payload["confidence_band"] in {"LOW", "MEDIUM", "HIGH"}
    assert isinstance(payload["contradictions"], list)
    assert isinstance(payload["unknowns"], list)
    # The HARD contradiction structure round-trips.
    assert payload["contradictions"][0]["grade"] == "HARD"
    assert "reason" in payload["contradictions"][0]


# ---------------------------------------------------------------------------
# Intra-manifest contradiction (MANIFEST_INTERNAL grade)
# ---------------------------------------------------------------------------


def test_intra_manifest_conflict_classification_and_confidence(tmp_path: Path) -> None:
    """Manifest with line=0.6.x but version=1.5.7 must:
    - return classification=UNKNOWN (no positive identification),
    - return confidence=0.4 (documented intra-manifest cap),
    - record the source_version observed in the manifest (R2: only the
      manifest can set source_version, and it did set it — even though
      the manifest is internally inconsistent).
    """
    _write_manifest(
        tmp_path,
        {"compatibility_line": "chromadb-0.6.x", "chromadb_version": "1.5.7"},
    )

    result = detect_palace_format(tmp_path)

    assert result.classification == UNKNOWN
    assert result.confidence == pytest.approx(0.4)
    assert result.source_version == "1.5.7"
    assert result.confidence_band == "LOW"


def test_intra_manifest_conflict_emits_inconsistency_evidence(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        {"compatibility_line": "chromadb-0.6.x", "chromadb_version": "1.5.7"},
    )

    result = detect_palace_format(tmp_path)

    assert any(
        e.source == "manifest" and e.kind == "inconsistency" for e in result.evidence
    ), "intra-manifest conflict must emit a manifest/inconsistency evidence entry"


def test_intra_manifest_conflict_surfaced_in_contradictions(tmp_path: Path) -> None:
    """A consumer that only reads `result.contradictions` must still see
    the intra-manifest conflict. This guards the docstring contract:
    `contradictions == ()` means no contradiction at all."""
    _write_manifest(
        tmp_path,
        {"compatibility_line": "chromadb-0.6.x", "chromadb_version": "1.5.7"},
    )

    result = detect_palace_format(tmp_path)

    assert result.contradictions != (), "intra-manifest conflict must populate contradictions"
    grades = [c.grade for c in result.contradictions]
    assert "MANIFEST_INTERNAL" in grades
    intra = next(c for c in result.contradictions if c.grade == "MANIFEST_INTERNAL")
    assert intra.reason == "line_vs_version"
    # Field naming is repurposed for MANIFEST_INTERNAL — see Contradiction
    # docstring. Both sides must be present and non-UNKNOWN.
    assert intra.manifest_class == CHROMA_0_6
    assert intra.structural_class == CHROMA_1_X


def test_intra_manifest_conflict_to_dict_round_trips(tmp_path: Path) -> None:
    _write_manifest(
        tmp_path,
        {"compatibility_line": "chromadb-0.6.x", "chromadb_version": "1.5.7"},
    )

    payload = detect_palace_format(tmp_path).to_dict()

    json.dumps(payload)  # must remain JSON-safe
    assert payload["classification"] == UNKNOWN
    assert payload["confidence_band"] == "LOW"
    assert any(c["grade"] == "MANIFEST_INTERNAL" for c in payload["contradictions"])


def test_intra_manifest_conflict_distinguishable_from_no_signal(tmp_path: Path) -> None:
    """A consumer must be able to tell 'no signal at all' (empty dir,
    confidence 0.0) apart from 'manifest is internally inconsistent'
    (confidence 0.4 with a MANIFEST_INTERNAL contradiction) without
    parsing evidence strings."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    no_signal = detect_palace_format(empty_dir)

    conflict_dir = tmp_path / "conflict"
    conflict_dir.mkdir()
    _write_manifest(
        conflict_dir,
        {"compatibility_line": "chromadb-0.6.x", "chromadb_version": "1.5.7"},
    )
    intra = detect_palace_format(conflict_dir)

    # Both share classification UNKNOWN and the LOW band.
    assert no_signal.classification == intra.classification == UNKNOWN
    assert no_signal.confidence_band == intra.confidence_band == "LOW"
    # But contradictions distinguish them unambiguously.
    assert no_signal.contradictions == ()
    assert any(c.grade == "MANIFEST_INTERNAL" for c in intra.contradictions)
