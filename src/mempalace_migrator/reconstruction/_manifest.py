"""Write the target manifest JSON file after a successful reconstruction.

No chromadb import. Pure I/O: reads ctx fields, writes one JSON file.
On any write failure the caller is responsible for triggering rollback.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

TARGET_MANIFEST_FILENAME = "reconstruction-target-manifest.json"


def write_target_manifest(
    *,
    target_path: Path,
    source_palace_path: Path,
    detected_format: str,
    source_version: str | None,
    drawer_count: int,
    collection_name: str,
    chromadb_version: str,
    migrator_version: str,
) -> Path:
    """Write the manifest at ``<target_path>/<TARGET_MANIFEST_FILENAME>``.

    Returns the path to the written file. Raises ``OSError`` on failure
    (the caller wraps this in a rollback + ``ReconstructionError``).
    """
    manifest = {
        "format_version": 1,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_palace_path": str(source_palace_path.resolve()),
        "detected_format": detected_format,
        "source_version": source_version,
        "drawer_count": drawer_count,
        "collection_name": collection_name,
        "chromadb_version": chromadb_version,
        "mempalace_migrator_version": migrator_version,
        "warnings": [],
    }
    manifest_path = target_path / TARGET_MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path
