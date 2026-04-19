"""Format detection.

The detector returns a DetectionResult with:
  - classification: chroma_0_6 | chroma_1_x | unknown
  - confidence: float in [0.0, 1.0]
  - evidence: list of Evidence facts (source, kind, detail)

Hard rules:
  - Manifest is the only source that can produce confidence >= 0.9.
  - Structural inspection alone NEVER produces a chroma_1_x classification.
  - Manifest contradicting structure downgrades confidence and emits an
    inconsistency Evidence entry.
  - Missing source path / missing DB / unreadable DB are all explicit
    classifications, not silent unknowns.

Strict scope: SUPPORTED_VERSION_PAIRS lists the (source_version,
target_version) pairs this tool is willing to handle. The version is
extracted from manifest.chromadb_version. Anything else is unsupported.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CHROMA_0_6 = "chroma_0_6"
CHROMA_1_X = "chroma_1_x"
UNKNOWN = "unknown"

MANIFEST_FILENAME = "mempalace-bridge-manifest.json"
SQLITE_FILENAME = "chroma.sqlite3"
TYPED_MARKER = "config_json"  # presence of typed config column => 1.x

# Strict scope. (source_version, target_version).
SUPPORTED_VERSION_PAIRS: tuple[tuple[str, str], ...] = (
    ("0.6.3", "1.5.7"),
)

# Confidence floor for the pipeline to accept the detection.
MIN_ACCEPT_CONFIDENCE = 0.9

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class Evidence:
    source: str          # 'manifest' | 'structure' | 'filesystem'
    kind: str            # 'fact' | 'inconsistency' | 'missing'
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "kind": self.kind, "detail": self.detail}


@dataclass(frozen=True)
class DetectionResult:
    palace_path: str
    classification: str
    confidence: float
    source_version: str | None
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "palace_path": self.palace_path,
            "classification": self.classification,
            "confidence": round(self.confidence, 3),
            "source_version": self.source_version,
            "evidence": [e.to_dict() for e in self.evidence],
        }

    def is_supported_pair(self) -> bool:
        if self.source_version is None:
            return False
        return any(src == self.source_version for src, _ in SUPPORTED_VERSION_PAIRS)


# --- Public entry ----------------------------------------------------------


def detect_palace_format(palace_path: Path) -> DetectionResult:
    palace_path = Path(palace_path)
    evidence: list[Evidence] = []

    if not palace_path.exists():
        evidence.append(Evidence("filesystem", "missing", f"path does not exist: {palace_path}"))
        return DetectionResult(str(palace_path), UNKNOWN, 0.0, None, tuple(evidence))

    if not palace_path.is_dir():
        evidence.append(Evidence("filesystem", "fact", f"path is not a directory: {palace_path}"))
        return DetectionResult(str(palace_path), UNKNOWN, 0.0, None, tuple(evidence))

    manifest_class, manifest_conf, manifest_version = _classify_from_manifest(
        palace_path, evidence
    )
    structural_class, structural_conf = _classify_from_structure(palace_path, evidence)

    # Reconcile.
    classification = manifest_class if manifest_class != UNKNOWN else structural_class
    confidence = manifest_conf if manifest_class != UNKNOWN else structural_conf

    if (
        manifest_class != UNKNOWN
        and structural_class != UNKNOWN
        and manifest_class != structural_class
    ):
        evidence.append(
            Evidence(
                "structure",
                "inconsistency",
                f"manifest says {manifest_class} but structure suggests {structural_class}",
            )
        )
        # Manifest still wins for classification; confidence is downgraded
        # because the source is internally inconsistent.
        confidence = min(confidence, 0.6)

    return DetectionResult(
        palace_path=str(palace_path),
        classification=classification,
        confidence=confidence,
        source_version=manifest_version,
        evidence=tuple(evidence),
    )


# --- Manifest --------------------------------------------------------------


def _classify_from_manifest(
    palace_path: Path, evidence: list[Evidence]
) -> tuple[str, float, str | None]:
    manifest_path = palace_path / MANIFEST_FILENAME
    if not manifest_path.is_file():
        evidence.append(Evidence("manifest", "missing", f"{MANIFEST_FILENAME} not present"))
        return UNKNOWN, 0.0, None

    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        evidence.append(Evidence("manifest", "fact", f"cannot read manifest: {exc!r}"))
        return UNKNOWN, 0.0, None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        evidence.append(
            Evidence("manifest", "inconsistency", f"manifest is not valid JSON: {exc!r}")
        )
        return UNKNOWN, 0.0, None

    if not isinstance(data, dict):
        evidence.append(
            Evidence("manifest", "inconsistency", "manifest top-level is not an object")
        )
        return UNKNOWN, 0.0, None

    line = data.get("compatibility_line")
    version = data.get("chromadb_version")

    line_class = _line_to_class(line) if isinstance(line, str) else UNKNOWN
    version_class = _version_to_class(version) if isinstance(version, str) else UNKNOWN

    if line is not None:
        evidence.append(Evidence("manifest", "fact", f"compatibility_line={line!r}"))
    else:
        evidence.append(Evidence("manifest", "missing", "compatibility_line not set"))

    if version is not None:
        evidence.append(Evidence("manifest", "fact", f"chromadb_version={version!r}"))
    else:
        evidence.append(Evidence("manifest", "missing", "chromadb_version not set"))

    # Conflicting fields inside the manifest itself.
    if line_class != UNKNOWN and version_class != UNKNOWN and line_class != version_class:
        evidence.append(
            Evidence(
                "manifest",
                "inconsistency",
                f"compatibility_line ({line_class}) conflicts with version ({version_class})",
            )
        )
        return UNKNOWN, 0.4, version if isinstance(version, str) else None

    # Pick the most specific signal.
    classification = (
        version_class if version_class != UNKNOWN else line_class
    )
    if classification == UNKNOWN:
        return UNKNOWN, 0.2, None

    # Confidence: full match needs both line+version coherent and version_class set.
    if version_class != UNKNOWN and line_class == version_class:
        confidence = 1.0
    elif version_class != UNKNOWN:
        confidence = 0.95
    else:
        # Only line, no version: cannot be used for the version-pair gate.
        confidence = 0.7

    return classification, confidence, version if isinstance(version, str) else None


def _line_to_class(line: str) -> str:
    line = line.strip().lower()
    if line == "chromadb-0.6.x":
        return CHROMA_0_6
    if line == "chromadb-1.x":
        return CHROMA_1_X
    return UNKNOWN


def _version_to_class(version: str) -> str:
    m = _VERSION_RE.match(version.strip())
    if not m:
        return UNKNOWN
    major, minor, _patch = (int(g) for g in m.groups())
    if major == 0 and minor == 6:
        return CHROMA_0_6
    if major >= 1:
        return CHROMA_1_X
    return UNKNOWN


# --- Structure -------------------------------------------------------------


def _classify_from_structure(
    palace_path: Path, evidence: list[Evidence]
) -> tuple[str, float]:
    db_path = palace_path / SQLITE_FILENAME
    if not db_path.is_file():
        evidence.append(
            Evidence("structure", "missing", f"{SQLITE_FILENAME} not present")
        )
        return UNKNOWN, 0.0

    if db_path.stat().st_size == 0:
        evidence.append(Evidence("structure", "fact", f"{SQLITE_FILENAME} is empty"))
        return UNKNOWN, 0.05

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        evidence.append(
            Evidence("structure", "fact", f"cannot open {SQLITE_FILENAME}: {exc!r}")
        )
        return UNKNOWN, 0.0

    try:
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        except sqlite3.DatabaseError as exc:
            evidence.append(
                Evidence("structure", "inconsistency", f"sqlite_master unreadable: {exc!r}")
            )
            return UNKNOWN, 0.0

        required_06 = {"collections", "embeddings", "embedding_metadata"}
        missing = required_06 - tables
        if missing:
            evidence.append(
                Evidence(
                    "structure",
                    "missing",
                    f"required 0.6 tables missing: {sorted(missing)}",
                )
            )
            # If it has segments etc. but not embeddings, it's clearly not 0.6.
            return UNKNOWN, 0.1

        evidence.append(
            Evidence("structure", "fact", "0.6 tables present: collections, embeddings, embedding_metadata")
        )

        # Check collections columns. config_json column is the 1.x marker.
        try:
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(collections)").fetchall()
            }
        except sqlite3.DatabaseError as exc:
            evidence.append(
                Evidence("structure", "fact", f"collections schema unreadable: {exc!r}")
            )
            return UNKNOWN, 0.1

        has_typed = any(c.startswith(TYPED_MARKER) for c in cols)
        if has_typed:
            evidence.append(
                Evidence(
                    "structure",
                    "fact",
                    f"typed config column present in collections: {sorted(c for c in cols if c.startswith(TYPED_MARKER))}",
                )
            )
            # HEURISTIC: typed config column appears in 1.x, but we refuse
            # to commit to a 1.x classification on structure alone.
            return UNKNOWN, 0.3

        # Check there is at least one row. Empty schema is suspicious.
        try:
            n_collections = conn.execute(
                "SELECT COUNT(*) FROM collections"
            ).fetchone()[0]
            n_embeddings = conn.execute(
                "SELECT COUNT(*) FROM embeddings"
            ).fetchone()[0]
        except sqlite3.DatabaseError as exc:
            evidence.append(
                Evidence("structure", "fact", f"row count failed: {exc!r}")
            )
            return CHROMA_0_6, 0.4

        evidence.append(
            Evidence(
                "structure",
                "fact",
                f"collections rows={n_collections} embeddings rows={n_embeddings}",
            )
        )

        if n_collections == 0 and n_embeddings == 0:
            return CHROMA_0_6, 0.45
        if n_collections == 0 or n_embeddings == 0:
            evidence.append(
                Evidence(
                    "structure",
                    "inconsistency",
                    "collections/embeddings row counts are inconsistent (one is empty)",
                )
            )
            return CHROMA_0_6, 0.5

        return CHROMA_0_6, 0.6
    finally:
        conn.close()
