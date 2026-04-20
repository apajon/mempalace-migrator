"""M10 — reconstruction/_manifest.py contract tests.

Covers:
  - written file is valid JSON
  - all required keys present with correct types
  - format_version == 1
  - created_at is ISO-8601 UTC ("Z" suffix)
  - warnings is an empty list
  - returns the Path to the manifest file
  - file lives at target_path / 'reconstruction-target-manifest.json'
  - raises on read-only target (caller handles rollback)
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from mempalace_migrator.reconstruction._manifest import TARGET_MANIFEST_FILENAME, write_target_manifest

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def _call(target_path: Path, **kwargs) -> Path:
    defaults = {
        "target_path": target_path,
        "source_palace_path": Path("/src/palace"),
        "detected_format": "chroma_0_6",
        "source_version": "0.6.3",
        "drawer_count": 7,
        "collection_name": "memory_palace",
        "chromadb_version": "1.5.7",
        "migrator_version": "0.1.0",
    }
    defaults.update(kwargs)
    return write_target_manifest(**defaults)


def test_returns_path_to_manifest(tmp_path: Path) -> None:
    target = tmp_path / "palace_out"
    target.mkdir()
    result = _call(target)
    assert result == target / TARGET_MANIFEST_FILENAME
    assert result.exists()


def test_manifest_constant_name() -> None:
    assert TARGET_MANIFEST_FILENAME == "reconstruction-target-manifest.json"


def test_valid_json(tmp_path: Path) -> None:
    target = tmp_path / "palace_out"
    target.mkdir()
    manifest_path = _call(target)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def test_format_version_is_1(tmp_path: Path) -> None:
    target = tmp_path / "palace_out"
    target.mkdir()
    manifest_path = _call(target)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["format_version"] == 1


def test_required_keys_present(tmp_path: Path) -> None:
    target = tmp_path / "palace_out"
    target.mkdir()
    manifest_path = _call(target)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = {
        "format_version",
        "created_at",
        "source_palace_path",
        "detected_format",
        "source_version",
        "drawer_count",
        "collection_name",
        "chromadb_version",
        "mempalace_migrator_version",
        "warnings",
    }
    assert required.issubset(set(data.keys()))


def test_warnings_is_empty_list(tmp_path: Path) -> None:
    target = tmp_path / "palace_out"
    target.mkdir()
    manifest_path = _call(target)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["warnings"] == []


def test_created_at_is_utc_z(tmp_path: Path) -> None:
    target = tmp_path / "palace_out"
    target.mkdir()
    manifest_path = _call(target)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    ts = data["created_at"]
    assert isinstance(ts, str)
    assert ts.endswith("Z"), f"expected UTC 'Z' suffix, got: {ts!r}"


def test_drawer_count_matches(tmp_path: Path) -> None:
    target = tmp_path / "palace_out"
    target.mkdir()
    manifest_path = _call(target, drawer_count=42)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["drawer_count"] == 42


def test_collection_name_matches(tmp_path: Path) -> None:
    target = tmp_path / "palace_out"
    target.mkdir()
    manifest_path = _call(target, collection_name="test_col")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["collection_name"] == "test_col"


def test_chromadb_version_matches(tmp_path: Path) -> None:
    target = tmp_path / "palace_out"
    target.mkdir()
    manifest_path = _call(target, chromadb_version="1.5.9")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["chromadb_version"] == "1.5.9"


def test_migrator_version_matches(tmp_path: Path) -> None:
    target = tmp_path / "palace_out"
    target.mkdir()
    manifest_path = _call(target, migrator_version="0.2.0")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["mempalace_migrator_version"] == "0.2.0"


def test_source_palace_path_is_string(tmp_path: Path) -> None:
    target = tmp_path / "palace_out"
    target.mkdir()
    manifest_path = _call(target, source_palace_path=Path("/abs/source"))
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["source_palace_path"] == "/abs/source"


# ---------------------------------------------------------------------------
# Read-only directory → raises OSError (caller handles rollback)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="chmod read-only unreliable on Windows")
def test_read_only_target_raises_oserror(tmp_path: Path) -> None:
    target = tmp_path / "readonly_dir"
    target.mkdir()
    target.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(OSError):
            _call(target)
    finally:
        target.chmod(stat.S_IRWXU)
