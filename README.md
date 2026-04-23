# mempalace-migrator

[![CI](https://github.com/apajon/mempalace-migrator/actions/workflows/ci.yml/badge.svg?event=pull_request)](https://github.com/apajon/mempalace-migrator/actions/workflows/ci.yml)

Reconstruction-based migration tool. Reads a MemPalace stored under
ChromaDB `0.6.x` and rebuilds it as a new palace under ChromaDB `1.x`.

Scope is narrow and intentionally constrained: one supported version
pair, one collection name, tested against a small fixture. Semantic
accuracy of the reconstructed palace is not asserted. See section 4
for the exact supported scope.

---

## 1. What this project is

`mempalace-migrator` reads a ChromaDB `0.6.x` SQLite database directly,
extracts the documents and metadata it can identify, and reconstructs a
new ChromaDB `1.x` palace from that extracted material.

It works by **reconstruction**, not by in-place migration:

- The source palace is opened in SQLite read-only URI mode (`mode=ro`)
  and is never modified.
- A new, separate target palace is built from scratch using the public
  ChromaDB `1.x` Python client.
- Extracted records are inserted via the `1.x` client API, which means
  the embedding function used by `1.x` decides how vectors are produced.

There is no shared format between the two versions. The tool does not
upgrade a database; it transcribes what it can read into a new one.

**Current status:** all five pipeline stages are implemented.
`migrate` runs the full pipeline end-to-end and writes a target palace.
`analyze` remains available for read-only inspection without writing.
The supported scope is narrow: one version pair, one collection name.
See section 4.

---

## 2. What this project is NOT

- Not an in-place upgrade tool. The source palace is never modified.
  A partial write to the target is rolled back automatically on pipeline
  failure, but a completed run cannot be undone without deleting the
  target directory manually.
- Not a general migration utility. Scope is hard-coded to the single
  version pair in section 4.
- Not a general ChromaDB conversion utility. It is hard-coded to one
  source structure and one target version.
- Does not commit to producing a target palace that is equivalent to
  the source in any semantic sense.
- Does not commit to preserving every record from the source.
- Does not commit to producing semantically accurate output, even when
  the run exits with code `0`.
- Does not commit to terminating without error on inputs it has not
  been tested against.
- Not a substitute for taking a backup of the source data.

---

## 3. Limitations and risks

Read this section before running the tool.

- **Embedding vectors are not transferred.** ChromaDB `1.x` recomputes
  embeddings using its own embedding function. Search results in the
  reconstructed palace will not be identical to those in the source,
  even when documents are byte-identical.
- **Output may pass structural checks but be semantically inaccurate.**
  The reconstructed palace may load, accept queries, and return results
  that differ from the source in ways the tool cannot detect. Semantic
  accuracy is not asserted and cannot be checked by the tool.
- **Completeness is not assured.** Rows that fail per-row integrity
  checks are excluded from the reconstruction and listed in the report.
  The tool continues; it does not refuse to produce a partial output.
- **The tool checks what it can** (PRAGMA integrity, ID uniqueness,
  document presence, metadata resolvability). It cannot check what it
  does not know to look for.
- **The tool may fail on inputs that other tooling accepts.** Detection
  requires a manifest with a recognised `chromadb_version`. Palaces
  without one are rejected, even if they are otherwise readable.
- **Tested coverage is narrow.** Only the version pair listed in
  section 4 has been exercised. Behaviour on other versions, schemas
  produced by other tooling, or palaces written by patched ChromaDB
  builds is undefined.
- **The tool refuses to run when a non-empty target directory is
  supplied.** The target path must not exist or must be empty; any
  other condition causes the pipeline to abort before writing.
- **Concurrent access to the source is not detected reliably.** The
  tool refuses to run when an uncheckpointed WAL file is present, but
  it cannot detect a concurrent reader-writer outside that signal.
- **Manifest authenticity is not checked.** A forged or stale manifest
  will be accepted.
- **Atomicity has limits.** Any failure after the target directory is
  created triggers a rollback (the partial directory is removed). A
  completed run that exits `0` cannot be undone without deleting the
  target directory manually.
- **`migrate` writes a manifest file.** On success, a file named
  `reconstruction-target-manifest.json` is written inside the target
  directory recording provenance: source path, detected format, drawer
  count, chromadb version, and tool version.
- **Empty-dict metadata is coerced.** Records whose metadata is an
  empty dict are stored with `None` metadata in the target palace,
  because chromadb `1.5.7` rejects empty-dict metadata. The drawer
  count and id set are preserved; no anomaly is emitted for this
  coercion. This is a faithful adaptation to the pinned chromadb
  version, not a data-loss event.
- **Detection evidence is not unified with the anomaly model.**
  Detection uses its own `Evidence` / `Contradiction` model internally.
  Pipeline gate failures are mirrored into `ctx.anomalies` as critical
  anomalies, but the two structured models are not merged into a single
  type.
- **`inspect` exits `0` when reconstruction is skipped.** No target is
  written; the stages section of the report marks `reconstruct` as
  `skipped` with `reason: no_target_path`.

A run that exits with code `0` means the tool completed without raising
a critical error. It does **not** mean the reconstructed palace
accurately represents the source.

---

## 4. Supported scope

The tool refuses to run outside this list.

| Source ChromaDB | Target ChromaDB |
|-----------------|-----------------|
| `0.6.3`         | `>=1.5.7,<2`    |

The dependency pin in `pyproject.toml` is `chromadb>=1.5.7,<2`.
Detection accepts palaces whose manifest lists `chromadb_version`
matching the single source version above (`0.6.3`). Detection also
requires a manifest file (`mempalace-bridge-manifest.json`) in the
source palace directory containing both `compatibility_line` and
`chromadb_version` fields. Without these, the tool aborts before
extraction.

There are no plans for additional version pairs in this repository.
Each new pair requires re-validation against real palaces.

---

## 5. Philosophy

These are the design constraints the codebase is held to. They are
described here so that contributors and users understand why the tool
behaves as it does.

- **Traceability over convenience.** Every excluded row, every
  inconsistency, and every ambiguity is recorded as a structured
  anomaly in the report. The tool does not silently drop data.
- **Explicit reporting over silent success.** Each report contains an
  `explicitly_not_checked` list naming the conditions the tool does
  not check. Silence in the output is not an assurance.
- **Strict boundaries over broad support.** Anything outside the
  supported version pair, or below the required detection confidence,
  is rejected. There is no `--force` option.
- **Read-only by construction.** The source database is opened in
  SQLite `mode=ro`. The target palace is built in a separate location.
  There is no codepath that writes to the source.
- **Failure model is documented, not improvised.** Critical conditions
  raise and abort. Per-row issues are collected and reported. The
  difference is defined in code, not left to the caller.

---

## 6. Quickstart

> **Warning: back up the source palace before running this tool.**
> Even though the source is opened read-only, the surrounding workflow
> (renames, moves, scripted cleanup) is the operator's responsibility.
>
> **Warning: do not point the target path at an existing non-empty
> directory.** The tool refuses to write to a non-empty target. The
> target must not exist or must be an empty directory.
>
> **Warning: inspect the report after every run.** A successful exit
> code is not an assurance of accuracy. Semantic accuracy is not
> asserted.

Install:

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e .
```

Available commands:

```bash
# Read-only: detect format and extract records. No target written.
.venv/bin/mempalace-migrator analyze /path/to/source-palace
.venv/bin/mempalace-migrator analyze /path/to/source-palace --json-output

# Full migration: detect > extract > transform > reconstruct > validate.
# TARGET must not exist or be empty. Partial writes are rolled back on failure.
.venv/bin/mempalace-migrator migrate /path/to/source-palace --target /path/to/new-palace
.venv/bin/mempalace-migrator migrate /path/to/source-palace --target /path/to/new-palace --json-output

# Inspect without writing: detect, extract, transform, validate (no target).
# Parity checks are listed as not-performed (no reconstruction ran).
.venv/bin/mempalace-migrator inspect /path/to/source-palace

# Re-render a JSON report saved from a previous run.
.venv/bin/mempalace-migrator report /path/to/report.json
```

`analyze` detects format and extracts records. No target is written.
Reads SOURCE; writes nothing to disk.

`inspect` runs detection, extraction, transformation, and validation
without writing a target palace. Reconstruction is skipped; parity
checks are listed as not-performed in the report. Exits `0` when no
critical anomaly is present, even when reconstruction is skipped.
Reads SOURCE; writes nothing to disk.

`migrate` runs all five stages and writes a new ChromaDB `1.x` palace at
`--target`. The source palace is never modified. A manifest file
(`reconstruction-target-manifest.json`) is written inside the target
directory on success. See section 3 for limitations that remain even
after a run exits `0`.

`report` re-renders an existing JSON report file as human-readable text.
No pipeline is executed. Reads one file; writes nothing.

---

## 7. CLI reference

### `analyze SOURCE`

Detect format and extract records. Read-only; no writes.

**Reads:** SOURCE directory (SQLite read-only URI mode).
**Writes:** nothing.
**Never touches:** the source palace, any existing target directory.
**Required flags:** none (only the SOURCE positional argument).
**Global flags:** `--json-output`, `--quiet`, `--debug`.
**Report keys populated:** `detection`, `extraction`, `extraction_stats`.
Other keys (`transformation`, `reconstruction`, `validation`) are `null`.
`stages` and `confidence_summary` are always present; they reflect which
stages executed or were not reached.
**Exit codes this command may produce:** 0, 1, 2, 3, 8, 10.

### `inspect SOURCE`

Detect, extract, transform, and validate without writing a target palace.

**Reads:** SOURCE directory (SQLite read-only URI mode).
**Writes:** nothing.
**Never touches:** the source palace, any existing directory.
**Required flags:** none (only the SOURCE positional argument).
**Global flags:** `--json-output`, `--quiet`, `--debug`.
**Report keys populated:** `detection`, `extraction`, `extraction_stats`,
`transformation`, `validation`.
`reconstruction` is `null` (reconstruction is skipped when no target path
is supplied; `stages.reconstruct` records `reason: no_target_path`).
`stages` and `confidence_summary` are always present.
**Exit codes this command may produce:** 0, 1, 2, 3, 4, 7, 8, 10.

### `migrate SOURCE --target TARGET`

Migrate SOURCE palace to a ChromaDB 1.x palace at TARGET.

**Reads:** SOURCE directory (SQLite read-only URI mode).
**Writes:** TARGET directory (new ChromaDB `1.x` palace) plus a manifest
file `TARGET/reconstruction-target-manifest.json`.
**Never touches:** the source palace. If a failure occurs after the
target directory is created, the partial directory is removed (rollback).
**Required flags:** `--target TARGET` (the destination directory; must
not exist or must be empty).
**Global flags:** `--json-output`, `--quiet`, `--debug`.
**Report keys populated:** all keys in the report shape (see section 8).
**Artefacts left on disk:** on exit `0`, the target palace directory and
`TARGET/reconstruction-target-manifest.json`. On any non-zero exit after
the target directory was created, the target directory is removed by
the rollback mechanism.
**Exit codes this command may produce:** 0, 1, 2, 3, 4, 5, 6, 7, 8, 10.

### `report REPORT_FILE`

Re-render a JSON report produced by analyze or inspect as text.

**Reads:** REPORT_FILE (a JSON file produced by a previous `analyze`,
`inspect`, or `migrate` run with `--json-output`, or saved manually).
**Writes:** nothing.
**Never touches:** any palace directory.
**Required flags:** none (only the REPORT_FILE positional argument).
**Global flags:** `--json-output` (re-emits the JSON report unchanged),
`--quiet` (suppresses output; only exit code is produced), `--debug`.
**Exit codes this command may produce:** 0, 1, 8, 9, 10. Exit `9` means
the file could not be read or was not parseable as JSON. Exit codes
`2`-`7` are not reachable from this subcommand; it does not run a
pipeline.

---

## 8. Output and reporting

Each run produces a structured report printed to stdout, or as JSON
with `--json-output`. The report always contains the following top-level
keys:

| Key | Contract |
|-----|----------|
| `schema_version` | Stable integer (`5`). External consumers may pin on this value to detect format changes. |
| `tool_version` | Tool version string from `pyproject.toml`. |
| `supported_version_pairs` | List of `{source, target}` objects reflecting the version pairs this build accepts. |
| `run_id` | UUID4 string. Unique per run; safe for cross-referencing logs and reports. |
| `started_at` | UTC ISO 8601 timestamp (seconds precision, `Z` suffix). |
| `completed_at` | UTC ISO 8601 timestamp (seconds precision, `Z` suffix). Always present, even on failure. |
| `outcome` | `"success"` or `"failure"`. |
| `failure` | `null` on success. On failure: `{stage, code, summary, details}` object. |
| `input` | `{source_path, target_path}`. `target_path` is `null` when no `--target` was supplied. |
| `detection` | Detection result: classification, numeric confidence, source version, evidence list. `null` if detection did not run. |
| `extraction` | Extraction result: collection name, PRAGMA integrity check result, `failed_rows` list (with per-row reason). `null` if extraction did not run. |
| `extraction_stats` | `{total_rows, parsed_rows, failed_rows, parse_rate}`. `null` if extraction did not run. |
| `transformation` | Transformation summary: `{drawer_count, sample_ids, metadata_keys, wing_room_counts, length_profile, dropped_count}`. `null` if transformation did not run. |
| `reconstruction` | Reconstruction summary: `{target_path, collection_name, imported_count, batch_size, chromadb_version, target_manifest_path}`. `null` if reconstruction did not run (e.g. `analyze`, `inspect`). |
| `validation` | Validation result: `{outcome, confidence_band, checks_not_performed, outcomes}`. `null` if validation did not run. |
| `stages` | Per-stage status map: each stage is `executed`, `aborted`, `skipped`, or `not_run`. |
| `confidence_summary` | `{detection_band, extraction_band, overall_band}`. Reflects the weakest confidence band observed across all stages that ran. |
| `anomalies` | List of structured anomaly objects. Each has `type` (registered enum value), `severity` (`low`/`medium`/`high`/`critical`), `location.stage`, `message`, and `evidence` list. Always present; may be empty. |
| `anomaly_summary` | `{by_severity, by_stage, top_severity, total_count}`. Always present. |
| `explicitly_not_checked` | List of condition strings naming checks the tool does not perform. Always present, always non-empty. |

Operators are expected to inspect the report. A non-empty
`failed_rows` list means data was excluded from the reconstruction. A
non-empty `anomalies` list with `severity >= high` means the run
contains conditions that warrant manual review before the output is
trusted for any purpose.

### Exit codes

| Code | Trigger |
|------|---------|
| `0`  | Pipeline completed without raising a critical error; no CRITICAL anomaly recorded |
| `1`  | CLI usage error (bad arguments, missing required path) |
| `2`  | Detection failed (unsupported format, version, or insufficient confidence) |
| `3`  | Extraction failed at a critical pre-flight check (PRAGMA failure, WAL not checkpointed) |
| `4`  | Transformation failed (extracted data missing or transformation raised) |
| `5`  | Reconstruction failed (target path conflict, chromadb write error, or rollback triggered) |
| `6`  | Report-builder pipeline error (`MigratorError` from the report stage) |
| `7`  | Validation raised unexpectedly (`validate()` normally never raises) |
| `8`  | Outcome is `success` but at least one CRITICAL anomaly was recorded ("silent failure" guard) |
| `9`  | `report` subcommand: the specified file could not be read or is not parseable as JSON |
| `10` | Unexpected or unrecognised failure; use `--debug` to surface the traceback |

---

## 9. Target audience

This tool is intended for operators who:

- understand the structural differences between ChromaDB `0.6.x` and
  `1.x`,
- can read SQLite directly to check what the tool reports,
- accept that semantic accuracy of the output is not asserted and that
  the reconstructed palace may need to be discarded after inspection,
- do not need a turnkey upgrade path.

If you need a supported migration product, this is not it.

---

## 10. Guarantees

The following properties are enforced by the test suite. This list is
closed: if a property is absent from this table, the project does not
commit to it.

| Property | Enforced by |
|----------|-------------|
| Source file bytes are unchanged after any subcommand | `tests/test_migrate_e2e.py::test_source_unchanged` |
| Target directory is rolled back (removed) on any failure after `mkdir` | `tests/adversarial/test_reconstruction_rollback.py` |
| Every skipped check carries a `SkippedReason` | `validation/_types.py::SkippedReason` + `tests/test_validation_parity.py` |
| Exit code `0` implies no CRITICAL anomaly in the report | `cli/main.py::_decide_exit_code` + `tests/test_cli_migrate.py`, `tests/test_cli.py` |
| Report `schema_version` is a stable integer (currently `5`) | `reporting/report_builder.py::REPORT_SCHEMA_VERSION` + `tests/adversarial/_invariants.py::check_schema_stability` |
| Detection accepts only the single documented source/target pair | `detection/format_detector.py::SUPPORTED_VERSION_PAIRS` + `tests/test_format_detector_structured_outputs.py` |
| Reconstruction never writes to the source palace | SQLite `mode=ro` URI + `tests/test_cli_migrate.py::test_migrate_source_byte_identical` |
| Every anomaly has a registered `AnomalyType`, a known stage, and at least one evidence entry | `core/context.py::AnomalyType` + `tests/adversarial/_invariants.py::check_anomaly_well_formedness` |

The following are **not** in scope and are not committed to:

- Retrieval-result parity between source and target palaces.
- Usage-scenario parity, MCP-runtime parity, or application-level
  equivalence.
- Embedding-vector numeric equivalence (chromadb `1.x` re-derives
  embeddings; only embedding presence is checked, and only as a
  best-effort `medium`-severity check).
- Semantic accuracy or completeness under corruption.
- Performance on inputs significantly larger than the test fixture.

---

## 11. CI

Every pull request against `main` must pass the `verify` job defined in
`.github/workflows/ci.yml` before merging. The job runs on
`ubuntu-latest` with Python `3.12`, installs the package via
`pip install -e ".[dev]"`, executes the full test suite with `pytest -q`,
checks each subcommand's `--help` exit code, and runs the end-to-end
migration smoke test. No step may proceed if a prior step fails.

Branch protection on `main` requires the `verify` check to pass;
pull requests are not mergeable while the check is absent or red,
except by explicit admin override.

---

## 12. Related projects

- **[mempalace-mcp-bridge](https://github.com/apajon/mempalace-mcp-bridge)**
  — the stable bridge between MemPalace and the Model Context Protocol.
  It is the production-oriented project. `mempalace-migrator` exists
  separately so that experimental reconstruction work does not affect
  the bridge's stability or its supported scope.
