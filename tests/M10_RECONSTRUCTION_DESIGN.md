# M10 — Reconstruction Stage (design)

Status: **design recorded, implementation pending**.
Owner phase: `TODO.json` `phases[13]`.
Roadmap milestone: `ROADMAP.json` `M10`.
Predecessors satisfied: M1–M9 (672 tests green; `step_transform`
produces a typed `TransformedBundle`).

This document fixes the strategy for implementing `step_reconstruct`
so that review and implementation can proceed in lockstep. M10 is the
**only writer** the migrator owns. It does not add target-side validation
(deferred to M11), nor any new exit code, severity, or stage. It does add
exactly one new CLI subcommand (`migrate`), one new dependency
(`chromadb>=1.5.7,<2`), one new pipeline tuple (`MIGRATE_PIPELINE`), and
one new `MigratorError` consumer (`ReconstructionError` already exists in
`core/errors.py` since M3 — M10 simply starts raising it).

---

## 1. Scope (what M10 lands)

In scope:

1. New module `src/mempalace_migrator/reconstruction/` populated with
   `_safety.py`, `_writer.py`, `_manifest.py`, `_types.py`, `reconstructor.py`,
   `__init__.py`. The existing `__init__.py` stub is replaced.
2. `chromadb>=1.5.7,<2` added to `pyproject.toml [project] dependencies`.
   No optional-dependency split: reconstruction is a first-class capability,
   not an extra.
3. `ctx.reconstruction_result` becomes a `ReconstructionResult` frozen
   dataclass instead of `Any = None`.
4. `step_reconstruct` calls `reconstruction.reconstruct(ctx)` when
   `ctx.target_path` is set; otherwise records the stage as
   **skipped** (reason `no_target_path`). The current
   `NOT_IMPLEMENTED/LOW` anomaly emission is removed.
5. New closed-registry `AnomalyType` members (§4.2).
6. New `MIGRATE_PIPELINE` and `PIPELINES["migrate"]` registration.
7. New `migrate SOURCE --target TARGET` Click subcommand.
8. `report["reconstruction"]` returns a real dict instead of hard-coded
   `None`. `text_renderer.render_text` gains a `Reconstruction:` block.
9. A `target_manifest` JSON file written at `target_path /
   reconstruction-target-manifest.json` mirroring the prototype schema.
10. Atomic-failure contract: on any exception after `target_path.mkdir`,
    the partial directory is removed; source palace is byte-identical
    before/after the run.
11. Tests covering every contract in §5.

Not in scope (carried forward to later milestones, do **not** silently
add):

- **No target-side validation** (count parity, id parity, document hash,
  metadata, embeddings). M11 owns those. `_SKIPPED_RECONSTRUCTION` in
  `validation/__init__.py` and `EXPLICITLY_NOT_CHECKED` in
  `report_builder.py` are **not** modified by M10. M11 will lift them.
- **No `--force` / `--overwrite` flag.** Refusal to overwrite an
  existing non-empty target is a hard contract. The user's only escape
  is to delete the target directory themselves.
- **No retry / resume logic.** Failure is atomic and total: rollback,
  then exit 5. Resuming a partially-built target is explicitly out of
  scope per ROADMAP non-goals.
- **No bundle serialisation to disk** (the prototype's `drawers.jsonl`
  is out of scope per the ROADMAP non-goal "No bundle serialisation").
- **No new exit code.** `EXIT_RECONSTRUCT_FAILED = 5` is already
  reserved by `cli/main.py` since M6.4.
- **No new severity.** Anomalies use `CRITICAL` (raise → exit 5),
  `HIGH` (write failure pre-rollback), and `MEDIUM` (manifest write
  warning). `LOW` is reserved for the `skipped` path (no anomaly emitted
  there at all — skip is recorded in `stage_results`, not as an anomaly).
- **No write to source.** Source remains opened read-only by extraction.
  `reconstruction/` must not import `extraction.chroma_06_reader` for
  any I/O purpose; it consumes only `ctx.transformed_data`.
- **No adversarial / hardening corpus extension.** M12 owns that. M10
  ships its own unit tests + one happy-path integration test only.

---

## 2. Module layout

```
src/mempalace_migrator/reconstruction/
    __init__.py        # re-exports: reconstruct, ReconstructionResult,
                       # TARGET_MANIFEST_FILENAME
    _types.py          # frozen ReconstructionResult dataclass
    _safety.py         # ensure_target_is_safe(target_path) — pure, no I/O writes
    _writer.py         # _open_client / _create_collection / _add_in_batches
                       #   — the only place chromadb is imported
    _manifest.py       # write_target_manifest(...) — single JSON write
    reconstructor.py   # reconstruct(ctx) — orchestrates safety → mkdir →
                       #   write → manifest → ReconstructionResult; rollback
                       #   on any failure between mkdir and manifest
```

Splitting `_writer.py` (chromadb-only) from `reconstructor.py`
(orchestration, no chromadb import) lets us mock the writer surface
cleanly in unit tests **without** importing chromadb in the test suite.
`reconstructor.py` imports `_writer` lazily (function-local) so that the
purity AST checks can later (M11) assert the validation layer stays
chromadb-free without false positives in `reconstructor.py`. Module-level
chromadb import is allowed only in `_writer.py` and `_manifest.py` is
not allowed to import chromadb.

Rationale for keeping `_safety.py` separate: `ensure_target_is_safe`
must be callable **before** any state mutation, and unit-testable
without a chromadb install.

---

## 3. Data model

### 3.1 `ReconstructionResult` (`_types.py`)

```python
@dataclass(frozen=True)
class ReconstructionResult:
    target_path: Path
    collection_name: str
    imported_count: int
    batch_size: int
    chromadb_version: str
    target_manifest_path: Path
```

All fields required. `imported_count` equals
`len(ctx.transformed_data.drawers)` on success — there is no partial
success state (a partial success triggers rollback and raises).
`chromadb_version` is captured via `chromadb.__version__` at write time
(not pinned at import time) so the report records what actually wrote.

`ctx.reconstruction_result: ReconstructionResult | None` replaces
`Any = None` in `core/context.py`.

### 3.2 `target_manifest` schema

File: `<target_path>/reconstruction-target-manifest.json`.
Constant: `TARGET_MANIFEST_FILENAME = "reconstruction-target-manifest.json"`
exported from `reconstruction/__init__.py`.

```json
{
  "format_version": 1,
  "created_at": "2026-04-20T12:34:56Z",
  "source_palace_path": "/abs/path/to/source",
  "detected_format": "chroma_0_6",
  "source_version": "0.6.x",
  "drawer_count": 42,
  "collection_name": "memory_palace",
  "chromadb_version": "1.5.7",
  "mempalace_migrator_version": "0.1.0",
  "warnings": []
}
```

Mirrors the prototype's `target_manifest` (see
`mempalace-mcp-bridge:scripts/palace_reconstruction_prototype.py
::write_target_manifest`). `format_version` is a separate integer
distinct from `report.schema_version`. `warnings` is a list of strings
populated only when manifest-write encounters a non-fatal issue (today:
empty list always; reserved for forward compatibility).

---

## 4. Contracts

### 4.1 Pipeline contract

| Condition                                   | Outcome                                                                |
| ------------------------------------------- | ---------------------------------------------------------------------- |
| `ctx.target_path is None`                   | `step_reconstruct` records `stage_results["reconstruct"] = "skipped"` with reason `no_target_path`. **No anomaly emitted.** No write. |
| `ctx.target_path` set, `ctx.transformed_data is None` | `step_reconstruct` emits `RECONSTRUCTION_INPUT_MISSING/CRITICAL` and raises `ReconstructionError(stage="reconstruct", code="reconstruction_input_missing", ...)` → exit 5. No mkdir. |
| Target exists as a file                     | Emit `TARGET_PATH_NOT_DIRECTORY/CRITICAL`, raise → exit 5. No mkdir.    |
| Target exists as non-empty directory        | Emit `TARGET_PATH_NOT_EMPTY/CRITICAL`, raise → exit 5. No mkdir.        |
| Target absent or empty directory            | Proceed: `mkdir(parents=True, exist_ok=True)` → write → manifest → store result. |
| Any exception after `mkdir`                 | Emit `RECONSTRUCTION_ROLLBACK/HIGH` carrying the original failure code as evidence; `shutil.rmtree(target_path, ignore_errors=False)`; raise `ReconstructionError` whose `code` reflects the originating phase (`chromadb_client_failed`, `chromadb_collection_create_failed`, `chromadb_batch_insert_failed`, `target_manifest_write_failed`). Exit 5. |
| Success                                     | `ctx.reconstruction_result = ReconstructionResult(...)`. No anomaly emitted. `stage_results["reconstruct"] = "executed"`. |

### 4.2 New `AnomalyType` members

Added under a new `# --- Reconstruction ---` section in
`core/context.py`:

```
RECONSTRUCTION_INPUT_MISSING       = "reconstruction_input_missing"
TARGET_PATH_NOT_DIRECTORY          = "target_path_not_directory"
TARGET_PATH_NOT_EMPTY              = "target_path_not_empty"
CHROMADB_CLIENT_FAILED             = "chromadb_client_failed"
CHROMADB_COLLECTION_CREATE_FAILED  = "chromadb_collection_create_failed"
CHROMADB_BATCH_INSERT_FAILED       = "chromadb_batch_insert_failed"
TARGET_MANIFEST_WRITE_FAILED       = "target_manifest_write_failed"
RECONSTRUCTION_ROLLBACK            = "reconstruction_rollback"
```

Closed-registry contract preserved (free-form anomaly types remain
rejected by `_coerce_type`). Each member must be exercised by at least
one test in §5.

### 4.3 Batch contract

- `BATCH_SIZE = 500` (matches the prototype). Single module-level
  constant in `_writer.py`. No CLI override.
- `collection.add(ids=[...], documents=[...], metadatas=[...])` only.
  **No `embeddings=` kwarg** — chromadb 1.x re-derives embeddings from
  documents on insert (per ROADMAP non-goal #4 on embedding
  re-computation: we explicitly do not pass through 0.6.x stored
  embedding bytes). This is documented in the manifest's
  `warnings: []` field (reserved for a future "embeddings re-derived"
  notice) and in `text_renderer`.
- Batch failure raises `_BatchInsertError` (private, internal) carrying
  the failing batch index and the chromadb exception. The orchestrator
  catches it, emits `CHROMADB_BATCH_INSERT_FAILED/CRITICAL` with the
  batch index + first/last id of the batch as evidence, then triggers
  rollback.

### 4.4 Atomicity contract (the central invariant)

> After any failure in `step_reconstruct`, `ctx.target_path` has the
> **same on-disk state** as before the run.

- "Same state" means: if the path did not exist before, it does not
  exist after. If it existed and was empty, it exists and is empty.
- Implementation: `_did_create = not target_path.exists()` captured
  **before** `mkdir`. On rollback, if `_did_create` is True call
  `shutil.rmtree(target_path)`; otherwise iterate the directory and
  remove only the children created by the run (chromadb's sqlite file +
  hnsw segments + `reconstruction-target-manifest.json`). The simpler
  "rmtree only when we created the dir" path is preferred; the
  caller-created-empty-dir case is handled by listing and removing
  exactly what we wrote.
- Source-side invariant: `sha256(source_palace/chroma.sqlite3)` and the
  file `mtime` are byte-identical pre/post run. Asserted by an
  integration test (§5.7).

### 4.5 Pure-orchestrator contract

`reconstructor.py` does **not** import chromadb at module level. The
import lives in `_writer.py`. This is asserted by an AST test
mirroring `tests/test_transformation_purity.py`, restricted to
`reconstructor.py` (not the whole package).

---

## 5. Test plan (M10)

All tests live under `tests/`. No new subdirectory. Each test file
maps to one design concern.

| File | Target | Asserts |
| ---- | ------ | ------- |
| `tests/test_reconstruction_safety.py` | `_safety.ensure_target_is_safe` | path-absent OK, empty-dir OK, file → `TARGET_PATH_NOT_DIRECTORY`, non-empty dir → `TARGET_PATH_NOT_EMPTY`. Pure: no mkdir performed. |
| `tests/test_reconstruction_manifest.py` | `_manifest.write_target_manifest` | schema version 1, ISO-Z timestamp, all required keys, JSON-loadable. Failure when target_path is read-only raises (caller handles rollback). |
| `tests/test_reconstruction_writer.py` | `_writer` (with chromadb) | round-trips a 3-drawer bundle; `collection.count()` equals input; ids preserved; metadata preserved verbatim; `BATCH_SIZE` honoured (3-batch test with `BATCH_SIZE=2` monkeypatched). |
| `tests/test_reconstruction_atomicity.py` | `reconstruct` rollback | monkeypatches `_writer._add_in_batches` to raise on batch index 1; asserts `target_path` does not exist post-call (when caller created it via reconstruct), `RECONSTRUCTION_ROLLBACK` anomaly present, `ReconstructionError(code="chromadb_batch_insert_failed")` raised, `ctx.reconstruction_result is None`. |
| `tests/test_reconstruction_pipeline.py` | `step_reconstruct` | (a) `target_path=None` → skipped, no anomaly, no write; (b) `transformed_data=None` → `RECONSTRUCTION_INPUT_MISSING/CRITICAL` + raise; (c) success path → `ctx.reconstruction_result` populated, manifest exists, `stage_results["reconstruct"] == "executed"`. |
| `tests/test_reconstruction_purity.py` | `reconstructor.py` AST | no `import chromadb` at module level; chromadb is imported only by `_writer.py`. |
| `tests/test_cli_migrate.py` | `migrate` subcommand (subprocess) | `migrate SOURCE --target TARGET` succeeds on a known-good fixture; exit 0; target manifest exists; source byte-identical (sha256 + mtime). `analyze SOURCE --target TARGET` and `inspect SOURCE --target TARGET` are rejected by Click as usage errors (exit 1). `migrate SOURCE` without `--target` exits 1. |
| `tests/test_report_builder.py` (extend) | `report["reconstruction"]` | populated dict when `ctx.reconstruction_result` set; `None` otherwise. Top-level keys list unchanged. |
| `tests/test_report_text.py` (extend) | `render_text` | renders a `Reconstruction:` block when present; absent block when null. |

**No CliRunner-based test for the migrate happy path.** Per the M7/M8
caveat: `CliRunner` cannot assert stdout/stderr separation under Click
8.2+. The migrate happy-path test uses `subprocess`. The `--target`
rejection tests for `analyze` / `inspect` may use CliRunner because
they only assert exit code, not stream separation.

---

## 6. Failure-mode catalogue

| # | Failure mode | Detection point | Outcome | Anomaly | Exit |
|---|--------------|-----------------|---------|---------|------|
| 1 | `target_path` is a file | `_safety` (pre-mkdir) | refuse | `TARGET_PATH_NOT_DIRECTORY/CRITICAL` | 5 |
| 2 | `target_path` is non-empty dir | `_safety` (pre-mkdir) | refuse | `TARGET_PATH_NOT_EMPTY/CRITICAL` | 5 |
| 3 | `target_path` parent is read-only / mkdir fails | `mkdir` raises `PermissionError` / `OSError` | refuse, no rollback (nothing was created) | `CHROMADB_CLIENT_FAILED/CRITICAL` is **wrong** here — use a dedicated `target_path_mkdir_failed` code on the `ReconstructionError` but reuse `TARGET_PATH_NOT_DIRECTORY/CRITICAL` is also wrong. **Decision:** mkdir failure raises `ReconstructionError(code="target_path_mkdir_failed")` and emits `TARGET_PATH_NOT_DIRECTORY/CRITICAL` with evidence `kind="os_error"` carrying the `errno`. No new anomaly type added; the existing one covers "we cannot use this path as a target directory". | `TARGET_PATH_NOT_DIRECTORY/CRITICAL` | 5 |
| 4 | `chromadb.PersistentClient(...)` raises | `_writer._open_client` | rollback | `CHROMADB_CLIENT_FAILED/CRITICAL` + `RECONSTRUCTION_ROLLBACK/HIGH` | 5 |
| 5 | `client.create_collection(...)` raises | `_writer._create_collection` | rollback | `CHROMADB_COLLECTION_CREATE_FAILED/CRITICAL` + `RECONSTRUCTION_ROLLBACK/HIGH` | 5 |
| 6 | `collection.add(...)` raises on batch N | `_writer._add_in_batches` | rollback | `CHROMADB_BATCH_INSERT_FAILED/CRITICAL` (with batch index, first/last id) + `RECONSTRUCTION_ROLLBACK/HIGH` | 5 |
| 7 | manifest write fails (disk full, EIO) | `_manifest.write_target_manifest` | rollback (chromadb files written but not yet committed-to-user) | `TARGET_MANIFEST_WRITE_FAILED/CRITICAL` + `RECONSTRUCTION_ROLLBACK/HIGH` | 5 |
| 8 | rollback itself fails (rmtree raises) | `_rollback_target` | re-raise rollback exception **chained** to original | both anomalies emitted; rollback exception added to `ReconstructionError.details` | 5 |
| 9 | `ctx.transformed_data` is `None` | `step_reconstruct` entry | refuse, no mkdir | `RECONSTRUCTION_INPUT_MISSING/CRITICAL` | 5 |
| 10 | empty bundle (`drawers == ()`) | `step_reconstruct` entry | **refuse**: emit `RECONSTRUCTION_INPUT_MISSING/CRITICAL` with evidence "drawer_count=0", raise. M10 does not silently produce an empty target. | `RECONSTRUCTION_INPUT_MISSING/CRITICAL` | 5 |

Failure mode #3 is the only place where the design folds two
distinct error situations into one `AnomalyType` for closed-registry
hygiene. The discriminator lives in the `evidence.kind` field, not in
a new enum member.

Failure mode #10 is a deliberate hard-fail. Producing an empty
ChromaDB collection on disk is indistinguishable from "I intentionally
migrated zero drawers"; the exit-loud rule applies.

Failure mode #8 (rollback-of-rollback) is **not** assertable in M10
without injecting filesystem chaos; covered by M12 adversarial
fixtures, not M10. M10 ships the code path (chained exception) but no
test exercises it.

---

## 7. CLI surface

New subcommand only; no flag added to existing subcommands.

```
mempalace-migrator migrate SOURCE --target TARGET [--debug] [--json-output] [--quiet]
```

- `SOURCE`: `click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path)` — same as `analyze` / `inspect`.
- `--target`: `click.Path(exists=False, file_okay=False, dir_okay=True, path_type=Path)` — required. **Note:** `exists=False` allows both absent and present directories; the safety check in `_safety.ensure_target_is_safe` is the authoritative gate.
- Pipeline: `MIGRATE_PIPELINE = (step_detect, step_extract, step_transform, step_reconstruct, step_validate)`.
- Registered in `PIPELINES["migrate"]`.

`analyze` and `inspect` remain unchanged. They do **not** accept
`--target`; passing it is a Click usage error (exit 1). This is
enforced naturally by Click (the option is not declared on those
commands), not by a custom check. A test pins the behaviour.

`_decide_exit_code` is **not** modified. The existing
`_EXIT_BY_STAGE["reconstruct"] = EXIT_RECONSTRUCT_FAILED` mapping
already handles the new raise path.

---

## 8. Reporting + rendering

### 8.1 `report["reconstruction"]`

```python
{
    "target_path": str,
    "collection_name": str,
    "imported_count": int,
    "batch_size": int,
    "chromadb_version": str,
    "target_manifest_path": str,
}
```

Returns this dict when `ctx.reconstruction_result is not None`,
otherwise returns `None`. Implementation lives in
`report_builder._reconstruction_section(ctx)` mirroring the existing
`_transformation_section`. The hard-coded `"reconstruction": None` line
in `build_report` is replaced with `"reconstruction": _reconstruction_section(ctx)`.

`REPORT_TOP_LEVEL_KEYS` is **not** changed (the key already exists).
`REPORT_SCHEMA_VERSION` bumps from 4 → 5 because the `reconstruction`
field changes from "always None" to "structured-or-None". Hardening
baselines must be regenerated (see §10).

`EXPLICITLY_NOT_CHECKED` is **not** changed by M10. M11 will remove
`target_record_count_parity` and `target_id_set_parity` once it lands.

### 8.2 `text_renderer.render_text`

Adds a section after "Transformation:":

```
Reconstruction:
  target:           /abs/target
  collection:       memory_palace
  imported drawers: 42
  batch size:       500
  chromadb version: 1.5.7
  manifest:         /abs/target/reconstruction-target-manifest.json
```

Section omitted when `report["reconstruction"] is None`.

---

## 9. Validation interaction (deliberately minimal)

Validation continues to consume `ctx.extracted_data` only. M10 does
**not** add target-side validation checks. The `_SKIPPED_RECONSTRUCTION`
tuple in `validation/__init__.py` is unchanged; the two parity checks
remain skipped with reason `stage_not_implemented`. This is
intentionally honest: until M11 lifts them, a successful M10
reconstruction is **not** validated against its source. The text
renderer's existing "validation output is advisory" warning continues
to apply.

A test (`tests/test_validation.py`, existing
`test_skipped_checks_reason_is_stage_not_implemented`) must continue to
pass unchanged. If it fails after M10 lands, that means M10 leaked into
M11's territory.

---

## 10. Hardening baselines

`tests/hardening/baselines/runtime_envelope.json` and
`baselines/report_signatures.json` change because:

1. `inspect` runs against the M9 corpus now produce one fewer
   `not_implemented` anomaly (the `reconstruct` stub anomaly is gone;
   the stage is recorded as `skipped/no_target_path` instead).
2. `report.schema_version` changes from 4 → 5.
3. The `reconstruction` field can now be a dict (only on successful
   migrate runs — no migrate run currently lives in the M8 corpus, so
   this only matters once M12 adds one).

M10 regenerates the baselines via `tests/hardening/rebaseline.py` in
the same commit as the implementation, with the diff limited to the
two changes above. Adding a `migrate` entry to the corpus is **M12's
job**, not M10's.

---

## 11. ChromaDB dependency hygiene

- Pin: `chromadb>=1.5.7,<2`. Lower bound matches the prototype's
  validated version. Upper bound prevents an unannounced major bump
  from breaking the writer contract.
- Import location: `_writer.py` only. Module-level `import chromadb`
  in any other module under `src/mempalace_migrator/` is a regression.
  The purity AST test in `tests/test_transformation_purity.py` already
  asserts this for `transformation/`; M10 adds the same check for
  `reconstruction/reconstructor.py` (see §5 row `test_reconstruction_purity.py`).
- Telemetry: `chromadb.PersistentClient` is opened with
  `Settings(anonymized_telemetry=False, allow_reset=False)` to make the
  writer hermetic and reproducible. No environment variable mutation.
- Embedding function: chromadb 1.x defaults to its bundled embedding
  function when documents are inserted without explicit embeddings.
  M10 accepts that default; no override. This is the documented "we
  re-derive embeddings on insert" choice from the ROADMAP non-goals.

---

## 12. Exit-gate checklist (what M10 done means)

A reviewer must verify **every** item before marking phase 13 done.

1. `pyproject.toml` has `chromadb>=1.5.7,<2` in `[project] dependencies`.
2. `src/mempalace_migrator/reconstruction/` matches §2 layout; old
   placeholder `__init__.py` replaced.
3. `_safety.ensure_target_is_safe` rejects file targets and non-empty
   directories with the correct anomaly types and never calls `mkdir`.
4. `ReconstructionResult` (frozen dataclass per §3.1) stored on
   `ctx.reconstruction_result`; the `Any = None` annotation in
   `core/context.py` is replaced with `ReconstructionResult | None`.
5. `step_reconstruct` no longer emits `NOT_IMPLEMENTED/LOW`.
   Behaviour matches §4.1 table for all six rows.
6. All eight new `AnomalyType` members from §4.2 are added to the
   closed registry and each is hit by at least one test.
7. Atomicity test (§5 row `test_reconstruction_atomicity.py`) passes:
   mid-batch failure leaves `target_path` absent (when reconstruct
   created it), source byte-identical (sha256 + mtime).
8. `target_manifest` JSON written at
   `<target>/reconstruction-target-manifest.json` with the §3.2 schema
   and `format_version: 1`.
9. New `migrate SOURCE --target TARGET` subcommand registered;
   `MIGRATE_PIPELINE` includes all five steps;
   `PIPELINES["migrate"]` reachable.
10. `analyze` and `inspect` continue to refuse `--target` (Click usage
    error, exit 1), pinned by test.
11. `report["reconstruction"]` is a dict when reconstruction ran, else
    `None`. `REPORT_SCHEMA_VERSION == 5`. Top-level keys list
    unchanged.
12. `text_renderer.render_text` renders a `Reconstruction:` block when
    populated, omits it otherwise.
13. `_writer.py` is the **only** module under
    `src/mempalace_migrator/` that imports `chromadb` at module level.
    Asserted by AST test.
14. Hardening baselines regenerated; diff limited to the three
    documented changes in §10.
15. Existing test `test_skipped_checks_reason_is_stage_not_implemented`
    still passes unchanged (M10 has not leaked into M11).
16. Full suite green. `EXIT_RECONSTRUCT_FAILED = 5` is reachable
    (failure-mode catalogue rows 1, 2, 4, 5, 6, 7, 9, 10 all
    asserted).

If any item is unverified, status remains `partial`, not `done`.

---

## 13. Explicitly missing (forwarded to later milestones)

- Target-side parity validation (record count, id set, document hash,
  metadata, embedding presence) — **M11**.
- Adversarial fixtures (read-only fs target, mid-batch failure injection
  in the corpus, mtime-invariant assertion in the corpus, empty bundle
  injection) — **M12**.
- `migrate` entry in the hardening baseline corpus — **M12**.
- `--force` / `--overwrite` / `--resume` flags — **out of scope, no
  milestone owns them**. Adding any of these requires a roadmap
  amendment.
- Source palace verification that the on-disk file is byte-identical
  before vs after every CLI command (not just `migrate`) — out of
  scope; M10 only proves it for `migrate`. M12 may broaden.
