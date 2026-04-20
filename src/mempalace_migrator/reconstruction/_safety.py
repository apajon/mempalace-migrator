"""Pre-write safety check for the reconstruction target path.

Pure function: performs only read-only filesystem inspection. No mkdir,
no writes, no chromadb import. Callable before any state mutation.
"""

from __future__ import annotations

from pathlib import Path

from mempalace_migrator.core.errors import ReconstructionError


def ensure_target_is_safe(target_path: Path) -> None:
    """Raise ``ReconstructionError`` if *target_path* cannot be used as a
    write target.

    Allowed states:
      - path does not exist
      - path is an existing **empty** directory

    Rejected states:
      - path is a file  → code ``target_path_not_directory``
      - path is a non-empty directory → code ``target_path_not_empty``

    Does **not** call ``mkdir``. The caller is responsible for creating the
    directory after this check passes.
    """
    if not target_path.exists():
        return

    if not target_path.is_dir():
        raise ReconstructionError(
            stage="reconstruct",
            code="target_path_not_directory",
            summary=f"target path is a file, not a directory: {target_path}",
            details=[f"path={target_path}"],
        )

    # Non-empty directory check: iterate at most one entry to avoid scanning
    # large trees unnecessarily.
    try:
        next(iter(target_path.iterdir()))
    except StopIteration:
        # Empty directory — allowed.
        return

    raise ReconstructionError(
        stage="reconstruct",
        code="target_path_not_empty",
        summary=f"target directory already exists and is not empty: {target_path}",
        details=[
            f"path={target_path}",
            "delete the target directory manually before retrying",
        ],
    )
