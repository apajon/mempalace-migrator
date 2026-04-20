"""M10 — reconstruction/_safety.py contract tests.

Covers:
  - absent path → accepted (no exception)
  - existing empty directory → accepted
  - target is a regular file → ReconstructionError(code='target_path_not_directory')
  - target is a non-empty directory → ReconstructionError(code='target_path_not_empty')
  - ensure_target_is_safe NEVER calls mkdir (pure pre-check)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mempalace_migrator.core.errors import ReconstructionError
from mempalace_migrator.reconstruction._safety import ensure_target_is_safe

# ---------------------------------------------------------------------------
# Absent path (no mkdir, safe)
# ---------------------------------------------------------------------------


def test_absent_path_is_safe(tmp_path: Path) -> None:
    target = tmp_path / "new_palace"
    assert not target.exists()
    ensure_target_is_safe(target)  # must not raise
    assert not target.exists()  # must NOT have called mkdir


# ---------------------------------------------------------------------------
# Existing empty directory (safe)
# ---------------------------------------------------------------------------


def test_empty_directory_is_safe(tmp_path: Path) -> None:
    target = tmp_path / "empty_dir"
    target.mkdir()
    ensure_target_is_safe(target)  # must not raise


# ---------------------------------------------------------------------------
# Target is a file → rejected
# ---------------------------------------------------------------------------


def test_file_target_raises_not_directory(tmp_path: Path) -> None:
    target = tmp_path / "a_file"
    target.write_text("content")
    with pytest.raises(ReconstructionError) as exc_info:
        ensure_target_is_safe(target)
    assert exc_info.value.code == "target_path_not_directory"
    assert exc_info.value.stage == "reconstruct"


def test_file_target_anomaly_type_in_message(tmp_path: Path) -> None:
    target = tmp_path / "a_file"
    target.write_text("x")
    with pytest.raises(ReconstructionError) as exc_info:
        ensure_target_is_safe(target)
    assert "file" in exc_info.value.summary.lower() or "directory" in exc_info.value.summary.lower()


# ---------------------------------------------------------------------------
# Target is a non-empty directory → rejected
# ---------------------------------------------------------------------------


def test_non_empty_directory_raises_not_empty(tmp_path: Path) -> None:
    target = tmp_path / "nonempty_dir"
    target.mkdir()
    (target / "child.txt").write_text("data")
    with pytest.raises(ReconstructionError) as exc_info:
        ensure_target_is_safe(target)
    assert exc_info.value.code == "target_path_not_empty"
    assert exc_info.value.stage == "reconstruct"


def test_non_empty_directory_with_subdirectory_rejected(tmp_path: Path) -> None:
    target = tmp_path / "nonempty_dir"
    target.mkdir()
    (target / "sub").mkdir()
    with pytest.raises(ReconstructionError) as exc_info:
        ensure_target_is_safe(target)
    assert exc_info.value.code == "target_path_not_empty"


# ---------------------------------------------------------------------------
# Purity: ensure_target_is_safe never creates the target directory
# ---------------------------------------------------------------------------


def test_does_not_create_absent_path(tmp_path: Path) -> None:
    target = tmp_path / "not_here"
    ensure_target_is_safe(target)
    assert not target.exists()


def test_does_not_create_on_safe_empty_dir(tmp_path: Path) -> None:
    target = tmp_path / "empty"
    target.mkdir()
    child_count_before = len(list(target.iterdir()))
    ensure_target_is_safe(target)
    assert len(list(target.iterdir())) == child_count_before
