"""M8 task 11.1 — logging discipline.

Walks every ``.py`` file under ``src/mempalace_migrator/`` using the AST and
asserts that:

  A. No ``print(...)`` call sites exist anywhere.
  B. The ``logging`` module is never imported and none of its callables are used.
  C. ``sys.stdout.write`` / ``sys.stderr.write`` are never called directly.
  D. ``click.echo(...)`` is used **only** inside ``cli/main.py``.

The walker itself is tested against synthetic Python source strings before
the production-code sweep, so a failing invariant always points at a
*production* defect and never at a silent walker bug.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "mempalace_migrator"
_CLI_MAIN = _SRC_ROOT / "cli" / "main.py"


# ---------------------------------------------------------------------------
# AST walker
# ---------------------------------------------------------------------------


def _get_attr_chain_root(node: ast.expr) -> str | None:
    """Return the outermost Name id in an attribute chain, e.g. ``sys.stdout.write`` → ``"sys"``."""
    while isinstance(node, ast.Attribute):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    return None


class _ForbiddenCallFinder(ast.NodeVisitor):
    """Visit a module AST and collect forbidden call-site descriptions."""

    def __init__(self, filepath: Path, *, is_cli_main: bool) -> None:
        self._filepath = filepath
        self._is_cli_main = is_cli_main
        self.violations: list[str] = []
        # Names in this module that reference the ``logging`` module/its callables.
        self._logging_names: set[str] = set()
        # Names in this module that reference the ``sys`` module.
        self._sys_names: set[str] = {"sys"}
        # Names in this module that reference the ``click`` module.
        self._click_names: set[str] = set()
        # Names imported directly from ``click`` that are ``echo``.
        self._echo_direct_names: set[str] = set()

    def _viol(self, node: ast.AST, message: str) -> None:
        line = getattr(node, "lineno", "?")
        self.violations.append(f"{self._filepath}:{line}: {message}")

    # -- import tracking ---------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name
            if alias.name == "logging" or alias.name.startswith("logging."):
                self._logging_names.add(name)
            elif alias.name == "sys":
                self._sys_names.add(name)
            elif alias.name == "click":
                self._click_names.add(name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module == "logging" or module.startswith("logging."):
            for alias in node.names:
                self._logging_names.add(alias.asname or alias.name)
        elif module == "click":
            for alias in node.names:
                if alias.name == "echo":
                    self._echo_direct_names.add(alias.asname or alias.name)
        self.generic_visit(node)

    # -- call detection ----------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func

        # A. print()
        if isinstance(func, ast.Name) and func.id == "print":
            self._viol(node, "print() call")

        # B. Direct call to a name imported from logging (e.g. ``getLogger()``)
        elif isinstance(func, ast.Name) and func.id in self._logging_names:
            self._viol(node, f"logging call: {func.id}()")

        elif isinstance(func, ast.Attribute):
            root = _get_attr_chain_root(func)

            # B. logging.X() — any call whose chain root is a logging alias
            if root and root in self._logging_names:
                self._viol(node, f"logging call via {root}.{func.attr}()")

            # C. sys.stdout.write() / sys.stderr.write()
            if (
                func.attr == "write"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr in ("stdout", "stderr")
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id in self._sys_names
            ):
                self._viol(node, f"sys.{func.value.attr}.write() call")

            # D. click.echo() outside cli/main.py
            if (
                not self._is_cli_main
                and func.attr == "echo"
                and isinstance(func.value, ast.Name)
                and func.value.id in self._click_names
            ):
                self._viol(node, "click.echo() outside cli/main.py")

        # D (direct import). echo() when imported directly from click
        elif isinstance(func, ast.Name) and func.id in self._echo_direct_names and not self._is_cli_main:
            self._viol(node, f"{func.id}() outside cli/main.py (imported from click)")

        self.generic_visit(node)


def walk_file(filepath: Path) -> list[str]:
    """Parse *filepath* and return a list of violation descriptions (empty = clean)."""
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filepath))
    is_cli_main = filepath.resolve() == _CLI_MAIN.resolve()
    finder = _ForbiddenCallFinder(filepath, is_cli_main=is_cli_main)
    finder.visit(tree)
    return finder.violations


def collect_source_files(root: Path) -> list[Path]:
    """Return sorted list of all ``.py`` files under *root*."""
    return sorted(root.rglob("*.py"))


# ---------------------------------------------------------------------------
# Walker unit tests (against synthetic source, not production code)
# ---------------------------------------------------------------------------


def _walk_source(source: str, *, filename: str = "<test>", is_cli_main: bool = False) -> list[str]:
    """Helper: parse *source* string and run the walker."""
    source = textwrap.dedent(source)
    tree = ast.parse(source, filename=filename)
    # Use a fake path so _CLI_MAIN check works via is_cli_main kwarg
    finder = _ForbiddenCallFinder(Path(filename), is_cli_main=is_cli_main)
    finder.visit(tree)
    return finder.violations


class TestWalkerDetectsForbiddenPatterns:
    """The walker must flag every forbidden call-site category."""

    def test_detects_print_call(self):
        source = "print('hello')"
        assert any("print()" in v for v in _walk_source(source))

    def test_detects_logging_import_and_call(self):
        source = """
        import logging
        logging.warning("oops")
        """
        viols = _walk_source(source)
        assert any("logging" in v for v in viols)

    def test_detects_from_logging_import(self):
        source = """
        from logging import getLogger
        log = getLogger(__name__)
        log.info("msg")
        """
        viols = _walk_source(source)
        assert any("logging" in v for v in viols)

    def test_detects_logging_alias(self):
        source = """
        import logging as _log
        _log.error("bad")
        """
        viols = _walk_source(source)
        assert any("_log" in v for v in viols)

    def test_detects_sys_stdout_write(self):
        source = """
        import sys
        sys.stdout.write("msg")
        """
        viols = _walk_source(source)
        assert any("sys.stdout.write" in v for v in viols)

    def test_detects_sys_stderr_write(self):
        source = """
        import sys
        sys.stderr.write("err")
        """
        viols = _walk_source(source)
        assert any("sys.stderr.write" in v for v in viols)

    def test_detects_click_echo_outside_cli_main(self):
        source = """
        import click
        click.echo("hi")
        """
        viols = _walk_source(source, is_cli_main=False)
        assert any("click.echo" in v for v in viols)

    def test_detects_direct_echo_outside_cli_main(self):
        source = """
        from click import echo
        echo("hi")
        """
        viols = _walk_source(source, is_cli_main=False)
        assert any("echo()" in v for v in viols)


class TestWalkerPermitsAllowedPatterns:
    """The walker must NOT flag legitimate code."""

    def test_allows_click_echo_in_cli_main(self):
        source = """
        import click
        click.echo("output")
        """
        viols = _walk_source(source, is_cli_main=True)
        assert not viols

    def test_allows_direct_echo_in_cli_main(self):
        source = """
        from click import echo
        echo("result")
        """
        viols = _walk_source(source, is_cli_main=True)
        assert not viols

    def test_allows_clean_module(self):
        source = """
        from pathlib import Path
        import json

        def load(p: Path) -> dict:
            return json.loads(p.read_text())
        """
        assert not _walk_source(source)

    def test_word_print_in_string_is_safe(self):
        source = 'x = "print this"'
        assert not _walk_source(source)

    def test_method_named_print_on_custom_obj_is_safe(self):
        # obj.print() — not a bare print() call
        source = """
        class Printer:
            def print(self): pass
        p = Printer()
        p.print()
        """
        assert not _walk_source(source)

    def test_sys_stdout_read_is_safe(self):
        source = """
        import sys
        data = sys.stdout.read()
        """
        assert not _walk_source(source)


# ---------------------------------------------------------------------------
# Production-code sweep (the actual 11.1 exit gate assertion)
# ---------------------------------------------------------------------------


def test_no_forbidden_call_sites_in_production_code():
    """Walk every .py file under src/mempalace_migrator/ and assert zero violations."""
    all_violations: list[str] = []
    source_files = collect_source_files(_SRC_ROOT)
    assert source_files, f"No .py files found under {_SRC_ROOT}"
    for filepath in source_files:
        all_violations.extend(walk_file(filepath))
    assert (
        not all_violations
    ), f"Forbidden call sites found in production code ({len(all_violations)} violation(s)):\n" + "\n".join(
        f"  {v}" for v in all_violations
    )


def test_sweep_covers_transformation_and_reconstruction_subtrees():
    """M12 §4.7 — the sweep must include files from transformation/ and reconstruction/.

    This guards against a future refactor that moves those subtrees to a path
    not under _SRC_ROOT, which would silently exclude them from the discipline
    check.  One file from each subtree is sufficient proof of coverage.
    """
    source_files = collect_source_files(_SRC_ROOT)
    paths = [str(p) for p in source_files]

    transformation_files = [p for p in paths if "/transformation/" in p]
    assert transformation_files, (
        f"No transformation/ files found in the logging-discipline sweep.\n"
        f"_SRC_ROOT={_SRC_ROOT}\n"
        f"Confirm transformation/ is still a subdirectory of src/mempalace_migrator/."
    )

    reconstruction_files = [p for p in paths if "/reconstruction/" in p]
    assert reconstruction_files, (
        f"No reconstruction/ files found in the logging-discipline sweep.\n"
        f"_SRC_ROOT={_SRC_ROOT}\n"
        f"Confirm reconstruction/ is still a subdirectory of src/mempalace_migrator/."
    )
