# M12 — Write-Path Adversarial + Hardening: Implementation Strategy

Status: **design only** (no new production code, no new tests merged yet).
Scope: phase 15 in `tests/TODO.json` (`15.1 .. 15.11`).
Predecessors satisfied: M1–M11 exit gates (see `ROADMAP.json::current_position` →
`M11_done`, 775 tests green).
Successor blocked: M13 (End-to-End Migration Usability Gate, phase 16) cannot
start until M12 exit gate is green.

This document fixes the *shape* of M12 work. It is intentionally narrow:
M12 adds **no features**, **no new pipeline stages**, **no new exit
codes**, **no new severities**, **no new `AnomalyType` values unless an
adversarial input proves one is missing**, **no new CLI flags**, **no
new subcommands**. M12's job is to prove — by construction — that the
write path (transform → reconstruct → parity) already implemented in
M9–M11 *fails loudly, atomically, and structurally* on hostile input,
and that the full `migrate` command fits inside a recorded runtime
envelope.

M12 is to the write path what M7+M8 jointly are to the read path.
It deliberately reuses the M7 corpus/invariant framework and the M8
baseline/rebaseline framework instead of inventing parallel
infrastructure.

---

## 1. M12 in one sentence

> Extend the M7 adversarial corpus and the M8 baseline envelope so that
> every transform/reconstruct/parity failure mode is observable through
> the existing public surface (exit code + report JSON + `ctx.anomalies`),
> that every failure leaves no partial target on disk, and that a
> successful `migrate` is locked against runtime drift.

If, after M12, a hostile or stressed input to the write path produces:

- a Python traceback escaping the CLI (non-`--debug`), **or**
- exit `0`/`8` on a run that left a partially-built target directory,
  **or**
- exit `5` on a run whose target directory still exists on disk (atomic
  rollback violated), **or**
- a `migrate` report whose `outcome == "success"` while the parity
  family contains a `failed` check, **or**
- a `migrate` wall-clock/RSS envelope drift on the minimal fixture that
  exceeds the recorded baseline tolerance without an accompanying
  reviewed re-baseline commit, **or**
- a module-level `print` / `logging` / `sys.std*.write` call site
  appearing in `transformation/` or `reconstruction/`,

then M12 has found a real defect. Defects surfaced by M12 are fixed in
the owning production module, not absorbed in test code and not papered
over by widening the baseline.

---

## 2. Non-goals (hard fences)

M12 must not:

1. Add a new pipeline stage, a new subcommand, or a new CLI flag. The
   pinned subcommand set `{analyze, inspect, report, migrate}` is
   frozen. The pinned exit-code set `{0..10}` is frozen. Exit `5`
   remains the only reconstruction-failure exit; no `5a`/`5b`
   sub-variants.
2. Add a new severity. `LOW/MEDIUM/HIGH/CRITICAL` is the closed set
   (M3).
3. Grow `AnomalyType` speculatively. M10/M11 already registered the
   write-path anomaly types (`TARGET_PATH_NOT_EMPTY`,
   `TARGET_PATH_NOT_DIRECTORY`, `TARGET_MANIFEST_WRITE_FAILED`,
   `RECONSTRUCTION_ROLLBACK`, `RECONSTRUCTION_INPUT_MISSING`,
   `TARGET_OPEN_FAILED`, `TARGET_RECORD_COUNT_MISMATCH`,
   `TARGET_ID_SET_MISMATCH`, `TARGET_DOCUMENT_HASH_MISMATCH`,
   `TARGET_METADATA_MISMATCH`, `TARGET_EMBEDDING_MISSING`,
   `TARGET_EMBEDDING_PROBE_INCONCLUSIVE`). If M12 finds a real failure
   mode without a matching enum value, that is a **finding to fix in
   the owning stage**, not an excuse to grow the registry from the test
   suite. Any such addition is tracked as a discovery per §6.
4. Introduce `print()` / `logging` / `sys.std*.write` in production
   code. The M8 AST walk (`tests/hardening/test_logging_discipline.py`)
   already sweeps the *entire* `src/mempalace_migrator/` tree, so
   `transformation/` and `reconstruction/` are already under its
   purview. Task `15.7` is therefore a **regression-guard
   re-assertion**, not a new walk.
5. Weaken any M7 invariant. `tests/adversarial/_invariants.py` is
   imported verbatim; M12 adds entries to the parametrised corpus, not
   new exception paths in the invariant functions.
6. Weaken any M8 tolerance. The recorded `wall_clock_pct: 50` and
   `rss_bytes_pct: 25` stay. Adding a `migrate`-success entry means the
   envelope grows by one row, not that the tolerance widens.
7. Optimise opportunistically. M12 may change reconstruction behaviour
   *only* when (a) an adversarial fixture produces a concrete defect
   observable through the public surface, (b) the defect is
   reproducible, (c) the fix is the minimal change that restores the
   documented contract.
8. Add fixtures that require a network, an external ChromaDB server,
   or non-pinned `chromadb` versions. The supported version pair is
   `chromadb>=1.5.7,<2` for reconstruction-side, `chroma_0_6` on the
   source side. Everything M12 touches uses exactly those.
9. Touch end-to-end usability claims. Those are M13's job (phase 16).
   M12 only proves the write-path *pieces* are hardened; it does not
   claim the product is end-to-end usable.
10. Validate retrieval parity, usage-scenario parity, or MCP-runtime
    behaviour. Those are explicit `non_goals` in `ROADMAP.json`.

---

## 3. Architecture

### 3.1 Test layout

```
tests/
├── adversarial/
│   ├── conftest.py                       # M7 — extended with write-path builders (§3.2)
│   ├── _invariants.py                    # M7 — unchanged, imported as-is
│   ├── test_adversarial_invariants.py    # M7 — corpus parametrisation grows (15.4)
│   ├── test_reconstruction_target.py     # NEW — 15.1
│   ├── test_reconstruction_rollback.py   # NEW — 15.2 + 15.8
│   ├── test_transformation_pathological.py  # NEW — 15.3
│   ├── test_reconstruction_stress.py     # NEW — 15.9
│   ├── test_reconstruction_duplicates.py # NEW — 15.10
│   └── test_reconstruction_readback.py   # NEW — 15.11
└── hardening/
    ├── conftest.py                       # M8 — BASELINE_CORPUS filter grows (§3.4)
    ├── rebaseline.py                     # M8 — regenerates both JSON files (unchanged logic)
    ├── baselines/
    │   ├── runtime_envelope.json         # gains migrate-success entry (15.5)
    │   └── report_signatures.json        # gains migrate-success signature (15.6)
    ├── test_logging_discipline.py        # M8 — re-asserted (15.7)
    └── test_stability_invariants.py      # M8 — gains migrate-success signature row (15.6)
```

Rationale (parallels M7 §3.1):

- One new adversarial file per distinct failure-class sub-task to keep
  traceability obvious (`15.1`, `15.2`, `15.3`, `15.9`, `15.10`,
  `15.11`). Tasks `15.2` and `15.8` share a file because "rollback" is
  a single invariant observed through multiple injection points.
- The cross-cutting invariants file (`test_adversarial_invariants.py`)
  is *extended* (corpus grows) and *not* forked. This is exactly the
  M7 §5 contract: one invariant sweep, one exit gate.
- `baselines/` files are committed, diff-reviewed, and regenerated only
  via `rebaseline.py`. No `--update-baseline` flag. No in-test
  auto-rewrite. M8 §3.3 protocol applies verbatim.

### 3.2 Fixture builders (additions to `tests/adversarial/conftest.py`)

The existing `CorpusEntry` dataclass and `run_cli` helper stay
unchanged. M12 adds **builders only**, and those builders compose M7's
`build_minimal_valid_chroma_06` (or its M10/M11 equivalent that already
ships in the conftest). Sketch only:

```python
# illustrative only — not final code

# 15.1 — target safety
def target_is_file(tmp_path: Path) -> Path: ...          # write a regular file at target path
def target_is_non_empty_dir(tmp_path: Path) -> Path: ... # mkdir + drop a stray file inside
def target_is_readonly_dir(tmp_path: Path) -> Path: ...  # POSIX-only (skipif elsewhere)

# 15.2 / 15.8 — mid-batch failure (injection via monkeypatch, not fixture bytes)
# The builder only prepares a *valid* source + target_path; the failure is
# injected in-test by monkeypatching chromadb.Collection.add on batch index N.

# 15.3 — pathological transformation input
def all_blank_ids_source(tmp_path: Path) -> Path: ...
def all_nonstring_documents_source(tmp_path: Path) -> Path: ...

# 15.9 — stress
def large_valid_source(tmp_path: Path, *, n_rows: int) -> Path: ...
#   n_rows parametrised: BATCH_SIZE - 1, BATCH_SIZE, BATCH_SIZE + 1, 2*BATCH_SIZE + 1
#   (BATCH_SIZE = 500 is the prototype-pinned value in reconstruction/_writer.py)

# 15.10 — duplicate ids reach the writer
def duplicate_ids_source(tmp_path: Path) -> Path: ...
#   Two rows sharing an embedding id that survive extraction + transformation
#   (if transformation already rejects the second, the test asserts *that*
#    behaviour and does not reach the writer — see §4.10).

# 15.11 — read-back
#   No new builder; reuses the minimal valid source. The test runs `migrate`,
#   then opens the target via the same lazy-import path as parity.py.
```

Rules that carry over from M7:

- Builders are the **only** place write-path-adversarial bytes are
  produced.
- Builders return a `Path` to a synthesised source (and, where
  relevant, a pre-shaped target path); they never mutate global state.
- Tests import builders; they never construct corruption or
  monkeypatches inline beyond the single injection point they are
  testing.

### 3.3 No mocking of production code (with one named exception)

M12 runs the real pipeline (`run_pipeline` / `PIPELINES["migrate"]`)
and the real CLI (`subprocess.run([..., "migrate", source, "--target",
target])`).

The **one named exception** is `15.2`/`15.8`: rolling back a real
ChromaDB write after a real failure is the contract under test, so we
need a reproducible failure at a chosen batch boundary. The only
permitted injection is a `monkeypatch` of `collection.add` (or of the
single call site inside `reconstruction/_writer.py`) to raise a chosen
exception on the Nth batch. No other production surface may be
patched. In particular:

- `_safety.ensure_target_is_safe` is **not** monkeypatched — its
  pre-write refusals are covered by real on-disk fixtures in `15.1`.
- `_manifest.write_target_manifest` is **not** monkeypatched — if
  manifest writes need a failure-path fixture, it comes from a real
  read-only directory on POSIX.
- `chromadb` module-level symbols are not replaced; only the bound
  `collection.add` method on the live client is intercepted, for the
  duration of the test.

### 3.4 Baseline corpus growth (M8 side)

`BASELINE_CORPUS` in `tests/hardening/conftest.py` is derived from
`CORPUS` by filtering on `allowed_exit_codes ⊆ {0, 8}`. Adding a
migrate-success baseline therefore requires:

1. A single new `CorpusEntry` in `tests/adversarial/conftest.py` with
   `pipeline="migrate"`, `allowed_exit_codes = frozenset({EXIT_OK})`,
   and a builder that stages both a valid source and a fresh target
   path. The entry is included in the invariant sweep (`15.4`).
2. `run_cli` already forwards arguments verbatim, but the `migrate`
   subcommand requires `--target`. `run_cli` is extended (or a
   `run_migrate_cli` thin wrapper is added in the conftest) to accept a
   per-entry "extra args" hook. The wrapper is a pure helper — no
   production code changes.
3. `rebaseline.py` then picks the new entry up automatically via
   `BASELINE_CORPUS`. Re-running it produces the new row in
   `runtime_envelope.json` and a new entry in `report_signatures.json`
   (see §4.6 for the redaction map).

---

## 4. Sub-task strategy

Each TODO sub-task maps to one test file (or one extension of an
existing file). For every sub-task we fix: the **assertion surface**,
the **input recipe**, and the **forbidden behaviour**.

### 4.1 — `15.1` Target safety (`tests/adversarial/test_reconstruction_target.py`)

| Input                                               | Expected exit | Required anomaly                                          | Forbidden                          |
|-----------------------------------------------------|---------------|-----------------------------------------------------------|------------------------------------|
| `--target` points to an existing regular file       | `5`           | `TARGET_PATH_NOT_DIRECTORY` at stage `reconstruct`        | silent overwrite; exit `0`/`8`     |
| `--target` points to an existing non-empty directory| `5`           | `TARGET_PATH_NOT_EMPTY` at stage `reconstruct`            | silent overwrite; partial merge    |
| `--target` parent is read-only (POSIX `skipif` gate)| `5`           | `TARGET_MANIFEST_WRITE_FAILED` **or** `RECONSTRUCTION_ROLLBACK` (writer owns which) | bare `PermissionError` escaping |

Assertions per fixture:

- `rc == 5` (real subprocess; `EXIT_RECONSTRUCT_FAILED`).
- No traceback on stderr (Inv. 2 / M7 §5).
- Report `failure.stage == "reconstruct"`.
- Report JSON contains at least one anomaly of the named type with
  non-empty `evidence` (Inv. 6).
- `target_path` on disk is **byte-identical** to its pre-run state for
  the "file" and "non-empty dir" cases (enumerate contents before/after
  and compare hashes; the safety check must have refused *before* any
  write). For the read-only-parent case, `target_path` must not exist
  post-run.
- Source palace mtime and byte content unchanged (global write-path
  invariant, re-asserted in every M12 adversarial test).

### 4.2 — `15.2` Mid-batch ChromaDB failure (`tests/adversarial/test_reconstruction_rollback.py`)

Parametrised over batch index `N ∈ {0, 1, k}` where `k` is the last
batch index on the stress fixture (see `15.9`). Strategy:

1. Build a valid source whose drawer count straddles `BATCH_SIZE`
   boundaries (`BATCH_SIZE = 500` per `reconstruction/_writer.py`).
2. `monkeypatch.setattr` the live `collection.add` to raise
   `RuntimeError("injected mid-batch failure")` when the call count
   equals `N`; all prior batches succeed normally.
3. Run `migrate` in-process via `run_pipeline` (to obtain `ctx`) **and**
   via subprocess (to obtain `rc, stdout, stderr`). Both must agree —
   disagreement is itself a finding (see M7 §3.3).

Assertions:

- `rc == 5`; report `failure.stage == "reconstruct"`.
- Anomaly `RECONSTRUCTION_ROLLBACK` present, with evidence naming the
  failed batch index.
- `target_path` does **not** exist on disk post-run (full rollback).
- Source palace unchanged (byte-compare + mtime).
- The anomaly list includes the injected failure's batch metadata
  (`batch_index`, `affected drawer ids count` at minimum). If the
  current writer does not already list affected ids, that is an M12
  finding per §6 and the fix lands in `reconstruction/_writer.py`, not
  in the test.

### 4.3 — `15.3` Transformation rejects-all (`tests/adversarial/test_transformation_pathological.py`)

Two fixtures:

- every drawer has a blank id;
- every drawer has a non-string document.

Assertions (analyze, inspect, migrate all exercised):

- `TransformedBundle.drawers` ends up empty.
- Transformation emits **one anomaly per dropped drawer** at stage
  `transform` (the M9 per-drawer-drop contract). No aggregate
  substitution.
- Downstream behaviour on `migrate`: the writer must **not** silently
  produce an empty target. The expected behaviour is a structured
  refusal — either reconstruction sees `RECONSTRUCTION_INPUT_MISSING`
  (already in the enum) and exits `5`, or transformation itself is
  allowed to be terminal per existing contract. M12 asserts whichever
  path is currently implemented and forbids the "silent empty target"
  outcome. If neither structured refusal fires and an empty target is
  written, that is a finding fixed in the owning module (§6).

### 4.4 — `15.4` M7 invariants on extended corpus (`tests/adversarial/test_adversarial_invariants.py`)

Nothing is edited except the parametrisation source: the corpus in
`conftest.py` grows to include every new `CorpusEntry` produced by
`15.1`, `15.2` (one representative batch-N entry), `15.3`, `15.9`,
`15.10`, `15.11`. The **nine** invariants in `_invariants.py` must
pass against every new entry unchanged. No invariant may be relaxed,
xfailed, or bypassed with `skipif` (except the POSIX-only fixtures
where `skipif` is itself asserted to mention a platform reason, M7 §8).

Explicit expectation: invariants 3 (no silent CRITICAL), 4 (schema
stability — currently `REPORT_SCHEMA_VERSION == 5`), 7 (forbidden
vocabulary), and 8 (failure stage known) each have fresh write-path
triggers via the new corpus.

### 4.5 — `15.5` Migrate-success baseline entry (`tests/hardening/baselines/runtime_envelope.json`)

- Add **one** baseline corpus entry for a successful `migrate` run on
  the minimal valid `chroma_0_6` fixture. No other entry is modified.
- Generation: `python3 tests/hardening/rebaseline.py`. The script does
  not grow — it already iterates `BASELINE_CORPUS` (§3.4).
- Tolerance remains `{wall_clock_pct: 50, rss_bytes_pct: 25}`.
- Commit contains the data drift **plus** a one-line justification in
  the PR body ("M12.5: add migrate-success baseline"). No silent
  rewrite. No opportunistic re-baseline of pre-existing entries in the
  same commit — those must land in a separate PR if they drift.

### 4.6 — `15.6` Migrate-success report signature (`tests/hardening/test_stability_invariants.py` + `report_signatures.json`)

The `extract_report_signature` helper in `tests/hardening/conftest.py`
already redacts `run_id`, `started_at`, `completed_at`. The migrate
success introduces new volatile fields; the redaction map **must** add
(at minimum):

- `reconstruction.target_manifest_path` — absolute, machine-specific;
- `reconstruction.chromadb_version` — version-pinned in `pyproject.toml`
  but still environment-sensitive;
- any `reconstruction.duration_*` / timing field present in the
  structured reconstruction summary (M10).

`imported_count` is **not** redacted — it must equal drawer count on
the fixture, so its stability is part of the signature (this is the
positive counterpart of `15.11`'s read-back check).

The stability test then re-runs `migrate` twice and asserts the
redacted signature is byte-identical between runs and matches the
committed `report_signatures.json` row for the new entry.

### 4.7 — `15.7` Logging discipline re-assertion (`tests/hardening/test_logging_discipline.py`)

**Current state (verified during this design pass):**
`_SRC_ROOT = ".../src/mempalace_migrator"` and `collect_source_files`
walks the entire tree — so `transformation/` and `reconstruction/` are
*already* swept, and the existing test already fails loudly on any
violation there.

M12's task is therefore:

1. Add an explicit assertion that the swept file set includes at least
   one file from each of `transformation/` and `reconstruction/`
   (guard against a future refactor that accidentally excludes a
   subtree).
2. Do **not** introduce a per-subtree test file. One AST walk, one
   authority.
3. Do **not** extend the forbidden-call list.

### 4.8 — `15.8` Atomic rollback re-assertion (`tests/adversarial/test_reconstruction_rollback.py`)

Same file as `15.2`, separate test function. Where `15.2` asserts
rollback on a chromadb `add` failure, `15.8` asserts rollback on
**every** injected write-path failure point:

- manifest write raises (POSIX read-only parent fixture from `15.1`);
- first-batch `add` raises (`15.2` with `N=0`);
- last-batch `add` raises (`15.2` with `N=k`);
- the `reconstructor` itself raises between `ensure_target_is_safe` and
  `collection.add` (if a reachable interior failure point exists;
  otherwise recorded as "no injection point, contract holds by
  construction").

Invariant re-asserted on every case: `target_path` does not exist
post-run, source is byte-identical, anomaly stage is `reconstruct`,
exit is `5`.

### 4.9 — `15.9` Batch-size stress (`tests/adversarial/test_reconstruction_stress.py`)

Parametrised over `n_rows ∈ {BATCH_SIZE - 1, BATCH_SIZE, BATCH_SIZE +
1, 2*BATCH_SIZE + 1}` to exercise batch-boundary edges. `BATCH_SIZE =
500` is imported from `reconstruction/_writer.py`, not hard-coded, so a
future tuning change is caught by a single source of truth.

Assertions per size:

- `rc == 0`; outcome `success`.
- `reconstruction.imported_count == n_rows`.
- Parity checks pass (M11 contract holds under size variation).
- Wall-clock and peak-RSS stay within the M8 recorded tolerances for
  the migrate baseline entry. **Important:** this is a sanity check,
  not a separate baseline row — we do not commit stress-size timings.
  A stress run that exceeds envelope fails the test; fixing it is a
  production-side concern.

### 4.10 — `15.10` Duplicate-id ingestion failure (`tests/adversarial/test_reconstruction_duplicates.py`)

Contract chain to pin:

1. Extraction emits `DUPLICATE_EMBEDDING_IDS` when two source rows share
   an id (already observed per `ROADMAP.json` M7 finding).
2. Transformation either drops the duplicate(s) with structured
   anomalies or passes both through depending on current behaviour.
3. If duplicates reach the writer, `collection.add` raises. M12
   asserts: `rc == 5`, `RECONSTRUCTION_ROLLBACK` emitted, target
   absent, source unchanged.

The test parametrises on the chain outcome: *either* transformation
filters and the writer never sees duplicates (assertion: no target
created, structured transformation anomalies present), *or* the writer
sees them and atomic rollback fires (assertion: §4.8 invariants).
Which branch is currently wired is observed, not prescribed — a silent
"both duplicates written, one overwrites the other" outcome is the one
and only forbidden behaviour.

### 4.11 — `15.11` Read-back verification (`tests/adversarial/test_reconstruction_readback.py`)

After a successful `migrate`:

1. Open the target via the same lazy-import path used by
   `validation/parity.py::_open_target_readonly` (do not duplicate the
   open logic in the test — import the helper).
2. Assert the opened collection's record count equals
   `reconstruction.imported_count` from the report.
3. Assert the id set read from the target equals the id set declared
   in the transformed bundle (retrievable from `ctx` in-process, or
   from the report's structured reconstruction summary).
4. Assert the target collection name equals the report's
   `reconstruction.collection_name`.

Negative counterpart: if the read-back disagrees with the bundle, the
test fails with a structured diff, not a bare `assert`. Divergence is
a production-side defect (M11 parity is supposed to have caught it);
the fix is in `validation/parity.py` or `reconstruction/_writer.py`,
not in the test.

---

## 5. Cross-cutting exit gate

M12's exit gate is **the union** of:

1. All nine M7 invariants in `tests/adversarial/_invariants.py`, applied
   unchanged to the extended corpus (`15.4`).
2. Two additional write-path invariants, applied on every adversarial
   write-path run:
   - **Atomicity:** failed `migrate` ⇒ `target_path` does not exist on
     disk post-run (or, for the "target pre-existed as file/non-empty
     dir" cases in `15.1`, is byte-identical to its pre-run state).
   - **Source immutability:** any `migrate`/`analyze`/`inspect` run
     (success or failure) leaves the source palace byte-identical and
     with unchanged `st_mtime_ns`.
3. The M8 baseline envelope, extended by the migrate-success row, still
   passes within the unchanged tolerance (`15.5` + `15.6`).
4. The M8 AST logging-discipline sweep still passes for the entire
   `src/mempalace_migrator/` tree, with `15.7`'s "covers
   transformation/ and reconstruction/" assertion added.

If any invariant fails on any corpus entry, M12 is **FAIL** —
regardless of how many positive per-file tests pass.

---

## 6. Handling discoveries during M12

M12 is expected to find defects. Triage rule (mirrors M7 §6):

| Discovery                                                                 | Action                                                                             |
|---------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| Missing `AnomalyType` for a real write-path failure mode                  | Add the enum value + emit it from the owning stage; record as caveat if pervasive  |
| Rollback leaves a file behind                                             | Fix `reconstruction/_writer.py` / `reconstructor.py`; tighten the cleanup          |
| Source mtime changes on any pipeline path                                 | Fix extraction-side open-mode (already SQLite URI read-only; re-verify)            |
| `migrate` report missing a reconstruction field under failure             | Fix `build_report` / structured reconstruction summary (M10 contract)              |
| `migrate` exits `5` with no `RECONSTRUCTION_ROLLBACK` anomaly             | Emit the anomaly from the writer's rollback path; test stays                       |
| Transformation accepts an input that should be per-drawer-dropped         | Tighten `transformation/_normalize.py` (M9 contract)                               |
| AST sweep misses a subtree due to refactor                                | Fix `collect_source_files` + re-assert the coverage guard from `15.7`              |
| Baseline drift on pre-existing entry (not migrate)                        | **Not M12's job** — defer, do not re-baseline opportunistically                    |
| Parity check reports `passed` on a read-back that diverges                | Fix `validation/parity.py`; test stays                                             |
| A class of failure requires a **new pipeline stage or new exit code**     | **Out of scope** — record as a caveat in `ROADMAP.json` and defer                  |

Each discovery: open the fix in the owning production module, keep the
M12 test that proved it, do **not** widen scope into new features.

---

## 7. What is implemented vs. what remains missing

**Implemented today (at HEAD, before any M12 code):**

- Write path end-to-end: `MIGRATE_PIPELINE`, `step_transform`,
  `step_reconstruct`, `step_validate` (parity family), CLI `migrate`
  subcommand with mandatory `--target`.
- Atomic rollback primitives in `reconstruction/_writer.py` /
  `reconstruction/reconstructor.py` (M10 exit gate pinned this; adapted
  to chromadb 1.5.7's empty-dict metadata rejection, see ROADMAP caveat).
- Parity checks (5) with lazy `chromadb` import; `EXPLICITLY_NOT_CHECKED`
  shrunk to 7 (M11).
- M7 adversarial framework (`CORPUS`, `_invariants.py`, `run_cli`).
- M8 hardening framework (`BASELINE_CORPUS`, `rebaseline.py`, two
  committed baseline JSON files, AST logging-discipline sweep over the
  entire `src/` tree).

**Not yet implemented (M12 will build):**

- Six new adversarial test files (`test_reconstruction_target.py`,
  `test_reconstruction_rollback.py`, `test_transformation_pathological.py`,
  `test_reconstruction_stress.py`, `test_reconstruction_duplicates.py`,
  `test_reconstruction_readback.py`).
- New builders in `tests/adversarial/conftest.py` for: target-as-file,
  target-as-non-empty-dir, target-readonly-parent (POSIX),
  all-blank-ids, all-nonstring-docs, large-valid-source parametrised on
  `BATCH_SIZE`, duplicate-ids.
- One new `CorpusEntry` with `pipeline="migrate"` and the per-entry
  extra-args hook in `run_cli` (or a thin `run_migrate_cli` wrapper).
- Extension of `extract_report_signature` redaction map to cover the
  reconstruction section's volatile fields (§4.6).
- One new row in `runtime_envelope.json`, one new entry in
  `report_signatures.json`, both regenerated via the existing
  `rebaseline.py`.
- `15.7` coverage-guard assertion in `test_logging_discipline.py`.
- Corpus parametrisation extension in
  `test_adversarial_invariants.py` (mechanical; no new invariants).

**Explicitly out of scope for M12 (do not start):**

- End-to-end usability claims (phase 16 / M13).
- Retrieval-parity, usage-parity, MCP-runtime validation
  (`ROADMAP.json::non_goals`).
- Supporting chromadb versions other than `>=1.5.7,<2`, or palace
  formats other than `chroma_0_6`.
- Re-baselining pre-existing (non-migrate) entries.
- Unifying detection's pre-M3 Evidence/Contradiction model with
  `Anomaly` (long-standing caveat).
- Tightening the per-row DUPLICATE_EMBEDDING_IDS granularity beyond
  what `15.10` requires (ROADMAP M7 deferred finding).

---

## 8. Failure modes M12 itself must avoid

Meta-risks for the test suite:

1. **Tautological tests.** Asserting "an anomaly was emitted" without
   asserting *which* one. Mitigation: every new test pins
   `anomaly.type` and `location.stage`, never just "non-empty".
2. **Baseline rot.** Re-baselining pre-existing entries in the same PR
   as adding the `migrate` entry. Mitigation: §4.5 forbids it
   explicitly; rebaseline PRs are one-purpose.
3. **Hidden non-determinism in `migrate` baseline.** The reconstruction
   summary contains paths and version strings that are
   machine-specific. Mitigation: §4.6 pins the redaction map; stability
   test runs twice in the same process and diffs.
4. **Mock leakage.** Monkeypatching more production surface than
   strictly needed. Mitigation: §3.3 lists the one permitted
   injection point (`collection.add`).
5. **Silent skips.** POSIX-only fixtures (`target_is_readonly_dir`)
   silently skipping on non-POSIX CI. Mitigation: `skipif` includes an
   asserted platform reason (M7 §8).
6. **Test code masking production code.** No `try/except` around
   pipeline calls — exceptions either surface as stage-tagged
   `MigratorError` (assertable) or fail the test.
7. **Builder-inventory drift.** New builders added elsewhere than
   `tests/adversarial/conftest.py`. Mitigation: §3.2 pins the single
   location.
8. **Stress suite doubling as a benchmark.** `15.9` timings must not
   be committed. Mitigation: the stress test uses the **existing**
   migrate baseline's tolerances as a sanity bound, and commits
   nothing.
9. **Duplicate-id contract drift.** `15.10` prescribes one of two
   branches (transform-filters vs writer-fails) based on current
   behaviour. If the production code changes branch without updating
   the test, the test must fail — hence asserting the chain outcome,
   not branching in the test itself. The test observes; it does not
   tolerate both outcomes silently.

---

## 9. Exit-gate checklist (what "M12 done" means)

M12 is **PASS** only when **all** of the following hold:

- [ ] The six new adversarial test files listed in §3.1 exist and are
      collected by pytest.
- [ ] At least one fixture per row of the §4 tables is exercised, with
      its per-row contract pinned (anomaly type + stage + exit code + on-disk
      invariant).
- [ ] `test_adversarial_invariants.py` parametrises over every new
      corpus entry; the nine M7 invariants plus the two M12 write-path
      invariants (§5.2) pass on every entry unchanged.
- [ ] No invariant is xfailed, skipped, or guarded by
      `pytest.mark.skipif` without a recorded platform reason.
- [ ] `runtime_envelope.json` contains a new `cid` for the
      migrate-success run, recorded via `tests/hardening/rebaseline.py`
      in a reviewed commit, with tolerance unchanged.
- [ ] `report_signatures.json` contains the migrate-success signature
      row; `test_stability_invariants.py` re-runs `migrate` and matches
      the committed signature (redaction map from §4.6 applied).
- [ ] `test_logging_discipline.py` still passes on the full
      `src/mempalace_migrator/` tree **and** now asserts explicit
      coverage of `transformation/` and `reconstruction/` subtrees.
- [ ] Every defect surfaced by M12 is fixed in the owning production
      module (not in test code) **or** explicitly recorded as an
      out-of-scope caveat in `ROADMAP.json::current_position.caveats`.
- [ ] Full test suite (existing 775 + M12 additions) is green.
- [ ] `tests/TODO.json` phase 15 sub-tasks `15.1`–`15.11` are flipped to
      `done` with a `note` pointing to the test file that proves each
      one.
- [ ] `tests/ROADMAP.json::current_position` is updated to
      `M12_done, M13 next` **only after** the ten items above are
      satisfied, and
      `completed_milestones` gains `"M12"`.

Until every box is ticked, M12 status remains `todo` and M13 must not
start. Partial completion is **CONDITIONAL FAIL**, not "in progress".
