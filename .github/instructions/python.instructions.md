---
description: "Use when writing or modifying Python source in this repo. Covers Python version, typing, imports, CLI boundaries, and lint/format expectations for mempalace-migrator."
applyTo: "**/*.py"
---

# Python Conventions — mempalace-migrator

Target runtime is **Python 3.12+** (see `pyproject.toml`). Dev tooling is
`pytest`, `ruff`, `mypy`.

## Style & Typing

- Always start modules with `from __future__ import annotations`.
- Type-annotate public functions and dataclass fields. Prefer `|` unions and
  built-in generics (`list[str]`, `dict[str, Any]`) over `typing.List` etc.
- Use `@dataclass(frozen=True)` for value objects (see
  `tests/adversarial/conftest.py::CliResult`, `CorpusEntry`).
- Keep modules focused: one concern per file, matching the package layout
  (`detection/`, `extraction/`, `transformation/`, `reconstruction/`,
  `validation/`, `reporting/`).

## Imports

- Group: stdlib, third-party, first-party (`mempalace_migrator.*`), then
  `tests.*`. Blank line between groups.
- No wildcard imports.
- Import exit codes and shared constants from their canonical module, do not
  redefine them. Production `EXIT_*` constants live in
  `mempalace_migrator.cli.main`; tests re-export them from
  `tests/adversarial/conftest.py` — keep the two in sync.

## CLI & I/O Boundaries

- The CLI entrypoint is `mempalace_migrator.cli.main:main` (Click).
- **stdout is reserved for the JSON report.** Logs, progress, and diagnostics
  go to **stderr**. Never `print()` to stdout from library code.
- Exit codes are part of the public contract — see `EXIT_*` in
  `cli/main.py`. Do not invent new exit codes without updating the
  adversarial corpus and the tests mirror.

## Errors

- Raise typed errors from `mempalace_migrator.core.errors`: `MigratorError`
  (base) and its subclasses `DetectionError`, `ExtractionError`,
  `TransformError`, `ReconstructionError`, `PipelineAbort`. Each carries
  `stage`, `code`, `summary`, `details` — populate all four; never raise the
  base `MigratorError` directly unless nothing more specific fits.
- Do not raise bare `Exception`, `RuntimeError`, or `ValueError` from
  pipeline code — they surface as `EXIT_UNEXPECTED` (10), which is a bug
  signal.
- No `except Exception: pass`. Never swallow errors silently.
- Validate at system boundaries (CLI args, file reads, DB reads). Don't add
  defensive checks for conditions that can't occur internally.

## Purity

- `transformation/` and `reconstruction/` modules are pure — no network, no
  global state, deterministic output for a given input. Preserve this when
  editing; there are dedicated purity tests (`test_transformation_purity.py`,
  `test_reconstruction_purity.py`).
- `detection/`, `extraction/`, `validation/`, and `reporting/` perform I/O
  (filesystem, sqlite, ChromaDB, stdout) and are not constrained to be pure,
  but keep side-effects localized to their entry functions.

## Dependencies

- Runtime deps: only `click` and `chromadb` (pinned `>=1.5.7,<2`). Do not
  add runtime dependencies without explicit approval.
- Dev-only tools go under `[project.optional-dependencies].dev`.

## Tooling

- Format/lint with `ruff`. Type-check with `mypy`.
- Before proposing a change, mentally run `ruff check` and `mypy` on the
  touched files.
