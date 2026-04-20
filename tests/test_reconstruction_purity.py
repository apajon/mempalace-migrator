"""M10 — reconstruction purity AST tests.

Asserts:
  - reconstructor.py does NOT import chromadb at module level
  - _writer.py is the only module under reconstruction/ that imports chromadb
  - _safety.py, _manifest.py, _types.py, __init__.py do not import chromadb

Mirrors the pattern of tests/test_transformation_purity.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_RECONSTRUCTION_PKG = Path(__file__).parent.parent / "src" / "mempalace_migrator" / "reconstruction"
_WRITER = _RECONSTRUCTION_PKG / "_writer.py"


# ---------------------------------------------------------------------------
# AST scanner (minimal — only checks module-level imports)
# ---------------------------------------------------------------------------


def _has_module_level_chromadb_import(source: str) -> bool:
    """Return True if the source has a top-level ``import chromadb``
    or ``from chromadb`` statement (not inside a function body).
    """
    tree = ast.parse(source)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "chromadb" or alias.name.startswith("chromadb."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "chromadb" or node.module.startswith("chromadb.")):
                return True
    return False


# ---------------------------------------------------------------------------
# Walker test: self-test with synthetic source
# ---------------------------------------------------------------------------


def test_scanner_detects_top_level_import():
    src = "import chromadb\n\nx = 1\n"
    assert _has_module_level_chromadb_import(src) is True


def test_scanner_detects_top_level_from_import():
    src = "from chromadb.config import Settings\n"
    assert _has_module_level_chromadb_import(src) is True


def test_scanner_ignores_function_level_import():
    src = "def foo():\n    import chromadb\n"
    assert _has_module_level_chromadb_import(src) is False


def test_scanner_ignores_function_level_from_import():
    src = "def foo():\n    from chromadb.config import Settings\n"
    assert _has_module_level_chromadb_import(src) is False


def test_scanner_clean_source():
    src = "x = 1\ndef foo(): pass\n"
    assert _has_module_level_chromadb_import(src) is False


# ---------------------------------------------------------------------------
# Production sweep
# ---------------------------------------------------------------------------


def _py_files():
    return sorted(_RECONSTRUCTION_PKG.glob("*.py"))


def test_only_writer_imports_chromadb_at_module_level():
    """Every .py file in reconstruction/ may import chromadb at module
    level ONLY if it is _writer.py.  All others must be chromadb-free
    at the top level (function-local imports are permitted).
    """
    violations: list[str] = []
    for py_file in _py_files():
        if py_file == _WRITER:
            # _writer.py MUST have a top-level chromadb import.
            src = py_file.read_text(encoding="utf-8")
            if not _has_module_level_chromadb_import(src):
                violations.append(f"{py_file}: _writer.py must import chromadb at module level")
        else:
            src = py_file.read_text(encoding="utf-8")
            if _has_module_level_chromadb_import(src):
                violations.append(f"{py_file}: must NOT import chromadb at module level (only _writer.py may)")
    assert violations == [], "\n".join(violations)


def test_reconstructor_no_chromadb_at_module_level():
    """Focused assertion: reconstructor.py specifically is chromadb-free."""
    reconstructor = _RECONSTRUCTION_PKG / "reconstructor.py"
    src = reconstructor.read_text(encoding="utf-8")
    assert not _has_module_level_chromadb_import(
        src
    ), "reconstructor.py imports chromadb at module level — move it to _writer.py"
