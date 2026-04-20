# M11 — Target Parity Validation (design)

Status: **design recorded, implementation pending.**
Owner phase: `TODO.json` `phases[14]`.
Roadmap milestone: `ROADMAP.json` `M11`.
Predecessors satisfied: M1–M10 (743 tests green; `step_reconstruct`
builds an atomic ChromaDB 1.5.7 palace at `ctx.target_path`; source is
byte-identical before/after the run).

This document fixes the strategy for M11 so that review and
implementation can proceed in lockstep. M11 is a **read-only**
milestone. It introduces no new pipeline stage, no new exit code, no
new severity, no new CLI subcommand, and no new dependency.
It strictly **lifts** the two parity checks that M5 deliberately parked
as `stage_not_implemented`, **adds** three more parity checks against
the freshly-built target, and **shrinks** `EXPLICITLY_NOT_CHECKED`
accordingly.

---

## 1. Scope (what M11 lands)

In scope:

1. New module `src/mempalace_migrator/validation/parity.py` containing
   the five target-side checks listed in §4. This is the **only** new
   file in `validation/`. It is the second module (after
   `reconstruction/_writer.py`) allowed to perform a module-level
   `import chromadb`.
2. `validation/__init__.py`: replace the unconditional
   `_SKIPPED_RECONSTRUCTION` tuple with a conditional path that calls
   `run_parity_checks(ctx)` when `ctx.reconstruction_result` is
   non-`None`, otherwise keeps the existing `stage_not_implemented`
   skip entries renamed to reason `reconstruction_not_run` (see §6.2 on
   the rename).
3. `report_builder.EXPLICITLY_NOT_CHECKED`: drop
   `target_record_count_parity` and `target_id_set_parity`.
   `EXPLICITLY_NOT_CHECKED` shrinks from 9 entries to 7. The three new
   parity checks (`target_document_hash_parity`,
   `target_metadata_parity`, `target_embedding_presence`) are **not**
   added to that list — they are real checks that always run when
   `ctx.reconstruction_result` is set.
4. New `AnomalyType` members for the failure modes (§4.7).
5. New `CheckSkipped.reason` literal `reconstruction_not_run` (added to
   the `SkippedReason` Literal in `validation/_types.py`).
6. `tests/test_validation_parity.py` covering every check, every
   failure mode, the read-only invariant, and the embedding
   inconclusive path.
7. Extension to `tests/test_reconstruction_purity.py` (rename to
   `test_chromadb_isolation.py` not required — keep the file, add a
   second AST assertion: only `reconstruction/_writer.py` **and**
   `validation/parity.py` may import chromadb at module level).

Not in scope (do **not** silently add):

- **No retrieval-parity check** (querying the rebuilt collection by
  embedding/text and comparing top-k). Out of scope per ROADMAP
  non-goals.
- **No usage-scenario / MCP-runtime check.** Same.
- **No embedding-vector equality check.** Embeddings are re-derived by
  chromadb 1.x from documents; vector equality is meaningless across
  the version pair. M11 only checks **presence** (best-effort,
  inconclusive when the include set is unavailable).
- **No new exit code.** A failed parity check raises no error; it
  produces a `failed`/`inconclusive` `CheckOutcome` and a HIGH-or-below
  anomaly. The migrate run still exits 0 if everything else succeeded
  — parity failures degrade `confidence_band` from HIGH to LOW (see
  §4.7), they do not abort the pipeline. This matches the
  long-standing "validation never raises" contract from M5.
- **No write to target.** Target is opened with chromadb's
  `PersistentClient` and only `.count()` and `.get(...)` are called.
  An AST test (§7.5) enforces this.
- **No new CLI flag.** The parity checks run automatically inside
  `step_validate` whenever `ctx.reconstruction_result` is set — i.e.
  on every successful `migrate` run.
- **No baseline regeneration / no adversarial corpus extension.** M12
  owns that. The hardening baselines and adversarial fixtures are
  **not** modified by M11.
- **No change to `REPORT_SCHEMA_VERSION`.** The report shape stays
  identical: `validation.checks_performed` simply gains entries and
  `validation.checks_not_performed` simply loses them. The set of
  top-level keys is unchanged. This intentionally avoids a baseline
  drift that M12 would have to absorb.

---

## 2. Module layout

```
src/mempalace_migrator/validation/
    __init__.py        # already exists — wire in run_parity_checks
    _types.py          # already exists — extend SkippedReason
    structural.py      # untouched
    consistency.py     # untouched
    heuristics.py      # untouched
    parity.py          # NEW — 5 checks, the only new file in this milestone
```

Why a new module instead of folding into `consistency.py`: the
`consistency` family is defined as "cross-checks between sub-structures
of the same in-memory state". Parity checks are categorically different
— they cross a **process/storage boundary** (chromadb on-disk
PersistentClient). Mixing them would dilute the family contract and
make the chromadb-import boundary harder to enforce by AST.

A new `CheckFamily` literal `"parity"` is added to
`validation/_types.py` (§3.1). The text renderer needs no change —
it only displays family counts via the existing `summary_counts`
aggregation.

---

## 3. Data model changes

### 3.1 `validation/_types.py`

```python
CheckFamily = Literal["structural", "consistency", "heuristic", "parity"]
SkippedReason = Literal[
    "stage_not_implemented",  # kept for back-compat (no longer emitted)
    "input_missing",
    "out_of_scope_m5",
    "reconstruction_not_run",  # NEW — emitted when ctx.reconstruction_result is None
]
```

`stage_not_implemented` stays in the literal so that any frozen
fixtures that compare strings keep parsing; the value is simply no
longer produced by the system after M11. `_SKIPPED_RECONSTRUCTION`
loses its only consumer in `validation/__init__.py` (§6.2).

### 3.2 `MigrationContext`

No new fields. `ctx.reconstruction_result` already exists. `parity.py`
reads `ctx.transformed_data` and `ctx.reconstruction_result` only.

### 3.3 No new top-level report keys

`report["validation"]["checks_performed"]` simply gains up to five
entries with `family="parity"`. No code in `report_builder.py` needs
to change beyond removing two literals from `EXPLICITLY_NOT_CHECKED`.

---

## 4. Parity checks

All five live in `validation/parity.py`. Each returns a `CheckOutcome`
with `family="parity"`. Each may emit at most one anomaly per check
into `ctx`. Anomaly evidence caps at 20 listed ids; the total count
goes in `summary` of the evidence `data` field (matches the M9
convention used by `wing_room_counts` etc.).

### 4.1 Common machinery

`_open_target_readonly(target_path, collection_name)` — the only
function in `parity.py` that touches chromadb. Returns
`(client, collection)`. Wrapped in `try`/`except`. On open failure:

- Every parity check is recorded as `inconclusive` with evidence
  `kind="target_open_failed"`.
- A single anomaly `TARGET_OPEN_FAILED/HIGH` is emitted (not one per
  check — that would be five copies of the same failure).
- No further chromadb calls are attempted.

`_iter_target_records(collection)` — yields `(id, document, metadata)`
pages of size 500 via `collection.get(limit=500, offset=N,
include=["documents", "metadatas"])`. **Page size matches the
reconstruction `BATCH_SIZE`** so memory pressure is symmetric. No
embeddings included here — the embedding-presence check uses its own
paged scan with `include=["embeddings"]` because that include set may
fail on some chromadb builds (see §4.6).

`collection.add` / `collection.update` / `collection.delete` are
**never imported** from chromadb in this module. The AST test (§7.5)
asserts this by walking `parity.py`'s top-level and inner calls
looking for any attribute access matching those names on a chromadb
object.

### 4.2 `parity.target_record_count_parity`

- **Lifted from** `_SKIPPED_RECONSTRUCTION`.
- **Inputs:** `ctx.transformed_data.summary.drawer_count`,
  `collection.count()`.
- **Pass:** equal.
- **Fail:** unequal. Anomaly `TARGET_RECORD_COUNT_MISMATCH/HIGH` with
  evidence carrying `{expected, actual}`.
- **Inconclusive:** target open failed (already covered by §4.1).

### 4.3 `parity.target_id_set_parity`

- **Lifted from** `_SKIPPED_RECONSTRUCTION`.
- Builds `set(transformed_ids)` from `ctx.transformed_data.drawers`
  and `set(target_ids)` from a paged scan.
- `missing_in_target = transformed_ids - target_ids`,
  `unexpected_in_target = target_ids - transformed_ids`.
- **Pass:** both empty.
- **Fail:** either non-empty. Anomaly `TARGET_ID_SET_MISMATCH/HIGH`.
  Evidence data: `{missing_in_target_count, unexpected_in_target_count,
  missing_sample (≤20), unexpected_sample (≤20)}`. Samples are the
  first 20 ids in **sorted** order so output is deterministic and
  test-stable.
- **Inconclusive:** target open failure.

### 4.4 `parity.target_document_hash_parity`

- **New.**
- Builds `expected_hashes: dict[str, str]` from
  `ctx.transformed_data.drawers` where `value =
  sha256(drawer.document.encode("utf-8")).hexdigest()`. Documents are
  already normalised by the transformer (str, non-empty). No
  whitespace normalisation here — M9's transformer is the canonical
  normaliser; M11 hashes the same bytes the writer wrote.
- Compares against the same hash applied to each target document, on
  the **intersection** of id sets. Drawers missing from the target are
  already reported by §4.3 and not double-counted here.
- **Pass:** every shared id has matching hash.
- **Fail:** ≥1 mismatch. Anomaly
  `TARGET_DOCUMENT_HASH_MISMATCH/HIGH` with
  `{mismatch_count, mismatch_sample (≤20 ids in sorted order)}`.
- **Inconclusive:** target open failure, or shared-id set is empty
  (which means §4.3 already failed catastrophically).

### 4.5 `parity.target_metadata_parity`

- **New.**
- For each shared id, compares
  `ctx.transformed_data.drawers[i].metadata` to the target's
  `metadata` dict. The transformer already coerced empty dicts (M9)
  and the writer already coerced `{}` → `None` for chromadb 1.5.7
  (M10 caveat). M11 normalises both sides identically before
  comparison: `meta_for_compare(m) = m or {}` so an empty dict on the
  transformed side compares equal to a `None` (or empty dict) returned
  by chromadb.
- Pure dict equality otherwise — keys and primitive-typed values
  (str/int/float/bool) by `==`. M9 has already rejected lists, dicts,
  and `None` values, so `==` is well-defined.
- **Pass:** all shared ids have matching normalised metadata.
- **Fail:** ≥1 divergence. Anomaly `TARGET_METADATA_MISMATCH/HIGH`.
  Evidence data: `{mismatch_count, mismatch_sample (≤20)}` where each
  sample entry is `{id, missing_keys_in_target, extra_keys_in_target,
  value_diff_keys}`. Values themselves are **not** echoed (could be
  large; could be PII). Only the key-level diff is structured.
- **Inconclusive:** target open failure, or shared-id set is empty.

### 4.6 `parity.target_embedding_presence`

- **New, best-effort.** Severity capped at MEDIUM — chromadb 1.x
  re-derives embeddings server-side and there are documented builds
  where `include=["embeddings"]` raises. M11 must not lower overall
  confidence to LOW because of an inconclusive embedding probe.
- First page: `collection.get(limit=1, include=["embeddings"])` to
  probe whether the include set is supported. If that call raises,
  the check is **inconclusive** with `kind="embedding_include_unsupported"`
  and a single MEDIUM anomaly `TARGET_EMBEDDING_PROBE_INCONCLUSIVE/MEDIUM`.
  No further pages fetched.
- If the probe succeeds: paged scan with the same include set; an id
  whose embedding is `None` or an empty list is recorded as missing.
- **Pass:** every id has a non-empty embedding.
- **Fail:** ≥1 missing. Anomaly `TARGET_EMBEDDING_MISSING/MEDIUM`
  with `{missing_count, missing_sample (≤20)}`.
- Severity cap MEDIUM means a fail demotes `confidence_band` to
  MEDIUM at worst, not LOW. This is the right honesty surface: the
  migration succeeded, embeddings will be re-derived on first read,
  but we surfaced the gap.

### 4.7 New `AnomalyType` members

Added under a new `# --- Validation: target parity ---` section in
`core/context.py`:

```
TARGET_OPEN_FAILED                  = "target_open_failed"
TARGET_RECORD_COUNT_MISMATCH        = "target_record_count_mismatch"
TARGET_ID_SET_MISMATCH              = "target_id_set_mismatch"
TARGET_DOCUMENT_HASH_MISMATCH       = "target_document_hash_mismatch"
TARGET_METADATA_MISMATCH            = "target_metadata_mismatch"
TARGET_EMBEDDING_MISSING            = "target_embedding_missing"
TARGET_EMBEDDING_PROBE_INCONCLUSIVE = "target_embedding_probe_inconclusive"
```

Closed-registry contract preserved. Each member is exercised by at
least one test in §7.

### 4.8 Severity policy summary

| Check                                | Pass | Fail severity | Inconclusive cause                 |
|--------------------------------------|------|---------------|------------------------------------|
| target_record_count_parity           | ok   | HIGH          | target open failure                |
| target_id_set_parity                 | ok   | HIGH          | target open failure                |
| target_document_hash_parity          | ok   | HIGH          | open failure, empty intersection   |
| target_metadata_parity               | ok   | HIGH          | open failure, empty intersection   |
| target_embedding_presence            | ok   | MEDIUM        | open failure, include unsupported  |

`confidence_band` after M11 (`_compute_band` rule unchanged):

- Any failed-HIGH parity check ⇒ band drops to LOW.
- Any failed-MEDIUM (only embedding) or inconclusive parity check ⇒
  band drops to MEDIUM.
- Otherwise HIGH (matches today's behaviour).

This is the correct shape: a missing record is a data-loss event
(LOW); a missing embedding is a recomputation event (MEDIUM). The
semantics line up with the existing M5 `_compute_band` rule with no
code change there.

---

## 5. Read-only contract

Hard rules, all individually testable:

1. **No mtime mutation of source.** The source byte-identity test from
   M10 (`test_migrate_source_byte_identical`) is extended (see §7.6) to
   re-hash the source `chroma.sqlite3` after `step_validate` returns,
   not just after `step_reconstruct`. M11 must not change that hash.
2. **No mtime mutation of target.** A new test
   `test_validate_target_mtime_invariant` snapshots
   `os.stat(target_path / "chroma.sqlite3").st_mtime_ns` before
   `step_validate` and asserts equality after. Tolerates the chromadb
   client opening a new connection (which on most filesystems does
   not bump mtime; if it does on some platform, the test is gated by
   `pytest.mark.skipif` with a recorded reason — silent skips are
   forbidden, the skip message must include the platform).
3. **No `collection.add` / `update` / `delete` / `modify` / `upsert`
   in `parity.py`.** AST walk in
   `tests/test_reconstruction_purity.py` (extend, don't fork) asserts
   only those allowlisted method names appear: `count`, `get`,
   `name` (attribute, not method) and the chromadb client's
   `get_collection`. Any other chromadb attribute access is a
   regression.
4. **No `chromadb.PersistentClient(..., allow_reset=True)`.**
   `Settings(anonymized_telemetry=False, allow_reset=False)` is the
   only allowed `Settings(...)` call in `parity.py`. Asserted by the
   same AST test as a literal-keyword check.

---

## 6. Wiring changes

### 6.1 `core/context.py`

Add the seven new `AnomalyType` members (§4.7). Nothing else.

### 6.2 `validation/__init__.py`

Replace:

```python
_SKIPPED_RECONSTRUCTION: tuple[CheckSkipped, ...] = (
    CheckSkipped(id="target_record_count_parity", reason="stage_not_implemented"),
    CheckSkipped(id="target_id_set_parity", reason="stage_not_implemented"),
)
```

with a callable:

```python
def _skipped_when_no_reconstruction() -> tuple[CheckSkipped, ...]:
    return (
        CheckSkipped(id="target_record_count_parity",   reason="reconstruction_not_run"),
        CheckSkipped(id="target_id_set_parity",         reason="reconstruction_not_run"),
        CheckSkipped(id="target_document_hash_parity",  reason="reconstruction_not_run"),
        CheckSkipped(id="target_metadata_parity",       reason="reconstruction_not_run"),
        CheckSkipped(id="target_embedding_presence",    reason="reconstruction_not_run"),
    )
```

Inside `validate(ctx)`:

```python
if ctx.reconstruction_result is None:
    not_performed = _skipped_when_no_reconstruction()
else:
    parity_outcomes = run_parity_checks(ctx)  # may emit anomalies
    outcomes.extend(parity_outcomes)
    not_performed = ()
```

The reason rename (`stage_not_implemented` → `reconstruction_not_run`)
is intentional: M5's "stage is a stub" reason was honest then, but
post-M10 the stage exists; the only way to skip parity now is that the
**user** chose `analyze` or `inspect` instead of `migrate`. The reason
must reflect the runtime state, not the historical excuse.

The skip set widens from 2 to 5 entries when reconstruction did not
run. This is the honest surface for `analyze`/`inspect`: those
pipelines never reach reconstruction, so all five parity checks must be
listed as not-performed.

### 6.3 `report_builder.py`

```python
EXPLICITLY_NOT_CHECKED: tuple[str, ...] = (
    "sqlite_corruption_below_pragma_level",
    "embedding_vector_equivalence_source_to_target",
    "search_result_semantic_equivalence",
    "concurrent_access_absence_during_extraction",
    "target_chromadb_default_embedding_function_match",
    "hnsw_segment_file_integrity",
    "manifest_authenticity",
)  # 9 → 7
```

`test_explicitly_not_checked_unchanged` becomes
`test_explicitly_not_checked_post_m11`: assert length 7 and assert
neither parity literal is present. Update the assertion in
`tests/test_report_builder.py` accordingly.

### 6.4 `core/pipeline.py`

No change. `step_validate` already runs after `step_reconstruct` in
`MIGRATE_PIPELINE`. The only new behaviour is inside `validate()`.

### 6.5 `cli/main.py`

No change. No new flag, no new subcommand.

---

## 7. Test plan

All in `tests/`. No new test directory.

### 7.1 `test_validation_parity.py` (new)

Builds a real chromadb 1.5.7 PersistentClient via `_writer.py` (or via
a tiny shared fixture in `tests/conftest.py` — single source of
truth) so the tests exercise the real read path, not a mock. Per
check:

- **Pass case:** transformed bundle equals target → outcome `passed`,
  no anomaly.
- **Fail case:** mutate one side of the comparison (drop a drawer,
  flip one document byte, change one metadata value, delete one
  embedding via direct write to a *different* target dir built ad-hoc
  for the test) → outcome `failed`, anomaly emitted with correct type
  and severity.
- **Inconclusive case** for embedding probe: monkeypatch
  `collection.get` to raise on `include=["embeddings"]` → outcome
  `inconclusive`, anomaly `TARGET_EMBEDDING_PROBE_INCONCLUSIVE`.
- **Target-open failure:** point `ctx.reconstruction_result` at a
  non-existent path → all five outcomes are `inconclusive`, exactly
  one `TARGET_OPEN_FAILED` anomaly emitted.

### 7.2 `test_validation.py` (extend)

- Update `test_skipped_reconstruction_*` to assert the **5** new
  skipped entries with reason `reconstruction_not_run` when
  `ctx.reconstruction_result is None`.
- Add a happy-path migrate-fixture test: after a successful
  reconstruction, `validation.checks_not_performed` is empty (parity
  group ran) and `confidence_band == "HIGH"`.

### 7.3 `test_report_builder.py` (extend)

- Assert `len(EXPLICITLY_NOT_CHECKED) == 7`.
- Assert `"target_record_count_parity" not in EXPLICITLY_NOT_CHECKED`.
- Assert `"target_id_set_parity" not in EXPLICITLY_NOT_CHECKED`.
- Assert no parity literal is added (the three new checks are real
  checks, not disclaimers).

### 7.4 `test_cli_migrate.py` (extend)

- Assert that on a successful migrate, the report's
  `validation.checks_performed` contains five entries with
  `family == "parity"` and all are `passed`.
- Assert exit 0 and `confidence_summary.overall_band == "HIGH"`.

### 7.5 `test_reconstruction_purity.py` (extend, do not fork)

Rename the test name `test_only_writer_imports_chromadb` to
`test_chromadb_import_allowlist` and assert the allowlist is
exactly `{"src/mempalace_migrator/reconstruction/_writer.py",
"src/mempalace_migrator/validation/parity.py"}`. Walk all
`*.py` files in `src/mempalace_migrator/`, parse with `ast`,
fail on any module-level `import chromadb` / `from chromadb...`
outside the allowlist.

Add a second AST walk over `parity.py` only:
- Allowed call attribute names on chromadb-derived objects:
  `get_collection`, `count`, `get`, `name`.
- Forbidden: `add`, `update`, `upsert`, `delete`, `modify`, `reset`,
  `create_collection`, `delete_collection`, `peek`.
- Forbidden literal: `allow_reset=True` in any
  `Settings(...)` call.

The walk operates on attribute names (not on the object's runtime
type, which AST cannot know) — this is a structural over-approximation
intentionally biased toward false positives over false negatives.

### 7.6 `test_reconstruction_atomicity.py` (extend)

The existing source-byte-identity test runs the whole `migrate`
pipeline. Extend it to re-hash the source `chroma.sqlite3` **after**
`step_validate` (today it hashes after `step_reconstruct`). The
target mtime invariant test (§5.2) is colocated here.

### 7.7 Negative tests

- `analyze` / `inspect` runs: `validation.checks_not_performed`
  contains all 5 parity entries with reason
  `reconstruction_not_run`; no parity anomaly emitted.
- `migrate` with mid-batch failure (existing M10 fixture): rollback
  triggers, `step_validate` still runs, but `ctx.reconstruction_result`
  is `None` because `reconstruct` raised — so `checks_not_performed`
  shows the 5 skip entries. No parity anomaly emitted, no chromadb
  open attempt at the (now-removed) target path.

---

## 8. Failure modes M11 must surface

| Failure mode                                              | Surfaced as                                                | Severity |
|-----------------------------------------------------------|------------------------------------------------------------|----------|
| Target collection has fewer/more rows than transformed    | `TARGET_RECORD_COUNT_MISMATCH` + check `failed`            | HIGH     |
| Target collection missing some transformed ids            | `TARGET_ID_SET_MISMATCH` + check `failed`                  | HIGH     |
| Target collection has unexpected ids                      | same anomaly, separate evidence sample list                | HIGH     |
| Document text drift (byte-level diff after sha256)        | `TARGET_DOCUMENT_HASH_MISMATCH` + check `failed`           | HIGH     |
| Metadata key/value divergence                             | `TARGET_METADATA_MISMATCH` + check `failed`                | HIGH     |
| Embedding entry empty/missing for some ids                | `TARGET_EMBEDDING_MISSING` + check `failed`                | MEDIUM   |
| chromadb build does not support `include=["embeddings"]`  | `TARGET_EMBEDDING_PROBE_INCONCLUSIVE` + check `inconclusive` | MEDIUM |
| Target directory unreadable / chromadb open fails         | `TARGET_OPEN_FAILED` + 5× `inconclusive`                   | HIGH     |

Failure modes **not** in scope (kept in `EXPLICITLY_NOT_CHECKED`):
- HNSW segment file integrity (binary sqlite-internal layout)
- Embedding-vector numeric equivalence across the version pair
- Default-embedding-function identity match
- Manifest authenticity (signing)

---

## 9. Risks and uncertainty

1. **`include=["embeddings"]` API stability.** chromadb 1.5.7 supports
   it on tested builds, but there is no public stability guarantee
   across patch versions. Mitigation: §4.6 falls back to
   `inconclusive` rather than raising. Risk: silent
   inconclusive-everywhere if the API changes. Mitigation: M12 must
   add a hardening check that asserts at least one fixture run
   produces `TARGET_EMBEDDING_MISSING` outcome `passed` (i.e. the
   probe worked).
2. **Empty-dict metadata coercion.** M10 caveat: writer turns `{}` →
   `None` for chromadb 1.5.7. M11's metadata comparator must
   normalise both sides via `m or {}` (§4.5). Without this, every
   empty-metadata drawer would falsely fail
   `TARGET_METADATA_MISMATCH`. Pinned by a dedicated unit test in
   §7.1.
3. **Page boundary determinism.** chromadb 1.x `collection.get(limit,
   offset)` order is **not** documented as stable. M11 sorts ids on
   both sides before comparison (set diff is order-independent for
   §4.3; explicit per-id lookup for §4.4 and §4.5). No test relies on
   page-iteration order.
4. **Hash collision.** sha256 over UTF-8 bytes; collision risk is
   cryptographically negligible. Not mitigated.
5. **Memory pressure.** A million-drawer palace would materialise two
   id sets (~64 MB at 64-byte ids) in memory. M11 accepts this — the
   migrator already loads the full transformed bundle into memory in
   M9. Out-of-process streaming is out of scope (would belong to a
   future M-series, not M11).
6. **chromadb-version drift.** `parity.py` calls `chromadb.__version__`
   only inside `_open_target_readonly` and stores it nowhere. The
   target manifest already records the writer's chromadb version
   (M10). Reader's chromadb version is the same module load — no
   drift possible within a single process.

---

## 10. Explicit non-changes (regression guard)

These files/strings must be **byte-identical** before/after M11
(asserted by `git diff` review, not by automation):

- `tests/hardening/baselines/runtime_envelope.json`
- `tests/hardening/baselines/report_signatures.json`
- `src/mempalace_migrator/cli/main.py`
- `src/mempalace_migrator/core/pipeline.py`
- `src/mempalace_migrator/reconstruction/` (entire directory)
- `pyproject.toml`
- `REPORT_SCHEMA_VERSION` (stays at 4 — see §1 note; the recorded "5"
  in TODO/ROADMAP appears to be aspirational text from M10 that did
  not reach the code; M11 does not adjudicate that drift, M12 will
  reconcile with the baseline regeneration).
- `EXPLICITLY_NOT_CHECKED` shrinks from 9 to 7 — this **is** a change,
  but a tightly-scoped one in `report_builder.py`.

---

## 11. Implementation order

1. Extend `core/context.py` with the 7 new `AnomalyType` members.
2. Extend `validation/_types.py` with `CheckFamily="parity"` and the
   `reconstruction_not_run` literal.
3. Write `validation/parity.py` with the 5 checks + `_open_target_readonly`
   + `_iter_target_records`.
4. Wire `validation/__init__.py` to call `run_parity_checks` when
   `ctx.reconstruction_result` is non-None and emit the 5-entry skip
   list otherwise.
5. Shrink `EXPLICITLY_NOT_CHECKED` in `report_builder.py`.
6. Land `test_validation_parity.py` and extend
   `test_validation.py`, `test_report_builder.py`,
   `test_cli_migrate.py`, `test_reconstruction_purity.py`,
   `test_reconstruction_atomicity.py`.
7. Run full suite; expected delta: +N tests (≈ 25), zero existing-test
   modifications other than the four enumerated above.

---

## 12. Exit-gate checklist (what M11 done means)

- [ ] `validation/parity.py` exists, contains the 5 checks, is the
      only `validation/*.py` that imports chromadb, and the AST test
      enforces the import allowlist.
- [ ] `target_record_count_parity` and `target_id_set_parity` are
      `CheckOutcome` entries (not `CheckSkipped`) on every successful
      `migrate` run; absent (with reason `reconstruction_not_run`) on
      every `analyze` / `inspect` run.
- [ ] `target_document_hash_parity`, `target_metadata_parity`,
      `target_embedding_presence` are `CheckOutcome` entries on every
      successful `migrate` run; embedding check may be `inconclusive`
      with the documented evidence kind on chromadb builds that reject
      `include=["embeddings"]`.
- [ ] `EXPLICITLY_NOT_CHECKED` has exactly 7 entries; neither
      `target_record_count_parity` nor `target_id_set_parity` is in
      it. The three new parity check ids are **not** in it either.
- [ ] No `collection.add` / `update` / `upsert` / `delete` / `modify`
      / `reset` / `create_collection` / `delete_collection` / `peek`
      call site in `validation/parity.py` (AST-asserted).
- [ ] Source `chroma.sqlite3` sha256 is byte-identical before
      `step_detect` and after `step_validate` on a successful
      `migrate` run.
- [ ] Target `chroma.sqlite3` mtime is identical before
      `step_validate` and after `step_validate` (or test is gated with
      a non-silent platform skip).
- [ ] On a successful migrate, `confidence_summary.overall_band ==
      "HIGH"` and `validation.summary_counts == {"passed": N,
      "failed": 0, "inconclusive": 0}` (where N includes the 5 parity
      checks).
- [ ] On an artificially-divergent target (test-only mutation of the
      built chromadb dir between reconstruct and validate), parity
      checks produce `failed` outcomes with the correct anomaly types
      and `confidence_band` drops appropriately (LOW for HIGH-severity
      failure, MEDIUM for embedding-only failure).
- [ ] Full test suite green. No new exit code, no new CLI flag, no
      new pipeline stage, no schema-version bump, no baseline
      regeneration.
- [ ] `TODO.json` `phases[14].status` flipped to `"done"`;
      `ROADMAP.json` `current_position` advanced to `M11_done`,
      `next_target` set to `M12`. Implementation status string lists
      the new test count and the shrunk `EXPLICITLY_NOT_CHECKED`
      count.
