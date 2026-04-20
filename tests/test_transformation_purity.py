"""M9 task 12.8 — transformation purity contract.

Walks every ``.py`` file under ``src/mempalace_migrator/transformation/`` using
the AST and asserts that none of the following modules are imported:

  - chromadb          (I/O layer, must stay in extraction/)
  - sqlite3           (direct DB access)
  - os                (filesystem access)
  - shutil            (filesystem access)
  - tempfile          (filesystem access)
  - pathlib           (filesystem write paths; plain Path used via TYPE_CHECKING is OK)
  - subprocess        (process execution)

The walker does NOT forbid importing Path for type annotations only (guarded
by ``if TYPE_CHECKING``), but *runtime* imports of os / shutil / tempfile /
pathlib are forbidden.

The walker itself is tested against synthetic Python source strings before the
production-code sweep (mutation guard), so any failure always points at a
*production* defect, never a silent walker bug.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TRANSFORM_PKG = Path(__file__).parent.parent / "src" / "mempalace_migrator" / "transformation"

# Modules that must never be imported inside the transformation package.
_FORBIDDEN_MODULES = frozenset(
    {
        "chromadb",
        "sqlite3",
        "os",
        "shutil",
        "tempfile",
        "pathlib",
        "subprocess",
    }
)


# ---------------------------------------------------------------------------
# AST walker
# ---------------------------------------------------------------------------


class _ForbiddenImportFinder(ast.NodeVisitor):
    """Collect import statements for forbidden modules in a single source file."""

    def __init__(self, filepath: Path) -> None:
        self._filepath = filepath
        self.violations: list[str] = []
        # Track whether we are inside an ``if TYPE_CHECKING:`` block.
        self._in_type_checking: int = 0

    def _viol(self, node: ast.AST, module: str) -> None:
        line = getattr(node, "lineno", "?")
        self.violations.append(f"{self._filepath}:{line}: forbidden import of '{module}'")

    # -- TYPE_CHECKING guard -----------------------------------------------

    def visit_If(self, node: ast.If) -> None:
        """Enter/exit ``if TYPE_CHECKING:`` guards."""
        is_tc = False
        test = node.test
        if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
            is_tc = True
        elif isinstance(test, ast.Attribute):
            # typing.TYPE_CHECKING
            if isinstance(test.value, ast.Name) and test.value.id == "typing":
                if test.attr == "TYPE_CHECKING":
                    is_tc = True
        if is_tc:
            self._in_type_checking += 1
            self.generic_visit(node)
            self._in_type_checking -= 1
        else:
            self.generic_visit(node)

    # -- import checks -----------------------------------------------------

    def _check_module_name(self, name: str, node: ast.AST) -> None:
        """Check the top-level module name (or full name) against the blocklist."""
        top = name.split(".")[0]
        if top in _FORBIDDEN_MODULES or name in _FORBIDDEN_MODULES:
            if self._in_type_checking:
                # TYPE_CHECKING-guarded imports are allowed (type annotations only).
                return
            self._viol(node, name)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module_name(alias.name, node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._check_module_name(node.module, node)
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Mutation guard (walker self-test)
# ---------------------------------------------------------------------------


def _check_source(source: str, filename: str = "<test>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    finder = _ForbiddenImportFinder(Path(filename))
    finder.visit(tree)
    return finder.violations


class TestWalkerMutationGuard:
    """The walker MUST catch all listed forbidden imports when present."""

    def test_detects_chromadb_import(self):
        src = "import chromadb\n"
        assert _check_source(src), "walker must flag 'import chromadb'"

    def test_detects_sqlite3_import(self):
        src = "import sqlite3\n"
        assert _check_source(src)

    def test_detects_os_import(self):
        src = "import os\n"
        assert _check_source(src)

    def test_detects_os_path_import(self):
        src = "from os import path\n"
        assert _check_source(src)

    def test_detects_shutil_import(self):
        src = "import shutil\n"
        assert _check_source(src)

    def test_detects_tempfile_import(self):
        src = "import tempfile\n"
        assert _check_source(src)

    def test_detects_pathlib_import(self):
        src = "from pathlib import Path\n"
        assert _check_source(src)

    def test_detects_subprocess_import(self):
        src = "import subprocess\n"
        assert _check_source(src)

    def test_allows_type_checking_guarded_pathlib(self):
        src = (
            "from __future__ import annotations\n"
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from pathlib import Path\n"
        )
        assert _check_source(src) == [], "TYPE_CHECKING guard must be allowed"

    def test_allows_harmless_imports(self):
        src = "import math\nimport re\nfrom dataclasses import dataclass\n"
        assert _check_source(src) == []

    def test_clean_source_returns_no_violations(self):
        src = "x = 1\n"
        assert _check_source(src) == []


# ---------------------------------------------------------------------------
# Production sweep
# ---------------------------------------------------------------------------


def _collect_py_files() -> list[Path]:
    return sorted(_TRANSFORM_PKG.rglob("*.py"))


@pytest.mark.parametrize("py_file", _collect_py_files(), ids=lambda p: p.name)
def test_no_forbidden_imports_in_transformation(py_file: Path) -> None:
    """Every .py file in transformation/ must be free of forbidden imports."""
    source = py_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(py_file))
    finder = _ForbiddenImportFinder(py_file)
    finder.visit(tree)
    assert finder.violations == [], "Forbidden imports found in transformation package:\n" + "\n".join(
        finder.violations
    )
