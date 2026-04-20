# M9 — Transformation Stage (design)

Status: **design recorded, implementation pending**.
Owner phase: TODO.json `phases[12]`.
Roadmap milestone: ROADMAP.json `M9`.
Predecessors satisfied: M1–M8 (718 tests green).

This document fixes the strategy for implementing `step_transform` so that
review and implementation can proceed in lockstep. M9 implements the
**pure, in-memory** transformation stage. It does **not** add any writer,
any chromadb dependency, or any new CLI surface. Reconstruction (M10),
target parity (M11), and write-path adversarial work (M12) are explicitly
out of scope.

---

## 1. Scope (what M9 lands)

In scope:

1. A new typed bundle (`TransformedBundle`) stored on `ctx.transformed_data`.
2. A pure `transform(ctx)` function in `src/mempalace_migrator/transformation/`.
3. Replacement of the `NOT_IMPLEMENTED/LOW` stub in `step_transform` with a
   real call into `transformation.transform`.
4. New closed-registry `AnomalyType` members for the four transformation
   failure modes.
5. A new `TransformError` subclass of `MigratorError` (stage = `"transform"`,
   reachable via the existing exit code 4 already pinned by M6.4).
6. A real `report["transformation"]` section (currently hard-coded to `None`
   in `report_builder.build_report`).
7. A `Transformation` block in `text_renderer.render_text`.
8. Tests covering the contracts in §5.

Not in scope (carried forward):

- No `chromadb` dependency, no `chromadb.PersistentClient` call, no
  `collection.add`, no writer of any kind. Transformation is a pure
  function. Enforced structurally (§4.1).
- No bundle serialisation to disk (`drawers.jsonl` from the prototype is
  out of scope per ROADMAP non-goals).
- No new CLI subcommand. `analyze` and `inspect` keep their current
  pipeline semantics; `inspect` will now record `transform: executed` in
  the `stages` section instead of `skipped`.
- No new exit code. `TransformError` reuses the already-reserved exit 4.
- No new severity. Anomalies use `HIGH` (record dropped),
  `MEDIUM` (coerced/normalised with loss), and `CRITICAL` only on raise.
- No new validation check family. Validation continues to consume
  `ctx.extracted_data` only. Adding transform-side validation checks is
  deferred to a later milestone (see §10 open question O1).

---

## 2. Module layout

```
src/mempalace_migrator/transformation/
    __init__.py            # re-exports: transform, TransformedBundle,
                           # TransformedDrawer, TransformedSummary
    _types.py              # frozen dataclasses (no logic)
    _normalize.py          # normalize_metadata + helpers (pure)
    _analyze.py            # analyze_drawers + helpers (pure)
    transformer.py         # transform(ctx) — orchestrates the above and
                           # emits anomalies
```

Splitting into four files mirrors the validation/ layout (`structural.py`,
`consistency.py`, `heuristics.py`, `_types.py`) and keeps each module
unit-testable in isolation. No module under `transformation/` may
`import chromadb` (§4.1).

---

## 3. Data model

All dataclasses are `frozen=True`. All collections inside them are
`tuple` (not `list`) so the bundle is hashable-friendly and immutable
once produced. The dicts inside `TransformedDrawer.metadata` and
`TransformedBundle.collection_metadata` are JSON-safe by contract
(values are `str | int | float | bool` only).

```python
@dataclass(frozen=True)
class TransformedDrawer:
    id: str                                # non-empty, no control chars
    document: str                          # non-empty string
    metadata: dict[str, str | int | float | bool]


@dataclass(frozen=True)
class LengthProfile:
    min: int
    max: int
    mean: float                            # rounded to 2 decimals
    p50: int
    p95: int


@dataclass(frozen=True)
class TransformedSummary:
    drawer_count: int
    sample_ids: tuple[str, ...]            # capped at 20, sorted, deterministic
    metadata_keys: tuple[str, ...]         # union across drawers, sorted
    wing_room_counts: tuple[tuple[str, str, int], ...]   # (wing, room, count), sorted
    length_profile: LengthProfile
    dropped_count: int                     # number of input drawers excluded
    coerced_count: int                     # number of drawers that survived
                                           # with at least one metadata coercion


@dataclass(frozen=True)
class TransformedBundle:
    collection_name: str                   # always EXPECTED_COLLECTION_NAME
    collection_metadata: dict[str, str | int | float | bool]
    drawers: tuple[TransformedDrawer, ...]
    summary: TransformedSummary
```

Notes:

- `wing` and `room` are conventional palace metadata keys produced by the
  upstream MemPalace MCP; `wing_room_counts` is a structural breakdown
  for the report. If `wing` or `room` is absent from a drawer, that
  drawer simply does not contribute to `wing_room_counts` — its absence
  is **not** an anomaly (the migrator is data-shape-agnostic about
  user-defined keys; only chroma 1.x type rules are enforced).
- `collection_metadata` is empty `{}` in M9. Source-side collection
  metadata is not currently extracted; passing `{}` matches the
  prototype's behaviour and avoids inventing a value chroma 1.x would
  reject. If extraction is later extended to surface collection metadata,
  transformation will pass it through unchanged.
- `length_profile` is computed over `len(document)` (Unicode code points,
  not bytes). The prototype uses byte length; we deliberately diverge
  because chroma 1.x stores documents as Python `str`. Recorded as a
  conscious change, not a bug-for-bug port.

---

## 4. Pure-function contract

### 4.1 No writer dependency

A new test (`tests/test_transformation_purity.py`) walks the AST of every
file under `src/mempalace_migrator/transformation/` and asserts that no
`Import` or `ImportFrom` node references `chromadb`, `sqlite3`, `os`,
`pathlib.Path` (for filesystem use; `Path` as a type is not used here),
`shutil`, `tempfile`, or `open`. This is the same discipline as the M8
logging-discipline AST walk (`tests/hardening/test_logging_discipline.py`).
Aliased imports must be caught — regex search is forbidden.

### 4.2 Determinism

`transform(ctx)` must be deterministic w.r.t. its inputs:

- Drawer ordering in `bundle.drawers` follows `ctx.extracted_data.drawers`
  ordering (which is itself sorted by `embedding_pk` per
  `chroma_06_reader._read_drawers_resilient`).
- `sample_ids`, `metadata_keys`, `wing_room_counts` are sorted before
  being frozen into tuples.
- The order in which anomalies are emitted is the same as the drawer
  iteration order. Stable order is asserted by an integration test so
  that report-signature stability (M8.4) keeps holding.

### 4.3 Idempotence on rejection

Calling `transform(ctx)` twice on the same `ctx` is forbidden (it would
double-emit anomalies). The pipeline guarantees single execution per
run; the test suite asserts this by running the full pipeline once and
checking that no `transform`-stage anomaly is duplicated.

---

## 5. Per-drawer normalisation rules

Inputs come from `ExtractionResult.drawers` (`tuple[DrawerRecord, ...]`).
By the time transformation sees them, extraction has already filtered:
blank ids, control-char ids, duplicate ids, NULL documents, missing
documents, all-NULL metadata values, orphaned embeddings. Transformation
**must not assume** these filters were applied — it re-validates each
drawer defensively, because an extractor regression must surface here as
a structured drop, not a downstream chromadb error.

For each `DrawerRecord d` (in order):

| Check | Failure → AnomalyType | Severity | Action |
|-------|------------------------|----------|--------|
| `d.id` is non-empty `str` with no control chars | `TRANSFORM_DRAWER_DROPPED` (reason=`invalid_id`) | HIGH | drop |
| `d.document` is non-empty `str` | `TRANSFORM_DRAWER_DROPPED` (reason=`invalid_document`) | HIGH | drop |
| `d.metadata` keys are all `str` | `TRANSFORM_DRAWER_DROPPED` (reason=`non_string_metadata_key`) | HIGH | drop |
| `d.metadata` values are `str \| int \| float \| bool` (post-normalisation) | see below | — | — |
| `d.id` not already seen in this run | `TRANSFORM_DUPLICATE_ID_DROPPED` | HIGH | drop later occurrences |

For metadata value normalisation (`normalize_metadata`):

| Input value type | Outcome | Anomaly |
|-------------------|---------|---------|
| `str`, `int`, `float`, `bool` | pass through unchanged | none |
| `None` | drop the **drawer** (chroma 1.x rejects None) | `TRANSFORM_DRAWER_DROPPED` (reason=`metadata_value_none`), HIGH |
| `list`, `tuple`, `dict`, `set`, any other type | drop the **drawer** | `TRANSFORM_DRAWER_DROPPED` (reason=`unsupported_metadata_value_type`), HIGH |
| `bool` masquerading as `int` | pass through as `bool` (Python's `True == 1`; we preserve original type via `type(v) is bool` check before `int`) | none |
| `int` outside `[-2**63, 2**63-1]` (sqlite/chroma int range) | coerce to `str(v)` | `TRANSFORM_METADATA_COERCED` (reason=`int_out_of_range`), MEDIUM |
| `float` that is `NaN` or `±Inf` | drop the **drawer** (chroma rejects non-finite floats) | `TRANSFORM_DRAWER_DROPPED` (reason=`non_finite_float`), HIGH |

`TRANSFORM_METADATA_COERCED` is the **only** path that mutates a value
without dropping the row. Every coercion is reported with the original
value (truncated `repr`, capped at 200 chars) and the new value, so
downstream readers can audit. The `coerced_count` field of
`TransformedSummary` is the cardinality of drawers that received at
least one coercion (not the number of coerced values).

A drop emits **exactly one** anomaly per drawer (M3 contract). If a
drawer fails multiple checks, only the first observed failure is
reported, matching the extraction stage's per-row policy.

---

## 6. New AnomalyType members

Added to `core/context.py` in a new section:

```python
# --- Transformation ---
TRANSFORM_INPUT_MISSING = "transform_input_missing"
TRANSFORM_DRAWER_DROPPED = "transform_drawer_dropped"
TRANSFORM_METADATA_COERCED = "transform_metadata_coerced"
TRANSFORM_DUPLICATE_ID_DROPPED = "transform_duplicate_id_dropped"
```

Closed-registry contract (M3) is preserved: any reason string used inside
a `TRANSFORM_DRAWER_DROPPED` anomaly lives in `evidence.data["reason"]`,
**not** as a new enum member. Reasons are: `invalid_id`,
`invalid_document`, `non_string_metadata_key`, `metadata_value_none`,
`unsupported_metadata_value_type`, `non_finite_float`. The set of reasons
is documented as a module-level constant `TRANSFORM_DROP_REASONS` so the
report builder can validate it without parsing strings.

---

## 7. Errors and exit codes

A new `TransformError(MigratorError)` is added to `core/errors.py`.
`step_transform` raises it only on **unrecoverable** conditions:

- `ctx.extracted_data is None` — ⇒ `TransformError(stage="transform",
  code="transform_input_missing", ...)`. This is a defensive guard; in
  practice extraction always runs first or aborts the pipeline. The
  existing pipeline plumbing converts this to a CRITICAL anomaly +
  exit 4 (already covered by M6.4 tests).

Per-record drops do **not** raise. They emit `HIGH` anomalies and
contribute to `summary.dropped_count`. A bundle with `drawer_count == 0`
is **not** a raise condition either — the exit code is determined by
existing rules (`_decide_exit_code` already exits 8 if a CRITICAL exists
without a raise; here no CRITICAL is produced, so exit 0). A
zero-drawer bundle is, however, surfaced by the validation heuristic
`VALIDATION_EMPTY_SOURCE` (already implemented), which reads
`extracted_data.parsed_count` — that path is unaffected. If we ever want
"all drawers rejected at transform" to fail loudly, that becomes a new
heuristic check in M11/M12, not in M9.

---

## 8. Pipeline + report integration

### 8.1 `step_transform`

```python
def step_transform(ctx: MigrationContext) -> None:
    if ctx.extracted_data is None:
        raise TransformError(
            stage="transform",
            code="transform_input_missing",
            summary="extraction did not produce a result; cannot transform",
        )
    ctx.transformed_data = transformation.transform(ctx)
```

The `NOT_IMPLEMENTED/LOW` stub anomaly is removed. The `stages` section
of the report (computed by `_stages_section` in `report_builder.py`) will
now classify `transform` as `executed` whenever `ctx.transformed_data` is
non-None — no change to `_stages_section` required, the existing rule
already produces the right answer.

### 8.2 `report["transformation"]`

`report_builder._transformation_section(ctx)` is added (mirrors
`_extraction_section`):

```python
def _transformation_section(ctx: MigrationContext) -> dict[str, Any] | None:
    tb = ctx.transformed_data
    if tb is None:
        return None
    s = tb.summary
    return {
        "collection_name": tb.collection_name,
        "drawer_count": s.drawer_count,
        "dropped_count": s.dropped_count,
        "coerced_count": s.coerced_count,
        "sample_ids": list(s.sample_ids),
        "metadata_keys": list(s.metadata_keys),
        "wing_room_counts": [
            {"wing": w, "room": r, "count": c} for w, r, c in s.wing_room_counts
        ],
        "length_profile": {
            "min": s.length_profile.min,
            "max": s.length_profile.max,
            "mean": s.length_profile.mean,
            "p50": s.length_profile.p50,
            "p95": s.length_profile.p95,
        },
    }
```

`build_report` replaces `"transformation": None` with
`"transformation": _transformation_section(ctx)`. The
`REPORT_TOP_LEVEL_KEYS` tuple is unchanged (the key already exists).
`REPORT_SCHEMA_VERSION` is **not** bumped: top-level shape is unchanged,
only a previously-`None` slot becomes populated. This matches the
schema-stability rule from the M8 design doc.

### 8.3 `text_renderer`

A new block, rendered after `extraction_stats`:

```
transformation: drawers=N dropped=D coerced=C metadata_keys=K
```

Skipped silently when `report["transformation"] is None` (analyze
pipeline, or aborted run before transform).

### 8.4 `confidence_summary`

Unchanged in M9. Transformation does not introduce a new confidence
band signal. Adding one (e.g. `1 - dropped/parsed`) is deferred — it
would be a new M5/heuristics check, not a transformation-stage
responsibility.

---

## 9. Test plan (matches TODO 12.8)

Files to add under `tests/`:

1. `test_transformation_normalize.py` — unit tests for
   `normalize_metadata` covering every row of the table in §5
   (pass-through types, None, list, dict, set, NaN, ±Inf, oversized
   int, bool/int discrimination). Uses no fixtures.

2. `test_transformation_analyze.py` — unit tests for `analyze_drawers`
   asserting deterministic `sample_ids` cap, sorted `metadata_keys`,
   `wing_room_counts` ordering, `length_profile` math (including
   p50/p95 on small inputs).

3. `test_transformation_transformer.py` — unit tests for `transform`
   with hand-built `MigrationContext` instances (no SQLite, no
   filesystem):
   - happy path: N valid drawers in, N valid drawers out, zero anomalies.
   - per-reason drops: one drawer per drop reason, asserting exactly one
     anomaly per drop with `reason` populated and `severity == HIGH`.
   - duplicate id: two drawers with the same id, second is dropped with
     `TRANSFORM_DUPLICATE_ID_DROPPED`.
   - coercion path: oversized int, surviving drawer, one
     `TRANSFORM_METADATA_COERCED/MEDIUM` anomaly, `coerced_count == 1`.
   - all-drop: every input drawer is invalid; bundle has
     `drawer_count == 0` but the function returns normally (no raise).
   - missing input: `ctx.extracted_data is None` ⇒ `TransformError`.

4. `test_transformation_purity.py` — AST walk asserting no forbidden
   imports under `transformation/` (§4.1). Asserts the test would fail
   if a deliberate bad import is injected (mutation-style guard via a
   sample bad source string parsed inline).

5. `test_pipeline_transform_integration.py` — end-to-end on a builder-
   produced minimal valid chroma 0.6 palace: assert `ctx.transformed_data
   is not None`, `report["transformation"]` is populated, `stages.transform.status
   == "executed"`, and no `NOT_IMPLEMENTED` anomaly with `stage='transform'`
   exists.

6. `test_report_text.py` — extend with a `"transformation:"` line
   assertion when the section is present, and absence when it is `None`.

7. `tests/hardening/baselines/report_signatures.json` — **must be
   re-baselined** via `tests/hardening/rebaseline.py` because the
   `inspect` corpus entries will now produce a populated
   `transformation` section. The re-baseline is a separate commit with
   reviewer sign-off, per M8 §3.3.

8. `tests/adversarial/` — **no changes in M9**. Write-path adversarial
   work is M12. Existing M7 invariants (no exit 10, no traceback w/o
   --debug, schema-stable, JSON-safe, etc.) re-run unchanged and must
   stay green.

---

## 10. Open questions / deferred decisions

- **O1**: should validation gain a `transform_drop_accounting` check
  asserting `bundle.summary.dropped_count + bundle.summary.drawer_count
  == ctx.extracted_data.parsed_count`? It would catch an internal bug
  in `transformer.py`. Deferred: it can be added in M11 alongside the
  target-parity checks without disrupting M9 scope. Not blocking.

- **O2**: should `collection_metadata` carry a provenance footprint
  (e.g. `{"mempalace_migrator_run_id": ctx.run_id}`)? Tempting for
  audit, but it introduces a side channel between metadata semantics
  and run identity — and chroma 1.x will then store run_id in the
  target. Decision: **no**; provenance belongs in the
  `target_manifest` (M10), not in collection_metadata.

- **O3**: prototype computes `length_profile` in bytes; we use Unicode
  code points. Recorded in §3 as a deliberate divergence. If a
  byte-length signal is later required (e.g. for sizing chroma 1.x
  storage budgets), add it as a separate field rather than overload
  `length_profile.min/max`.

- **O4**: NaN/Inf in metadata is treated as a drawer drop. Alternative
  would be a coercion to `str("nan")`/`str("inf")` matching prototype
  behaviour. Decision: **drop**, because chroma 1.x rejects non-finite
  floats and we prefer a structurally-honest drop over a silent loss
  of numeric semantics.

---

## 11. Failure modes the design rules out

- **Silent metadata coercion (lists, dicts, None → string).** The only
  coercion path is oversized int → string, which emits a MEDIUM
  anomaly. All other type mismatches drop the drawer with a HIGH
  anomaly. AST walk on `transformation/` would catch a future
  developer adding a `json.dumps` coercion shortcut (no `json` import
  is whitelisted).
- **Hidden record drops.** Every drop ⇒ exactly one
  `TRANSFORM_DRAWER_DROPPED` anomaly with `reason` in `evidence.data`.
  Test 3 (per-reason matrix) covers each path.
- **Module-level `chromadb` import.** Forbidden by §4.1 AST test.
- **Re-introduction of the `NOT_IMPLEMENTED` stub anomaly.** Test 5
  asserts no such anomaly exists for `stage='transform'` after a
  successful pipeline run.
- **Schema drift in the report.** `REPORT_TOP_LEVEL_KEYS` is unchanged;
  `_transformation_section` returns a stable dict shape; report-
  signature baseline is re-recorded once, then locked.
- **Crash on the `analyze` pipeline.** `analyze` does not include
  `step_transform`; `ctx.transformed_data` stays `None`;
  `report["transformation"]` stays `None`; `stages.transform.status`
  becomes `not_run` (no longer `skipped`, since the stub anomaly is
  gone). Existing analyze-path tests must be updated to assert
  `not_run` in lieu of `skipped` for the transform stage.

---

## 12. Exit-gate checklist (what M9-done means)

M9 is **only** done when *all* of the following hold:

- [ ] `src/mempalace_migrator/transformation/` contains
      `_types.py`, `_normalize.py`, `_analyze.py`, `transformer.py`,
      and an `__init__.py` re-exporting the public surface.
- [ ] `transform(ctx)` is pure: no I/O, no `chromadb`, no `sqlite3`,
      no filesystem access (asserted by the AST test in §4.1).
- [ ] `step_transform` no longer emits the `NOT_IMPLEMENTED/LOW`
      stub anomaly. Asserted by integration test.
- [ ] `TransformError` exists in `core/errors.py` and is raised on
      missing input; exit code 4 is reachable on a `migrate`-less
      pipeline only via this raise. The existing exit-code tests
      cover this without modification.
- [ ] Four new `AnomalyType` members are present in the enum and
      every code path that emits one of them is unit-tested.
- [ ] `report["transformation"]` is a populated dict on every
      `inspect` corpus entry where extraction produced ≥1 drawer;
      `None` otherwise.
- [ ] `text_renderer` renders a `transformation:` line when the
      section is present and skips silently otherwise.
- [ ] `tests/hardening/baselines/report_signatures.json` has been
      regenerated via `tests/hardening/rebaseline.py` in a
      reviewed commit; baseline diff is limited to the
      `transformation` slot becoming populated.
- [ ] Full suite green (existing 718 tests still pass + new tests).
- [ ] `tests/TODO.json` `phases[12].status` flipped to `done` and
      `ROADMAP.json.current_position.completed_milestones` extended
      with `M9`. **Not before** every box above is ticked.

Out of M9 scope (must remain stubbed/unimplemented at exit):

- `step_reconstruct` — still emits its `NOT_IMPLEMENTED/LOW` stub.
- `report["reconstruction"]` — still `None`.
- `target_path`, `chromadb` dependency, `migrate` CLI subcommand,
  target parity validation checks (`target_record_count_parity`,
  `target_id_set_parity` remain in `EXPLICITLY_NOT_CHECKED`).
- `tests/adversarial/` — unchanged in M9.
