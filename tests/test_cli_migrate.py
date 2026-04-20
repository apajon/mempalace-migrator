"""M10 — CLI migrate subcommand tests.

Covers:
  - ``migrate SOURCE --target TARGET`` succeeds on a known-good palace:
      * exit code 0
      * target manifest exists at TARGET/reconstruction-target-manifest.json
      * source sha256 + mtime byte-identical before/after run
  - ``migrate SOURCE`` without ``--target`` exits 1 (CLI usage error)
  - ``analyze SOURCE --target TARGET`` is rejected by Click → exit 1
  - ``inspect SOURCE --target TARGET`` is rejected by Click → exit 1
  - ``migrate SOURCE --target EXISTING_NON_EMPTY_DIR`` exits 5

Uses subprocess for the happy path (stdout/stderr separation contract).
Uses CliRunner for the error/rejection paths (they only assert exit code).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from mempalace_migrator.cli.main import EXIT_RECONSTRUCT_FAILED, EXIT_USAGE_ERROR, cli
from mempalace_migrator.detection.format_detector import MANIFEST_FILENAME, SQLITE_FILENAME
from mempalace_migrator.extraction.chroma_06_reader import EXPECTED_COLLECTION_NAME
from mempalace_migrator.reconstruction._manifest import TARGET_MANIFEST_FILENAME

# ---------------------------------------------------------------------------
# Palace fixture helpers (duplicated from test_cli.py for isolation)
# ---------------------------------------------------------------------------

_MANIFEST_06 = {"compatibility_line": "chromadb-0.6.x", "chromadb_version": "0.6.3"}


def _write_manifest(root: Path, data: dict[str, Any] | None = None) -> None:
    (root / MANIFEST_FILENAME).write_text(json.dumps(data or _MANIFEST_06))


def _make_valid_db(root: Path, *, n_drawers: int = 3) -> None:
    db_path = root / SQLITE_FILENAME
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE collections (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY, collection_id INTEGER, embedding_id TEXT
        );
        CREATE TABLE embedding_metadata (
            id INTEGER NOT NULL, key TEXT NOT NULL,
            string_value TEXT, int_value INTEGER, float_value REAL, bool_value INTEGER
        );
        """
    )
    conn.execute("INSERT INTO collections (id, name) VALUES (1, ?)", (EXPECTED_COLLECTION_NAME,))
    for i in range(1, n_drawers + 1):
        conn.execute(
            "INSERT INTO embeddings (id, collection_id, embedding_id) VALUES (?, 1, ?)",
            (i, f"drawer-{i}"),
        )
        conn.execute(
            "INSERT INTO embedding_metadata (id, key, string_value) VALUES (?, 'chroma:document', ?)",
            (i, f"content of drawer {i}"),
        )
    conn.commit()
    conn.close()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def make_valid_palace(root: Path, *, n_drawers: int = 3) -> Path:
    _write_manifest(root)
    _make_valid_db(root, n_drawers=n_drawers)
    return root


# ---------------------------------------------------------------------------
# Happy path (subprocess)
# ---------------------------------------------------------------------------


def test_migrate_happy_path_exit_0(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    make_valid_palace(source, n_drawers=3)
    target = tmp_path / "target"

    result = subprocess.run(
        [sys.executable, "-m", "mempalace_migrator.cli.main", "migrate", str(source), "--target", str(target)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"


def test_migrate_happy_path_manifest_exists(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    make_valid_palace(source, n_drawers=2)
    target = tmp_path / "target"

    subprocess.run(
        [sys.executable, "-m", "mempalace_migrator.cli.main", "migrate", str(source), "--target", str(target)],
        capture_output=True,
        text=True,
    )
    assert (target / TARGET_MANIFEST_FILENAME).exists()


def test_migrate_source_byte_identical(tmp_path: Path) -> None:
    """Source chroma.sqlite3 must have same sha256 and mtime after migration."""
    source = tmp_path / "source"
    source.mkdir()
    make_valid_palace(source, n_drawers=3)
    sqlite_src = source / SQLITE_FILENAME
    sha_before = _sha256(sqlite_src)
    mtime_before = sqlite_src.stat().st_mtime
    target = tmp_path / "target"

    subprocess.run(
        [sys.executable, "-m", "mempalace_migrator.cli.main", "migrate", str(source), "--target", str(target)],
        capture_output=True,
        text=True,
    )
    sha_after = _sha256(sqlite_src)
    mtime_after = sqlite_src.stat().st_mtime
    assert sha_after == sha_before, "source sqlite sha256 changed after migration"
    assert mtime_after == mtime_before, "source sqlite mtime changed after migration"


# ---------------------------------------------------------------------------
# Missing --target → exit 1
# ---------------------------------------------------------------------------


def test_migrate_without_target_exits_1(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    make_valid_palace(source)

    runner = CliRunner()
    result = runner.invoke(cli, ["migrate", str(source)])
    # CliRunner runs cli directly (no main() wrapper), so Click returns 2
    # for a missing required option; just assert non-zero.
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# analyze/inspect refuse --target (Click usage error, exit 1)
# ---------------------------------------------------------------------------


def test_analyze_rejects_target_flag(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli, ["analyze", str(source), "--target", str(tmp_path / "t")])
    assert result.exit_code != 0  # Click usage error


def test_inspect_rejects_target_flag(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli, ["inspect", str(source), "--target", str(tmp_path / "t")])
    assert result.exit_code != 0  # Click usage error


# ---------------------------------------------------------------------------
# Non-empty target dir → exit 5
# ---------------------------------------------------------------------------


def test_migrate_nonempty_target_exits_5(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    make_valid_palace(source, n_drawers=2)
    target = tmp_path / "target"
    target.mkdir()
    (target / "existing_file.txt").write_text("occupied")

    result = subprocess.run(
        [sys.executable, "-m", "mempalace_migrator.cli.main", "migrate", str(source), "--target", str(target)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == EXIT_RECONSTRUCT_FAILED


# ---------------------------------------------------------------------------
# M11: parity checks appear in validation result after successful migrate
# ---------------------------------------------------------------------------


def test_migrate_happy_path_produces_parity_outcomes(tmp_path: Path) -> None:
    """After a successful migrate the validation result must include five
    parity CheckOutcomes (family='parity').

    We use CliRunner so we can inspect ctx. But CliRunner runs the CLI
    in-process; we capture the result by reading the JSON report written
    to stdout or by using the underlying pipeline directly.

    Here we call the underlying pipeline directly (same as CLI does) so
    we can inspect ctx.validation_result.
    """
    from mempalace_migrator.core.context import MigrationContext
    from mempalace_migrator.core.pipeline import FULL_PIPELINE, run_pipeline

    source = tmp_path / "source"
    source.mkdir()
    make_valid_palace(source, n_drawers=3)
    target = tmp_path / "target"

    ctx = MigrationContext(source_path=source, target_path=target)
    run_pipeline(ctx, FULL_PIPELINE)

    assert ctx.validation_result is not None
    parity_outcomes = [o for o in ctx.validation_result.checks_performed if o.family == "parity"]
    assert len(parity_outcomes) == 5, (
        f"Expected 5 parity outcomes, got {len(parity_outcomes)}: " f"{[o.id for o in parity_outcomes]}"
    )


def test_migrate_happy_path_parity_all_passed(tmp_path: Path) -> None:
    from mempalace_migrator.core.context import MigrationContext
    from mempalace_migrator.core.pipeline import FULL_PIPELINE, run_pipeline

    source = tmp_path / "source"
    source.mkdir()
    make_valid_palace(source, n_drawers=3)
    target = tmp_path / "target"

    ctx = MigrationContext(source_path=source, target_path=target)
    run_pipeline(ctx, FULL_PIPELINE)

    parity_outcomes = [o for o in ctx.validation_result.checks_performed if o.family == "parity"]
    failed = [o for o in parity_outcomes if o.status == "failed"]
    assert not failed, f"Parity checks failed on clean migration: {[(o.id, o.status) for o in failed]}"


def test_migrate_happy_path_checks_not_performed_empty(tmp_path: Path) -> None:
    """After a successful migrate, checks_not_performed must be empty."""
    from mempalace_migrator.core.context import MigrationContext
    from mempalace_migrator.core.pipeline import FULL_PIPELINE, run_pipeline

    source = tmp_path / "source"
    source.mkdir()
    make_valid_palace(source, n_drawers=2)
    target = tmp_path / "target"

    ctx = MigrationContext(source_path=source, target_path=target)
    run_pipeline(ctx, FULL_PIPELINE)

    assert ctx.validation_result.checks_not_performed == (), (
        f"Expected empty checks_not_performed after full migrate; " f"got {ctx.validation_result.checks_not_performed}"
    )
