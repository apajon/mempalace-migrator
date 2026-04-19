# Architecture

Production-grade reconstruction tool. Read source palace, rebuild target
palace, report everything observed. No monolithic scripts. Strict
boundaries between stages. Traceability is the primary design constraint.

---

## 1. Directory tree

```
src/mempalace_migrator/
├── __init__.py
├── cli/                  # Process boundary: argv -> exit code
│   ├── __init__.py
│   └── main.py
├── core/                 # Cross-cutting primitives. No I/O.
│   ├── __init__.py
│   ├── context.py        # MigrationContext, Anomaly, Severity
│   ├── errors.py         # MigratorError hierarchy (stage + code)
│   └── pipeline.py       # Step type, run_pipeline, ANALYZE/FULL pipelines
├── detection/            # Identify the source palace.
│   ├── __init__.py
│   └── format_detector.py
├── extraction/           # Read source palace (read-only).
│   ├── __init__.py
│   └── chroma_06_reader.py
├── transformation/       # Pure data shaping. No I/O. (stub)
│   └── __init__.py
├── reconstruction/       # Write target palace via Chroma 1.x client. (stub)
│   └── __init__.py
├── validation/           # Post-write checks against target. (stub)
│   └── __init__.py
└── reporting/            # Render MigrationContext -> structured report.
    ├── __init__.py
    └── report_builder.py
```

Tests live in `tests/`, mirroring this layout. No module imports from a
sibling stage except via `core` types.

---

## 2. Module responsibilities

Each module has one job, one set of inputs, one set of outputs. Cross-stage
communication happens only through `MigrationContext` and the typed result
objects each stage produces.

### `core`
Primitives shared by every stage. **No I/O. No third-party clients.**
- `context.py`: `MigrationContext` (run state), `Anomaly` (structured
  finding), `Severity` literal type.
- `errors.py`: `MigratorError` base + `DetectionError`, `ExtractionError`,
  `PipelineAbort`. Every error carries `stage` + stable `code`.
- `pipeline.py`: declares the `Step` callable type, the canonical
  `ANALYZE_PIPELINE` and `FULL_PIPELINE` step tuples, and `run_pipeline`,
  which runs steps in order, records a critical anomaly on failure, and
  always builds a report before re-raising.

### `detection`
Inspect the source directory and classify it. Produces a `DetectionResult`
with `classification`, numeric `confidence`, `source_version`, and an
ordered `evidence` list (every fact and inconsistency observed). Refuses
unsupported pairs and low-confidence inputs by raising `PipelineAbort`.
Manifest-driven; no fingerprinting heuristics.

### `extraction`
Open the source SQLite database in `mode=ro` URI form. Walk Chroma `0.6.x`
internal tables. Produces an `ExtractionResult` with the collection,
PRAGMA integrity result, parsed records, and a `failed_rows` list with a
machine-readable reason per excluded row. Pre-flight failures
(e.g. PRAGMA integrity_check != "ok", WAL not checkpointed) raise
`ExtractionError`. Per-row failures are collected, never raised.

### `transformation`
Pure functions. Input: `ExtractionResult`. Output: `TransformedRecords`
ready for the target client. **No SQLite. No Chroma client. No filesystem.**
Currently a stub; the boundary is reserved so transformation logic can be
added without disturbing extraction or reconstruction.

### `reconstruction`
Open a Chroma `1.x` client at `target_path`. Insert transformed records
through the public `1.x` API. **The only module allowed to write.** The
target path must be empty or explicitly overwritable. Currently a stub.

### `validation`
Read back from the freshly written target via the `1.x` client and verify
what the tool can verify (count parity, ID set parity, document presence).
Records anomalies; does not mutate. Currently a stub.

### `reporting`
Take a `MigrationContext` (with optional terminal failure) and render the
final report dict. Always emits `explicitly_not_checked`. Pure: no I/O,
no formatting choices that depend on the caller. Stable JSON shape.

### `cli`
Process boundary. Parses argv (Click), constructs a `MigrationContext`,
runs a pipeline, emits the report (text or JSON), and maps
`MigratorError.stage` to a stable exit code. Contains zero domain logic.

---

## 3. `MigrationContext` design

`MigrationContext` is a **dumb holder**. It owns no logic except appending
anomalies. Stages read from it, write their typed result onto it, and
move on. This makes the pipeline trivially testable: every step is
`(MigrationContext) -> None`.

Fields (see [src/mempalace_migrator/core/context.py](src/mempalace_migrator/core/context.py)):

| Field | Owner | Purpose |
|---|---|---|
| `source_path: Path` | caller | Read-only source directory. |
| `target_path: Path \| None` | caller | Write target. `None` for `analyze`. |
| `run_id: str` | auto (uuid4) | Stable identifier for cross-referencing report and logs. |
| `started_at: str` | auto (UTC ISO) | Run start timestamp. |
| `detected_format` | `detection` | `DetectionResult`. |
| `extracted_data` | `extraction` | `ExtractionResult`. |
| `transformed_data` | `transformation` | Stub. |
| `reconstruction_result` | `reconstruction` | Stub. |
| `validation_result` | `validation` | Stub. |
| `anomalies: list[Anomaly]` | any stage | Ordered structured findings. |
| `report: dict` | `reporting` | Final emitted artifact. |

Invariants:
- A stage writes its own slot and may append anomalies. It does **not**
  read another stage's slot directly except where the dependency is part
  of the pipeline contract (e.g. transformation reads `extracted_data`).
- `anomalies` is append-only during a run.
- The context is not shared across runs and is not thread-safe by design.

`Anomaly` is frozen and JSON-safe by contract: `type` (stable string),
`severity` (`low`/`medium`/`high`/`critical`), `stage`, `message`,
`context: dict`. This is the only structure the report layer needs to
understand.

---

## 4. Pipeline flow

Pipelines are **ordered tuples of steps**, declared in
[src/mempalace_migrator/core/pipeline.py](src/mempalace_migrator/core/pipeline.py):

- `ANALYZE_PIPELINE = (step_detect, step_extract)` — read-only, no target.
- `FULL_PIPELINE   = (step_detect, step_extract, step_transform,
  step_reconstruct, step_validate)` — full reconstruction.

`run_pipeline(ctx, steps)` is the only orchestrator:

1. Iterate steps in order.
2. On `MigratorError`, stop the loop, ensure a `critical` anomaly exists
   for that stage, and remember the failure.
3. **Always** call `build_report(ctx, failure=...)` before returning.
4. Re-raise the original failure if any.

### Data flow (text diagram)

```
                  +---------+
   argv --------> |   cli   |  builds MigrationContext, picks pipeline
                  +----+----+
                       |
                       v
                +-------------+
                | run_pipeline|  ordered Step execution + report build
                +------+------+
                       |
   +-------------------+-------------------+
   |                   |                   |
   v                   v                   v
+---------+      +-----------+      +---------------+
|detection| ---> | extraction| ---> | transformation| (pure)
+----+----+      +-----+-----+      +-------+-------+
     |                 |                    |
     | DetectionResult | ExtractionResult   | TransformedRecords
     v                 v                    v
   ctx.detected_format / extracted_data / transformed_data
                                            |
                                            v
                                  +------------------+
                                  |  reconstruction  | (only writer)
                                  +--------+---------+
                                           |
                                           v
                                  +------------------+
                                  |    validation    |
                                  +--------+---------+
                                           |
                                           v
                                  +------------------+
                                  |    reporting     |  builds ctx.report
                                  +--------+---------+
                                           |
                                           v
                                       cli emits
                                  (text or JSON) +
                                   maps stage -> exit code
```

Anomaly bus (orthogonal to data flow):

```
any stage --append--> ctx.anomalies --read--> reporting
```

---

## 5. Failure model (high level)

Two failure classes, distinguished in code, not in prose.

### Critical failure → raise → abort
A condition that invalidates the entire run. The stage:
1. Records a `critical` anomaly (so the report shows the cause).
2. Raises a `MigratorError` subclass with `stage` + stable `code` +
   human `summary` + optional `details`.

`run_pipeline` halts the loop, builds the report, and re-raises. The CLI
maps `stage` to an exit code via a lookup table:

| Stage           | Exit code |
|-----------------|-----------|
| `detect`        | `2`       |
| `extract`       | `3`       |
| `report`        | `6`       |
| anything else   | `10`      |
| success         | `0`       |

Examples: unsupported source format, confidence below threshold,
unsupported version pair, PRAGMA integrity failure, uncheckpointed WAL,
target path conflict (future), report builder crash.

### Soft failure → collect → continue
A condition that excludes data but does not invalidate the run. The
stage:
1. Appends one or more `Anomaly` entries (severity `low`/`medium`/`high`).
2. Records the offending row in a stage-local `failed_rows` list with a
   machine-readable reason.
3. Continues processing.

Examples: a row with a missing document, an unresolvable metadata
reference, a duplicate ID.

### Guarantees
- Exit code `0` means **the pipeline did not raise**. It is **not** a
  correctness claim. The report is the source of truth.
- Every report contains `explicitly_not_checked`, naming conditions the
  tool does not verify. Silence is never a guarantee.
- Critical anomalies are always present in the report when a run aborts;
  `run_pipeline` synthesises a generic one if a step raised without
  recording its own.

---

## 6. Key design decisions

- **`MigrationContext` is dumb.** All logic lives in stages. The context
  exists to be passed around and inspected by the report layer. This
  keeps every stage trivially unit-testable as a pure
  `(ctx) -> None` function.
- **Stages communicate only through `ctx` and typed result objects.** No
  module imports a sibling stage's internals. Stages may import `core`
  freely; nothing else.
- **Pipelines are data, not code.** `ANALYZE_PIPELINE` and
  `FULL_PIPELINE` are tuples of `Step` callables. New flows are new
  tuples. The orchestrator is one function.
- **One writer.** Only `reconstruction` writes. Source is opened
  read-only at the SQLite URI level. There is no codepath that can
  mutate the source.
- **Errors carry `stage` + `code`.** The CLI does not parse strings to
  pick exit codes, and the report layer does not parse strings to render
  failures. Both consume structured fields.
- **The report is always built.** `run_pipeline` calls
  `build_report` even when a step raised. Failures are first-class
  citizens of the report, not an alternate path.
- **Soft vs. critical is encoded, not improvised.** Critical = raise.
  Soft = `add_anomaly` + per-row `failed_rows`. The distinction is in
  the type system, not in caller convention.
- **No `--force`.** Strict boundaries are enforced in `step_detect`:
  unsupported classification, low confidence, unsupported version pair
  all abort. There is no escape hatch.
- **Stubs are explicit.** `transform`, `reconstruct`, `validate` each
  record a `not_implemented` anomaly so reports never look "clean" by
  accident while large parts of the pipeline are inert.
- **Reporting is pure.** No I/O, no env-dependent formatting. The CLI
  decides text vs. JSON; the builder produces one canonical dict shape.
- **CLI is a thin shell.** Click parses argv, maps exceptions to exit
  codes, prints the report. Zero domain logic. Replaceable by any other
  driver (HTTP handler, library call) without touching stages.
