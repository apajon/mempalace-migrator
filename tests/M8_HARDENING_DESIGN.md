# M8 — Final Hardening: Implementation Strategy

Status: **design only** (no production code, no new tests merged yet).
Scope: phase 11 in `tests/TODO.json` (`11.1 .. 11.4`).
Predecessors satisfied: M1–M7 exit gates (see `tests/M7_ADVERSARIAL_DESIGN.md` §9).
Successor: none. M8 is the last milestone in `tests/ROADMAP.json::execution_order`.

This document fixes the *shape* of M8 work. It is intentionally narrow: M8
adds **no features**, **no new pipeline stages**, **no new exit codes**,
**no new severities**, **no new anomaly types**, **no transformation /
reconstruction / validation production behaviour**. M8's job is to prove —
by construction — that the system already documented in M1–M7 stays
**stable, structured, and bounded** under sustained and degenerate load,
and that its diagnostic surface is honest about what it does and does not
emit.

---

## 1. M8 in one sentence

> Lock the runtime envelope (logging, latency, memory, repeatability) of
> the existing pipeline so that future change is detected as drift against
> a recorded baseline, rather than discovered in production.

If the system, after M8, produces:
- an unstructured `print()` / `logging` line escaping any production
  module, **or**
- an `analyze` run on a recorded baseline corpus that diverges from its
  recorded `(exit_code, schema_version, anomaly counts by type)` triple
  without an intentional, reviewed change, **or**
- a memory profile on the largest baseline fixture that exceeds the
  recorded ceiling without an explicit re-baseline, **or**
- a wall-clock time on the smallest baseline fixture that exceeds the
  recorded ceiling by more than the documented tolerance,

then M8 has found a real defect or an unannounced behavioural change.
Defects discovered during M8 are fixed in the owning production module —
**not** in test code, **not** by widening the baseline.

---

## 2. Non-goals (hard fences)

M8 must not:

1. Add a new pipeline stage, a new subcommand, or a new CLI flag. The
   pinned subcommand set `{analyze, inspect, report}` is frozen. The
   pinned exit-code set `{0,1,2,3,4,5,6,7,8,9,10}` is frozen.
2. Add a new severity. `LOW/MEDIUM/HIGH/CRITICAL` is the closed set
   (M3).
3. Add a new `AnomalyType` value. M3's enum closes the registry; M8
   does not extend it. If M8 finds a real failure mode without a
   matching enum value, that is a **finding to fix in the owning
   stage**, not an excuse to grow the registry from the test suite.
4. Introduce free-form `print()` or `logging` calls in production code.
   Anomalies remain the single inconsistency channel (M3). Any
   diagnostic surface added in M8.1 is **opt-in, off-by-default, and
   structured** — see §4.1.
5. Touch transformation / reconstruction. Those stages are stubs by
   design; M8 does not implement them. Stubs that emit a
   `not_implemented` anomaly today must continue to do so unchanged.
6. Optimise opportunistically. M8 may only change runtime behaviour
   when (a) a baseline regression is observed, (b) the regression is
   reproducible, (c) the fix is the smallest change that restores the
   recorded envelope. Speculative refactors are **out of scope**.
7. Add fixtures that depend on a live ChromaDB install or any network.
   M7's no-network rule carries over verbatim.
8. Re-derive any M7 invariant. M8 imports them as black-box
   assertions; it does not weaken them.
9. Promise crash-free behaviour on inputs M7 already classified as
   "exit 2 / 3 / 8". M8's stability target is **the absence of
   *new* failure modes under load**, not the elimination of failure
   modes M7 deliberately surfaced.

---

## 3. Architecture

### 3.1 Test layout

```
tests/
├── adversarial/                  # M7 — unchanged
├── hardening/                    # NEW — M8 lives entirely here
│   ├── __init__.py
│   ├── conftest.py               # baseline corpus + envelope fixtures (§3.2)
│   ├── baselines/                # recorded JSON envelopes, committed (§3.3)
│   │   ├── runtime_envelope.json
│   │   └── report_signatures.json
│   ├── test_logging_discipline.py        # 11.1
│   ├── test_performance_baseline.py      # 11.2
│   ├── test_memory_envelope.py           # 11.3
│   └── test_stability_invariants.py      # 11.4 — cross-cutting (§5)
└── ...
```

Rationale (parallels M7 §3.1):
- One file per TODO sub-task (`11.1`–`11.4`) to keep traceability
  obvious.
- One *invariant* file (`11.4`) that runs against the entire baseline
  corpus — that is where the M8 exit gate is actually enforced (§5).
- `baselines/` is **committed** (unlike M7's `corpus/`). Reasons:
  (a) the whole point of M8 is drift detection — a baseline that is
  regenerated on every run detects nothing;
  (b) the baseline is a small JSON document, diff-friendly, and
  reviewable;
  (c) re-baselining is an explicit, reviewed code change, not a
  side-effect of running tests. See §3.3 for the re-baseline protocol.

### 3.2 Baseline corpus

M8 does **not** invent inputs. The baseline corpus is the union of:
- the existing M1–M6 happy-path fixtures (smallest viable Chroma 0.6.x
  palace, plus the canonical "good" palace used by reporting tests),
- a **subset** of the M7 adversarial corpus, restricted to entries whose
  expected exit code is `0` or `8` (i.e. the system is *supposed* to
  finish). Adversarial entries that abort at `detect`/`extract` are
  *not* part of the performance/memory baseline because their runtime is
  dominated by early abort and would mask regressions in the hot path.

The M7 fixture builders in `tests/adversarial/conftest.py` are imported
by `tests/hardening/conftest.py`. M8 owns no new fixture builders.

### 3.3 Baseline files

Two committed JSON documents pin the envelope:

```text
baselines/runtime_envelope.json
{
  "schema_version": 1,
  "recorded_on": "<UTC ISO date>",
  "python_version": "3.12",
  "tolerance": {"wall_clock_pct": 50, "rss_bytes_pct": 25},
  "entries": [
    {
      "fixture_id": "minimal_valid_chroma_06",
      "command": ["analyze"],
      "wall_clock_seconds_p50": <float>,
      "peak_rss_bytes": <int>,
      "exit_code": 0,
      "report_signature_id": "minimal_valid_analyze"
    },
    ...
  ]
}

baselines/report_signatures.json
{
  "schema_version": 1,
  "entries": {
    "minimal_valid_analyze": {
      "schema_version": 3,
      "outcome": "success",
      "top_severity": "low" | "medium" | "high" | "none",
      "anomaly_counts_by_type": {"NOT_IMPLEMENTED": 0, ...},
      "stages_executed": ["detect", "extract"],
      "exit_code": 0
    },
    ...
  }
}
```

Notes:
- `wall_clock_seconds_p50` is a median over a small in-test repeat
  count (e.g. 5 runs) — never a single sample.
- `tolerance` is **per-baseline-file**, not per-entry. A regression
  alert fires when `observed > recorded * (1 + tolerance_pct/100)`.
- Tolerances are deliberately wide (50% wall-clock, 25% RSS). M8 is a
  **regression detector**, not a benchmark suite. A 5% slowdown is not
  M8's problem; a 5x slowdown is.
- `report_signatures.json` is the **structural** signature of a report,
  not its full JSON. Diffing full reports would couple M8 to formatting
  noise (`run_id`, timestamps). The signature is the small set of
  fields whose change is meaningful.

Re-baseline protocol (the only way these files change):
1. The change that caused the drift is committed first, with its own
   justification.
2. `python -m tests.hardening.rebaseline` (script to be added under
   §4.2) regenerates both JSON files from the current code.
3. The diff against the previous baseline is included in the same PR
   as the production change. Re-baselining without an accompanying
   production change is a review red flag.

There is **no** `--update-baseline` flag on the test runner. Drift
fails the suite; it does not silently rewrite the baseline.

---

## 4. Sub-task strategy

Each TODO sub-task (`11.1`–`11.4`) maps to one test file. For every
sub-task we fix: the **assertion surface**, the **input recipe**, and
the **forbidden behaviour**.

### 4.1 — `11.1` Logging cleanup (`test_logging_discipline.py`)

Current state (verified during this design pass): production code
under `src/mempalace_migrator/` contains **no** `print(`, `logging.`,
or `logger.` call sites. The only `click.echo(..., err=True)` call
sites are in `cli/main.py` and are the documented `MigratorError`
summary line plus the `report` subcommand's "cannot read file"
message. Stdout is reserved for the report (text or JSON).

M8.1 turns this *current good state* into a **pinned contract**:

| Assertion                                                                      | Mechanism                                                                       |
|--------------------------------------------------------------------------------|---------------------------------------------------------------------------------|
| No `print(` in `src/mempalace_migrator/`                                       | `ast` walk over every `.py` file under `src/`; assert no `Call(func=Name("print"))` |
| No `import logging` / `from logging` in `src/mempalace_migrator/`              | same `ast` walk                                                                 |
| No `sys.stdout.write` / `sys.stderr.write` in `src/mempalace_migrator/`        | same `ast` walk                                                                 |
| Allowed `click.echo` call sites are only in `cli/main.py`                       | `ast` walk recording file path of each `click.echo(...)` site; assert allow-list |
| Allowed `click.echo(..., err=True)` messages do not contain a Python traceback | regex on rendered subprocess stderr across the M7 corpus subset                 |
| Stdout under `--quiet` is empty for every baseline corpus entry                | re-asserted from M7 invariant 9, restricted to baseline subset                  |
| Stdout under `--json-output` is exactly one JSON document                      | re-asserted from M7 invariant 9                                                 |

Forbidden:
- Adding a `logging` configuration "for diagnostics" and then asserting
  it is silent by default. M8.1's contract is **structural absence**,
  not runtime silence. A logger that exists can be re-enabled by an
  importer; an absent logger cannot.
- Whitelisting new `click.echo` sites by extending the allow-list
  without a corresponding production justification.

If M8.1 ever needs a structured diagnostic surface (e.g. for M8.2
timing collection), the surface is **the report itself**. Timings,
counters, and runtime metadata go into a new `report["runtime"]`
section, JSON-safe, schema-versioned, and subject to the existing
forbidden-vocabulary regex (M5). They do **not** go to stdout/stderr.

### 4.2 — `11.2` Performance baseline (`test_performance_baseline.py`)

Goal: detect order-of-magnitude regressions in the hot path
(`detect` + `extract`), not micro-optimise.

Mechanism:
- For each entry in `baselines/runtime_envelope.json`, run the CLI as
  a subprocess (same machinery as M7 — `subprocess.run`, never
  `CliRunner`) `N=5` times, take the median wall-clock, compare to the
  recorded `wall_clock_seconds_p50` against
  `tolerance.wall_clock_pct`.
- The runner helper is shared with `test_memory_envelope.py` to avoid
  two parallel measurement implementations.
- The script `tests/hardening/rebaseline.py` is the **only** producer
  of `runtime_envelope.json`. The test file is read-only against it.

Forbidden:
- Asserting absolute wall-clock numbers ("must be < 100 ms"). Absolute
  numbers depend on the runner. M8 asserts **delta vs. baseline only**.
- Re-running a slow test until it passes ("flake retry"). A regression
  that disappears on retry is still a regression.
- Skipping the test on slow CI. Either the tolerance is wide enough to
  absorb CI noise, or the baseline is wrong. There is no third option.

Out of scope:
- Profiling individual functions. M8 measures the public CLI surface,
  not internal call counts.
- Comparing across Python versions. The baseline pins
  `python_version: "3.12"`; running under another major version
  short-circuits the test with a structured `pytest.skip` whose
  reason is asserted (no silent skips, per M7 §8.3).

### 4.3 — `11.3` Memory handling (`test_memory_envelope.py`)

Goal: bound peak resident memory of the hot path on the largest
baseline fixture, so a future change that slurps the whole DB into
RAM is caught.

Mechanism:
- Subprocess runner identical to §4.2.
- Peak RSS is measured via `resource.getrusage(RUSAGE_CHILDREN)` after
  `subprocess.run(...).wait()` on POSIX. On Windows, the test is
  skipped with an asserted reason (`getrusage` is POSIX-only); the
  skip is not silent.
- For each baseline entry, assert
  `observed_peak_rss <= recorded_peak_rss * (1 + tolerance.rss_bytes_pct/100)`.

Forbidden:
- Using `tracemalloc` from inside the test process. The CLI runs in a
  subprocess; in-process Python allocation tracking measures the
  pytest harness, not the tool.
- Asserting "no memory leak" by running the pipeline N times and
  diffing RSS. That is a different test (long-lived process); the
  CLI is short-lived by design.

Open question (recorded, not resolved here): whether a
"largest reasonable Chroma 0.6.x palace" fixture needs to be added
specifically for §4.3, or whether the existing happy-path fixture is
large enough to make RSS measurements meaningful. If a new fixture is
required, it is built by the existing M7 builder
`build_minimal_valid_chroma_06` parametrised by row count — **no new
builder family**. The decision is made when §4.3 is implemented; the
default is "use existing fixtures, scale row count if needed".

### 4.4 — `11.4` Stability validation (`test_stability_invariants.py`)

This file is the **actual M8 exit gate**. It parametrises over the
entire baseline corpus and asserts the invariants in §5. It is the
M8 analogue of M7's `test_adversarial_invariants.py`.

It does **not** re-derive M7 invariants; it imports the M7 invariant
helpers (refactored into `tests/adversarial/_invariants.py` if not
already importable) and re-runs them on the M8 baseline subset. Any
M7 invariant that no longer holds on the baseline is an M8 failure
**and** an M7 regression.

---

## 5. Cross-cutting invariants (the actual exit gate)

`test_stability_invariants.py` parametrises over the **entire baseline
corpus** and asserts the following on each run's
`(exit_code, report, stdout, stderr, peak_rss, wall_clock)` tuple:

1. **Report-signature stability.** For every baseline entry, the
   recorded signature in `report_signatures.json` matches the observed
   signature exactly. No tolerance. Drift here is *always* a finding.
2. **Schema stability.** `report["schema_version"] == REPORT_SCHEMA_VERSION`
   for every baseline entry. Bumping the schema version requires a
   re-baseline PR (§3.3).
3. **Exit-code stability.** Observed exit code matches recorded exit
   code for every baseline entry. No tolerance.
4. **Wall-clock envelope.** `observed_p50 <= recorded_p50 * (1 + wall_clock_pct/100)`.
5. **RSS envelope.** `observed_peak <= recorded_peak * (1 + rss_bytes_pct/100)`,
   POSIX only; non-POSIX produces an *asserted* skip with the recorded
   reason `"non_posix_rss_unsupported"`.
6. **Logging discipline (re-asserted).** No `print` / `logging` /
   `sys.std*.write` call site appears in `src/mempalace_migrator/` (the
   AST walk from §4.1 is also imported here so the gate cannot pass with
   §4.1 disabled).
7. **stdout/stderr discipline (re-asserted).** Under `--json-output`,
   stdout is exactly one JSON document; under `--quiet`, stdout is empty;
   under default mode, stderr contains zero traceback lines. (M7
   invariant 9, restricted to the baseline subset.)
8. **All M7 invariants still hold** on the baseline subset. Imported
   from `tests/adversarial/_invariants.py`. If any M7 invariant fails
   on a baseline entry that is *also* in the adversarial corpus, the
   M7 suite would have failed first; the duplication here is
   defensive, not authoritative.
9. **Determinism.** For every baseline entry, two consecutive
   subprocess runs produce identical `report_signature` (modulo
   `run_id` and `started_at`, which are stripped before comparison).
   A non-deterministic report is itself a finding.

These nine invariants are the M8 exit gate. If any of them fails on
any baseline corpus entry, M8 is **FAIL** — regardless of how many
positive sub-task tests pass.

---

## 6. Handling discoveries during M8

M8 is expected to find **at most a handful** of defects (M1–M7 already
exercised the structural surface aggressively). Triage rule:

| Discovery                                                                   | Action                                                                            |
|-----------------------------------------------------------------------------|-----------------------------------------------------------------------------------|
| Stray `print` / `logging` import slipped into a stage                       | Remove it; replace with `add_anomaly` if the message carried information           |
| `click.echo` call site appears outside `cli/main.py`                        | Remove it; the call belongs in the CLI shell, not in a stage                       |
| Wall-clock regression > tolerance on a baseline entry                       | Bisect; fix the offending change; re-baseline only if the regression is intended  |
| RSS regression > tolerance on a baseline entry                              | Same as above; do not raise tolerance to mask                                     |
| Report signature drift on a baseline entry                                  | Treat as a contract change; either revert the production change or re-baseline   |
| Non-deterministic report (invariant 9 fails)                                | Find the source of non-determinism (clock, dict order, random); pin it           |
| M7 invariant fails on a baseline entry                                      | This is an M7 regression, not an M8 finding; fix in the M7-owning module          |
| A baseline fixture needs a new `AnomalyType` to describe a real failure     | Stop. This is an M3 / M5 finding, not an M8 finding. Do **not** add the enum here. |

Each discovery: open the fix in the owning module, keep the M8 test
that proved it, do **not** widen scope into new features. If a
discovery would require a new pipeline stage, new exit code, new
severity, or new `AnomalyType`, it is **out of scope** — record it as
a caveat in `ROADMAP.json::current_position.caveats` and defer to a
future milestone (which `ROADMAP.json::execution_order` does not
currently contain).

---

## 7. What is implemented vs. what remains missing

Implemented today (already verified by M1–M7):
- Production code is free of `print` / `logging` / `sys.std*.write`
  call sites (verified by inspection during this design pass; not yet
  pinned by a test).
- The only `click.echo(..., err=True)` call sites are in
  `cli/main.py` (verified during this design pass).
- M7 `test_adversarial_invariants.py` enforces nine cross-cutting
  invariants on a 28-entry adversarial corpus (`tests/M7_ADVERSARIAL_DESIGN.md` §9).
- `_decide_exit_code` is pure and unit-tested; exit `8` (silent
  CRITICAL guard) is enforced.
- Report `schema_version == 3`, JSON-safe without `default=`, and
  passes the forbidden-vocabulary regex (M4 / M5).

**Not yet implemented** (M8 will build):
- `tests/hardening/` directory and `conftest.py`.
- `baselines/runtime_envelope.json` and `baselines/report_signatures.json`.
- `tests/hardening/rebaseline.py` (the only producer of the baseline
  files; not run by the test suite).
- Four sub-task test files (`11.1`–`11.4`).
- The AST-walk helper that pins logging discipline structurally
  (§4.1).
- The subprocess runner helper shared by §4.2 and §4.3 (likely
  refactored out of M7's existing subprocess runner if one exists,
  otherwise added fresh in `tests/hardening/conftest.py`).
- Refactor of M7 invariants into an importable module
  (`tests/adversarial/_invariants.py`) so M8 invariant 8 does not
  duplicate code. This is a **test-only refactor**; it does not
  change M7 assertions.

Explicitly **out of scope** for M8 (do not start):
- Filling in the transformation / reconstruction stubs. These remain
  `not_implemented` anomaly emitters. M8 baselines record their
  current behaviour exactly.
- Unifying detection's pre-M3 `Evidence` / `Contradiction` model with
  `Anomaly`. Long-standing caveat in `ROADMAP.json`; M8 only asserts
  both surfaces remain structured, not that they merge.
- Adding a structured logging surface (e.g. `--log-format json`).
  M3's "anomalies are the single channel" rule stands. If diagnostic
  metadata is needed, it goes into `report["runtime"]` (§4.1), not
  into stderr.
- Long-running / load / fuzz / property-based tests. M8 is a
  regression detector against a recorded envelope, not a load test
  suite.
- Cross-OS coverage beyond POSIX / non-POSIX skip discipline. M8's
  baseline pins `python_version: "3.12"`; OS-specific tolerances are
  not modelled.

---

## 8. Failure modes M8 itself must avoid

Meta-risks for the test suite:

1. **Self-rewriting baselines.** Any test path that calls the
   re-baseline script automatically defeats the entire milestone. The
   re-baseline script lives outside `pytest` collection
   (`tests/hardening/rebaseline.py`, no `test_` prefix) and is
   invoked manually.
2. **Tolerance creep.** Each tolerance bump is a code change with its
   own justification. A test that fails today and passes tomorrow only
   because tolerance was widened is an M8 failure dressed as a fix.
3. **Hidden non-determinism.** Same rule as M7 §8.3: `tmp_path`-based,
   no global state, no network, no `time.sleep`, no `random` without a
   fixed seed. Invariant 9 (§5) detects this directly; it must not be
   xfailed.
4. **Subprocess flake retry.** Wall-clock and RSS measurements take
   the **median of 5 runs**; the test then runs **once**. There is no
   `pytest-rerunfailures` hook. A flaky baseline is a real signal.
5. **CliRunner trap (inherited from M6/M7).** All M8 measurement and
   stdout/stderr discipline tests use real `subprocess.run`. No
   `CliRunner`. The same rule as M7 §8.5.
6. **AST walk false negatives.** §4.1's structural ban is implemented
   as an `ast` walk, not a regex. A regex would miss
   `from logging import getLogger as gl; gl(...)` — the AST walk
   resolves names through `ast.Import` / `ast.ImportFrom` aliases.
   The walk is itself unit-tested against a synthetic file containing
   each forbidden pattern.
7. **Baseline corpus drift.** If an M7 fixture builder changes shape,
   the M8 baseline silently re-records different content. Mitigation:
   `report_signatures.json` pins the structural signature, so any
   shape change in the fixture surfaces as a signature drift in M8.4
   (invariant 1) before it can rot the baseline.

---

## 9. Exit-gate checklist (what "M8 done" means)

M8 is **PASS** only when **all** of the following hold:

- [ ] `tests/hardening/` exists with the four sub-task files and the
      committed baseline directory listed in §3.1.
- [ ] `baselines/runtime_envelope.json` and
      `baselines/report_signatures.json` exist, are committed, and
      were produced by `tests/hardening/rebaseline.py` from the
      current `HEAD` (recorded in their `recorded_on` field).
- [ ] At least one baseline entry per happy-path fixture and per
      `exit_code ∈ {0, 8}` adversarial fixture is recorded.
- [ ] Every baseline run has its
      `(exit_code, report, stdout, stderr, peak_rss, wall_clock)`
      tuple checked by **all nine** invariants in §5.
- [ ] The AST walk (§4.1) finds zero `print` / `logging` /
      `sys.std*.write` call sites in `src/mempalace_migrator/`, and
      zero `click.echo` call sites outside the documented allow-list.
- [ ] No invariant is xfailed, skipped, or guarded by
      `pytest.mark.skipif` without a recorded reason, and the
      recorded reason is asserted by the test (no silent skips).
- [ ] Every defect surfaced by M8 is fixed in the owning production
      module (not in test code, not by tolerance widening) **or**
      explicitly recorded as an out-of-scope caveat in `ROADMAP.json`.
- [ ] Full test suite (existing 546 passed + 3 intentional skips from
      M7, plus M8 additions) is green.
- [ ] `TODO.json` phase 11 sub-tasks `11.1`–`11.4` are flipped to
      `done` with a `note` pointing to the test file that proves each
      one.
- [ ] `ROADMAP.json::current_position` is updated to `M8_done`
      **only after** the nine items above are satisfied.

Until every box is ticked, M8 status remains `todo` and the project
is not "production-credible". Partial completion is **CONDITIONAL
FAIL**, not "in progress" — same rule as M7 §9.
