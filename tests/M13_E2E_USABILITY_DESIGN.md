# M13 — End-to-End Migration Usability Gate: Implementation Strategy

Status: **design only** (no test code, no production code merged yet).
Scope: phase 16 in `tests/TODO.json` (`16.1 .. 16.7`).
Predecessors satisfied: M1–M12 exit gates (see `ROADMAP.json::current_position` →
`M12_done`, 840 tests green).
Successor: none. M13 is the final milestone of the project.

This document fixes the *shape* of M13 work. M13 is intentionally the
narrowest milestone in the roadmap:

- **No** new pipeline stage.
- **No** new CLI subcommand or flag.
- **No** new `AnomalyType`, `Severity`, or `CheckFamily` values.
- **No** new exit code.
- **No** new production module. A single new test file
  (`tests/test_migrate_e2e.py`) plus one meta-assertion inside
  `tests/TODO.json` (the 16.7 promotion rule) is the entire deliverable.

M13 is a **proof obligation**, not a feature milestone. Its job is to
demonstrate — by construction, on the real `migrate` CLI surface — that
the implementation landed over M9–M12 is actually *usable* as a complete
product on the supported version pair (chromadb 0.6.x → 1.5.7), under
the constraints the project has been advertising since M1.

---

## 1. M13 in one sentence

> Pin, via a dedicated end-to-end test file, that on the minimal valid
> `chroma_0_6` fixture the `migrate` command exits 0, produces a
> reopenable ChromaDB 1.5.7 target, runs every target-parity check
> (none remain in `checks_not_performed`), keeps the source byte- and
> mtime-identical, reports confidence ≥ MEDIUM without forbidden
> correctness vocabulary, and is repeatable across two fresh targets
> modulo explicitly non-deterministic fields.

If, after M13, any of the following is observable on the happy-path
fixture:

- exit code ≠ 0 on a supported `migrate` invocation, **or**
- a `target_*_parity` entry in `report.validation.checks_not_performed`
  of a successful run, **or**
- the reconstructed target cannot be reopened via
  `chromadb.PersistentClient` in a fresh process, **or**
- `collection.count()` of the reopened target ≠
  `report.reconstruction.imported_count`, **or**
- the source palace’s SHA-256 or `st_mtime_ns` changes between the
  pre-run and post-run snapshots, **or**
- `validation.confidence_band` is below `MEDIUM`, **or**
- a forbidden correctness word (`correct`, `verified`, `guaranteed`,
  `valid`) appears in the JSON report, **or**
- any stage carries `status == "executed"` while its result hides a
  `CRITICAL` anomaly, **or**
- two migrate runs against two different fresh targets produce
  diverging `extract_report_signature` payloads (modulo the documented
  volatile fields), **or**
- `current_position.completed_milestones` claims `M13` while any of
  `16.1–16.6` are not `done`,

then M13 has found a real defect. Defects surfaced by M13 are fixed in
the owning production module (pipeline, validation, reconstruction,
reporting) — **not** absorbed in test code, not hidden by relaxing an
assertion, and not papered over by marking the check inconclusive.

---

## 2. Non-goals (hard fences)

M13 must not:

1. Add a new pipeline stage, subcommand, or CLI flag.
2. Introduce a new `AnomalyType`, `Severity`, `CheckFamily`, or
   `SkippedReason` value.
3. Introduce a new exit code. The five existing codes
   (`EXIT_OK`, `EXIT_USAGE_ERROR`, `EXIT_EXTRACT_FAILED`,
   `EXIT_CRITICAL_ANOMALY`, `EXIT_RECONSTRUCT_FAILED`) remain
   exhaustive.
4. Broaden supported source formats. Only `chroma_0_6` is the happy
   path; UNKNOWN/other formats remain rejected at detection.
5. Add retrieval-parity, usage-parity, or MCP-runtime checks
   (explicit ROADMAP `non_goals`).
6. Weaken any existing honesty contract: the M5 forbidden-vocabulary
   regex, the M7 invariant battery, the M8 baseline envelope, and the
   M11 parity trichotomy must all continue to hold on the new test
   file.
7. Re-baseline the M8 runtime envelope. The migrate-success entry was
   already committed in M12 (15.5). M13 *consumes* it; it does not
   move it.
8. Write any new production code, unless a defect surfaced by
   16.1–16.6 forces a minimal fix in the owning module (see §6).

---

## 3. Surface under test

The only public surface exercised by M13 is:

- the `migrate` CLI subcommand (`python -m mempalace_migrator.cli.main
  migrate SOURCE --target TARGET --json-output`), invoked through
  `subprocess` (to preserve the Click 8.2+ stdout/stderr discipline
  established in M9/M12), and
- the resulting on-disk artifacts:
  - `TARGET/` populated by `reconstruction/_writer.py`, including the
    `TARGET_MANIFEST_FILENAME` sidecar,
  - the JSON report on `stdout`,
  - the textual report on `stderr` (only inspected for the forbidden
    vocabulary negative check, matching M5 `test_validation.py`), and
  - the source directory hash (`sha256(MANIFEST) + sha256(SQLITE)`)
    and each source file’s `stat().st_mtime_ns`.

Additionally, one assertion (16.6) reopens the target through
`chromadb.PersistentClient` in a **fresh Python subprocess** — not
in-process — to prove the reopenability is not an artifact of the
writer’s still-warm client.

No other entry point is added or exercised.

---

## 4. Fixture strategy

M13 reuses — it does **not** rebuild — the happy-path fixture already
used by M9–M12:

1. **Minimal valid `chroma_0_6` palace.** The helpers
   `_write_manifest`, `_make_valid_db`, and `_sha256` already exist in
   `tests/test_cli_migrate.py`. M13 imports them as-is or copies them
   verbatim into `tests/test_migrate_e2e.py` with attribution. No new
   schema, no new row shape.
2. **Drawer count.** `n_drawers = 3` (the M10/M11 default). M13 does
   **not** stress batch boundaries (that is M12’s 15.8 territory).
3. **Two fresh targets.** For 16.4 (repeatability), each migrate run
   targets a *distinct* empty directory under `tmp_path`. The source
   palace is shared and must remain untouched across both runs
   (16.5 re-asserts this at the full-command level).
4. **No adversarial perturbation.** M13 is the happy-path gate. Every
   adversarial input is already covered by M7/M12. Introducing a new
   adversarial fixture here would widen scope.

---

## 5. Test file layout

A single new file: `tests/test_migrate_e2e.py`.

Suggested internal structure (not binding — the tests are free to
share helpers):

```
tests/test_migrate_e2e.py
├── _run_migrate(source, target) -> (exit_code, report_dict, stderr_text)
│       # subprocess.run([sys.executable, "-m", ...], capture_output=True)
│       # json.loads(stdout); returns stderr for the vocab check.
├── _snapshot_source(source) -> dict[Path, tuple[str, int]]
│       # {manifest: (sha256, mtime_ns), sqlite: (sha256, mtime_ns)}
├── test_migrate_happy_path_exits_zero_and_builds_target       # 16.1
├── test_parity_checks_are_executed_not_skipped                # 16.2
├── test_confidence_and_no_forbidden_vocabulary                # 16.3
├── test_two_fresh_target_runs_are_repeatable                  # 16.4
├── test_source_bytes_and_mtime_invariant_across_full_command  # 16.5
├── test_reconstructed_target_reopens_in_fresh_process         # 16.6
└── test_todo_promotion_rule_is_self_consistent                # 16.7
```

Implementation size budget: ~200–300 LOC total, excluding the shared
fixture helpers if they are imported rather than copied.

---

## 6. Per-task contract

Each task below fixes the **assertion**, the **fixture input**, and
the **failure-to-production-fix** mapping. Assertions are written
positively — M13 proves the happy path, so a failure means a
production defect, not a missing feature.

### 6.1 Task 16.1 — Successful migrate command contract

- **Fixture:** minimal valid `chroma_0_6` palace (3 drawers), empty
  `target/` directory under `tmp_path`.
- **Invocation:** `migrate SOURCE --target TARGET --json-output`
  via `subprocess.run`.
- **Assertions:**
  1. `exit_code == EXIT_OK` (0).
  2. `(target / TARGET_MANIFEST_FILENAME).is_file()`.
  3. `report["stages"]` contains, each with `status == "executed"`,
     the keys: `detect`, `extract`, `transform`, `reconstruct`,
     `validate`. No stage is `aborted`, `skipped`, or `not_run` on
     the happy path.
  4. `report["reconstruction"]["imported_count"] == 3`.
- **If it fails:** the defect is in the pipeline wiring
  (`core/pipeline.py::MIGRATE_PIPELINE`) or in one of the four
  stages, **not** in the test. A partial/aborted stage on the
  happy-path fixture means M9/M10/M11 regressed.

### 6.2 Task 16.2 — No skipped parity checks after successful migrate

- **Fixture/Invocation:** same as 16.1.
- **Assertions:** extract the set of `id` fields from
  `report["validation"]["checks_not_performed"]`. It must be
  disjoint from:

  ```
  {
      "target_record_count_parity",
      "target_id_set_parity",
      "target_document_hash_parity",
      "target_metadata_parity",
  }
  ```

  Additionally, every check in `report["validation"]["checks"]`
  whose `family == "parity"` must have `status in {"passed",
  "failed", "inconclusive"}` — never absent.
- **If it fails:** the defect is in
  `validation/__init__.py` (the `_skipped_when_no_reconstruction`
  branch is being taken despite a successful reconstruction) or in
  `validation/parity.py` (a parity check early-returns an
  inconclusive without evidence). The M11 honesty contract
  (EXPLICITLY_NOT_CHECKED shrunk 9 → 7) must hold at run time, not
  just at import time.

### 6.3 Task 16.3 — Confidence and honesty gate

- **Fixture/Invocation:** same as 16.1.
- **Assertions:**
  1. `report["validation"]["confidence_band"] in {"HIGH",
     "MEDIUM"}`. `LOW` and `UNKNOWN` are rejected on the happy
     path.
  2. Reuse the M7 forbidden-vocabulary check on the *JSON* report
     (not the text render): no word-boundary match against
     `{"correct", "verified", "guaranteed", "valid"}`. Import
     `check_no_forbidden_vocabulary` from
     `tests/adversarial/_invariants.py` to avoid vocabulary drift.
  3. For every stage with `status == "executed"`, no anomaly
     attached to that stage has `severity == "CRITICAL"`. Derive
     the per-stage anomaly set from `report["anomalies"]` filtered
     by `location.stage`. (This is the happy-path positive
     counterpart of M7 invariant 3, "no silent CRITICAL".)
- **If it fails:** a confidence downgrade on the happy path means
  one of the validation families is producing an `inconclusive` or
  `failed` outcome it should not. A forbidden word means a new
  report field leaked untested wording — fix in
  `reporting/report_builder.py` or the offending validation check.
  A hidden CRITICAL means the pipeline’s stage-status accounting in
  `core/pipeline.py` is out of sync with the anomaly stream.

### 6.4 Task 16.4 — Repeatability on fresh targets

- **Fixture/Invocation:** same source palace, two distinct empty
  target directories (`target_a/`, `target_b/`). Each run uses its
  own subprocess.
- **Assertions:**
  1. Both runs exit 0 and produce manifests.
  2. `report_a["reconstruction"]["imported_count"] ==
     report_b["reconstruction"]["imported_count"]`.
  3. Parity outcomes are set-equal: for every check
     `c` with `family == "parity"`,
     `status_a[c.id] == status_b[c.id]`.
  4. `extract_report_signature(report_a, 0) ==
     extract_report_signature(report_b, 0)`. Reuse the M8 helper
     from `tests/hardening/conftest.py` verbatim — do not
     re-implement signature extraction. The helper already redacts
     `run_id`, `started_at`, `completed_at`,
     `target_manifest_path`, and `chromadb_version`.
- **Volatile-field budget:** if a new volatile field appears in the
  report between M12 and M13 (e.g. a duration), the fix is to
  extend `extract_report_signature`’s redaction list in
  `tests/hardening/conftest.py`, not to relax this assertion. The
  extension must be reviewed as part of the same commit.
- **If it fails:** non-determinism is leaking through the pipeline.
  Likely culprits: an unordered anomaly list, a `dict` iteration
  order that depends on insertion order across runs, or a
  timestamp field not yet redacted. Fix the producer, not the
  test.

### 6.5 Task 16.5 — End-to-end source invariance

- **Fixture/Invocation:** same as 16.1, but snapshot the source
  before and after the full command.
- **Assertions:**
  1. `sha256(MANIFEST_FILENAME)` and `sha256(SQLITE_FILENAME)`
     identical pre/post.
  2. `stat().st_mtime_ns` identical pre/post for both files.
  3. The source directory contains the same set of filenames
     pre/post (no stray lockfile, no `-journal`, no
     `-wal`/`-shm` residue).
- **Why this is distinct from M10/M11.** M10 asserts the writer
  never touches the source at the `reconstruction/_writer.py`
  unit-test level. M11 asserts the parity validator opens the
  target read-only. M13.5 re-asserts both **at the full-command
  level** — the only level where a defect involving the detection
  or extraction stage (e.g. a SQLite `PRAGMA journal_mode=DELETE`
  that rewrites `mtime`) would be caught. The M12 corpus only
  asserts sha256; M13 adds `mtime_ns` and the filename-set
  assertion.
- **If it fails:** extraction or detection opened the source in a
  mode that writes. The fix is to pin the `mode=ro` URI or the
  `PRAGMA query_only=1` contract in the owning module.

### 6.6 Task 16.6 — Readable target smoke test

- **Fixture/Invocation:** same as 16.1.
- **Assertions (executed in a separate `subprocess`):**
  1. A fresh Python interpreter can `import chromadb`, construct
     `PersistentClient(path=str(target))`, fetch the collection by
     `EXPECTED_COLLECTION_NAME`, and call `collection.count()`.
  2. That count equals `report["reconstruction"]["imported_count"]`.
  3. The re-open does not raise and does not print to the parent
     process’s stderr.
- **Why a fresh subprocess is mandatory.** `chromadb.PersistentClient`
  caches settings and file handles at process scope. Re-opening
  in-process would mask a defect where the writer’s client state
  (sqlite WAL still open, metadata not flushed) is the only reason
  the target looks readable. A fresh process is the closest
  available proxy for "a real user opens the migrated palace
  tomorrow". This decision is consistent with the subprocess
  discipline already used in `test_cli_migrate.py` (M10) and in
  the M12 hardening corpus.
- **If it fails:** the writer is not flushing or is not calling
  `client.reset()` / letting the client go out of scope before
  returning. Fix in `reconstruction/_writer.py` — do **not** paper
  over it by re-opening the target in-process.

### 6.7 Task 16.7 — Current-position promotion rule

- **Fixture:** `tests/TODO.json` and `tests/ROADMAP.json`, loaded
  as JSON.
- **Assertions:**
  1. Find phase `16` in `TODO.json`. Collect `(task.id,
     task.status)` pairs for `16.1..16.6`.
  2. If any of those six tasks is not `"done"`, then
     `ROADMAP.json::current_position.milestone` must not be
     `"M13_done"` and `"M13"` must not appear in
     `current_position.completed_milestones`.
  3. Conversely, if all six are `"done"`, the project is allowed —
     but not required — to set `current_position.milestone` to
     `"M13_done"`.
- **Nature of this assertion.** 16.7 is the project’s own anti-lie
  contract: it prevents the roadmap from claiming end-to-end
  usability while the underlying tests are not all green. It is
  the only M13 test that does not exercise the `migrate` CLI.
- **If it fails:** the roadmap is out of sync with the TODO. Fix
  one of the two JSON files — **not** the test.

---

## 7. Reused infrastructure

M13 is deliberately built on top of already-committed infrastructure.
No duplicate of any of the following is introduced:

| Primitive | Owner | Reused by |
|---|---|---|
| `_write_manifest`, `_make_valid_db`, `_sha256` | `tests/test_cli_migrate.py` | 16.1–16.5 |
| `TARGET_MANIFEST_FILENAME` | `reconstruction/_manifest.py` | 16.1 |
| `EXPECTED_COLLECTION_NAME` | `extraction/chroma_06_reader.py` | 16.6 |
| `EXIT_OK`, `EXIT_RECONSTRUCT_FAILED`, `EXIT_USAGE_ERROR` | `cli/main.py` | 16.1 |
| `extract_report_signature` | `tests/hardening/conftest.py` | 16.4 |
| `check_no_forbidden_vocabulary` | `tests/adversarial/_invariants.py` | 16.3 |
| Parity check ids (`target_record_count_parity` …) | `validation/parity.py` / `validation/__init__.py` | 16.2 |
| Subprocess invocation pattern | `tests/test_cli_migrate.py` (happy path), `tests/adversarial/conftest.py::run_cli` | 16.1, 16.4, 16.5, 16.6 |

If a primitive needs to evolve (e.g. a new volatile field forces an
update to `extract_report_signature`), the evolution happens in the
**owning** file, and M13 imports the new version — it does not fork.

---

## 8. Failure modes M13 must *not* introduce

These are the patterns the reviewer should flag on the M13 PR:

1. **Test-only fixture drift.** A fresh `_make_valid_db` variant in
   `test_migrate_e2e.py` that diverges from the M10/M12 shape.
   Either import or copy verbatim; do not tweak.
2. **In-process reopen for 16.6.** `chromadb.PersistentClient(...)`
   in the test process. Must be a subprocess.
3. **Relaxed signature comparison for 16.4.** Using
   `assert sig_a.keys() == sig_b.keys()` instead of
   `assert sig_a == sig_b`. The M8 helper already makes full
   equality achievable.
4. **`try/except` around the CLI invocation.** The CLI must not
   raise. A traceback escaping the subprocess is a real M7
   invariant violation (invariant 2) and M13 must not hide it.
5. **Shared `target/` directory across 16.1–16.6.** Each test gets
   its own `tmp_path`-scoped target. Reuse of a populated target
   between two tests would either mask a rollback defect or
   trigger the M10 "non-empty target" refusal (exit 5) and falsely
   pass 16.2’s skip-set check via a different code path.
6. **Weakening the M5 vocabulary regex to scope only to the text
   renderer.** The M7 invariant already asserts it on the JSON
   output; 16.3 restates the JSON scope. Removing the JSON scope
   would silently re-enable forbidden words in the machine-readable
   surface.
7. **Marking 16.7 `done` without 16.1–16.6 `done`.** The
   `ROADMAP.json::current_position` must remain at `M12_done`
   until every E2E test is green.

---

## 9. Exit-gate checklist — what "M13 done" means

M13 is only considered complete when **all** of the following hold
simultaneously:

1. `tests/test_migrate_e2e.py` exists and contains the seven tests
   named in §5.
2. The full test suite is green: `pytest` exits 0 with the M12
   baseline (840 tests) + the new M13 tests (target: 840 + 7 =
   847 tests), no skips introduced by M13.
3. `tests/test_migrate_e2e.py::test_migrate_happy_path_exits_zero_and_builds_target`
   passes without any `xfail`, `skipif`, or
   `pytest.skip(...)` gate.
4. `report["validation"]["checks_not_performed"]` on the happy-path
   fixture contains **none** of the four target parity check ids.
5. `report["validation"]["confidence_band"]` on the happy-path
   fixture is `"HIGH"` or `"MEDIUM"`.
6. The M7 invariant battery, re-run by the existing
   `tests/adversarial/test_adversarial_invariants.py` against the
   unchanged corpus, is still fully green (M13 must not regress
   M7/M8/M12).
7. No new production file was added. No new `AnomalyType`,
   `Severity`, `CheckFamily`, `SkippedReason`, CLI flag,
   subcommand, exit code, or pipeline stage was introduced.
8. If a production fix was required to make an M13 assertion
   pass, the fix is scoped to the owning module, accompanied by a
   one-line entry in `ROADMAP.json::caveats`, and does **not**
   change any M1–M12 public contract (no schema bump, no baseline
   move).
9. `tests/TODO.json` marks each of `16.1..16.6` as `"done"` only
   after the corresponding assertion is green in CI.
10. `ROADMAP.json::current_position.milestone` is moved to
    `"M13_done"` **only** after 16.7 is green. The promotion is a
    separate, reviewable commit.

Any criterion above that is not met means M13 is **not** done —
regardless of how many of the other criteria are met.

---

## 10. What is implemented vs. what remains missing after M13

**Implemented (by M1–M12, re-proven by M13):**

- Detection, extraction, transformation, reconstruction, validation,
  reporting, CLI, adversarial corpus, hardening baseline, write-path
  rollback, target-parity trichotomy.
- Happy-path `migrate SOURCE --target TARGET` on the supported
  chroma_0_6 → chromadb 1.5.7 pair, producing a reopenable target
  and an honest report.

**Explicitly still missing (deliberately — out of scope per ROADMAP
`non_goals`):**

- Retrieval-parity and usage-scenario-parity validation (prototype
  `record-retrieval`, `compare-retrieval`, `record-usage`,
  `compare-usage`).
- MCP-runtime validation (`validate-mcp-runtime`).
- Embedding-shape validation / re-computation.
- Migration of any source format other than `chroma_0_6`.
- Multi-collection palaces.
- Any CLI flag for `--force`, `--skip-validation`, or
  `--strict-parity`.

M13 **does not close** any of these gaps. It only proves that the
gap between "stage-level mechanics work" and "the `migrate` command
is end-to-end usable on the supported pair" is closed.

Claiming the project is a **safe migration tool** remains out of
scope. The README positioning — an experimental reconstruction tool
derived from exploration work — is unchanged by M13.
