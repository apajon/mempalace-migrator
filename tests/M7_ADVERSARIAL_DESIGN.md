# M7 — Adversarial Testing: Implementation Strategy

Status: **design only** (no production code, no new tests merged yet).
Scope: phase 10 in `tests/TODO.json` (`10.1 .. 10.5`).
Predecessors satisfied: M1–M6 exit gates.
Successor blocked: M8 (Final Hardening) cannot start until M7 exit gate is green.

This document fixes the *shape* of M7 work. It is intentionally narrow: M7
adds **no features**, **no new pipeline stages**, **no new exit codes**,
**no new anomaly types unless an adversarial input proves one is missing**.
M7's job is to prove — by construction — that the system already documented
in M1–M6 *fails loudly and structurally* on hostile input.

---

## 1. M7 in one sentence

> Build a corpus of deliberately-broken inputs and assert that the system's
> public observable surface (exit code + report JSON + `ctx.anomalies`)
> reacts in a way that is **explicit, structured, and never silently
> wrong**.

If a hostile input produces:
- a Python traceback escaping the CLI, **or**
- exit `0` with `top_severity == "critical"` (this is exit `8` territory), **or**
- a report whose `outcome == "success"` but whose anomalies do not name the
  injected defect, **or**
- a `MigratorError` with stage `""` / unknown stage (exit `10`),

then M7 has found a real defect. Defects discovered during M7 are fixed in
the existing modules — **not** in adversarial test code.

---

## 2. Non-goals (hard fences)

M7 must not:

1. Add a new pipeline stage, a new subcommand, or a new CLI flag.
2. Add a new exit code. The pinned set `{0,1,2,3,4,5,6,7,8,9,10}` is frozen
   for M7. Exit `10` is the catch-all for "M7 found a class of failure the
   system does not yet model" — and that is itself a finding to fix
   upstream, not to absorb in M7.
3. Add a new severity. `LOW/MEDIUM/HIGH/CRITICAL` is the closed set.
4. Add free-form logging or `print()` in production code to make tests
   easier. Anomalies remain the single inconsistency channel (M3).
5. Touch transformation / reconstruction. Those stages are stubs by design;
   adversarial input that flows past extraction simply enters the
   `not_implemented` anomaly path that already exists. M7 asserts that
   that path is taken — it does not implement the stages.
6. Optimise. Performance work is M8.
7. Add fixtures that depend on a live ChromaDB install or any network.

---

## 3. Architecture

### 3.1 Test layout

```
tests/
├── adversarial/                  # NEW — M7 lives entirely here
│   ├── __init__.py
│   ├── conftest.py               # fixture builders (see §3.2)
│   ├── corpus/                   # generated on demand by builders, gitignored
│   ├── test_corrupted_json.py            # 10.1
│   ├── test_broken_sqlite.py             # 10.2
│   ├── test_mixed_format_inputs.py       # 10.3
│   ├── test_inconsistent_data.py         # 10.4
│   ├── test_extreme_edge_cases.py        # 10.5
│   └── test_adversarial_invariants.py    # cross-cutting invariants (§4)
└── ...
```

Rationale:
- One file per TODO sub-task (`10.1`–`10.5`) to keep traceability obvious.
- One *invariant* file that runs against **every** corpus entry — that is
  where the M7 exit gate is actually enforced (§5).
- `corpus/` is **constructed in fixtures**, not committed. Reasons:
  (a) corrupted SQLite blobs are diff-hostile;
  (b) we want the build recipe to live next to the assertion;
  (c) committed binaries rot silently — exactly the failure mode M7 must
  prevent.

### 3.2 Fixture builders (sketch only)

A small set of pure helpers in `tests/adversarial/conftest.py`:

```python
# illustrative only — not final code
def build_minimal_valid_chroma_06(tmp_path) -> Path: ...
def corrupt_sqlite_header(db_path: Path) -> None: ...
def truncate_sqlite_mid_page(db_path: Path, at_byte: int) -> None: ...
def overwrite_metadata_with(db_path: Path, raw_blob: bytes) -> None: ...
def write_unparseable_json_sidecar(dir_path: Path, name: str, payload: bytes) -> None: ...
```

Each builder returns either a `Path` (a synthesised "source") or mutates
one. They are the **only** place adversarial bytes are produced. Tests
import builders, never construct corruption inline. This keeps the
inventory of attacks discoverable in one place.

### 3.3 No mocking of production code

M7 runs the real pipeline (`run_pipeline`) and the real CLI
(`subprocess.run([... ,"analyze", source]) ` for the cases where M5/M6
already established CliRunner cannot isolate stdout/stderr in Click ≥ 8.2).

In-process tests use `MigrationContext` + `run_pipeline` directly to assert
on `ctx.anomalies` shape; subprocess tests assert on **exit code** and
**stdout/stderr discipline**. Both layers must agree. Disagreement
between them is itself a finding.

---

## 4. Adversarial categories → invariants

Each TODO sub-task maps to a category. For every category we fix:
- the **input recipe**,
- the **expected exit code**,
- the **required anomaly evidence**,
- the **forbidden behaviour**.

| TODO  | Category              | Input recipe (representative)                                       | Expected exit | Required anomaly (type / stage)                              | Forbidden                                  |
|-------|-----------------------|----------------------------------------------------------------------|---------------|---------------------------------------------------------------|--------------------------------------------|
| 10.1  | Corrupted JSON        | metadata column contains `b"{not json"`; document column = bytes; truncated UTF-8 | `0` or `8`    | extraction emits per-row anomaly w/ `evidence.kind` naming the bad column; row appears in `failed_rows`, *not* in `parsed` | crash; row silently dropped without anomaly |
| 10.2  | Broken SQLite         | (a) zeroed header (b) PRAGMA `integrity_check` ≠ "ok" (c) mid-scan page error (d) missing required table | `3`           | `ExtractionError` with stage=`extract`; pre-flight CRITICAL anomaly with `evidence.kind="pragma_integrity_check"` or `kind="missing_table"` | partial result + exit 0; bare `sqlite3.DatabaseError` escaping |
| 10.3  | Mixed format          | directory containing both Chroma 0.6 layout *and* 1.x layout artefacts; Chroma 0.6 layout *and* a stray manifest claiming 1.x | `2`           | `PipelineAbort` stage=`detect`; detection `contradictions` non-empty; `confidence_band ∈ {LOW, UNKNOWN}` | classification chosen silently with high confidence |
| 10.4  | Inconsistent data     | duplicate IDs across rows; same ID in `parsed` *and* `failed_rows`; row whose declared collection ≠ scanned collection | `0` or `8`    | validation `consistency` checks fail (`unique_drawer_ids`, `id_not_in_both_parsed_and_failed`); `ValidationResult.confidence_band` drops to `LOW` | validation reports `passed`; meta-anomaly absent |
| 10.5  | Extreme edge cases    | empty DB; 0-byte file; symlink loop; non-UTF-8 path component; document of size 0; document of size 32 MiB; embedding of wrong dimensionality; `NaN` in embedding | varies (`2`/`3`/`8`) per input; **never `10`, never raw traceback** | each case has a *named* anomaly type already in `AnomalyType`; if not, M7 records the gap (see §6) | exit 10; uncaught `OSError`/`UnicodeDecodeError` |

Notes:
- `failed_rows` exit policy is unchanged: per-row failures isolate (M2),
  the run continues, exit is `0` (or `8` if the rate triggers a CRITICAL
  heuristic). M7 must assert *which* anomaly, not just "an anomaly".
- "Required anomaly" is asserted on `evidence[*].kind` and
  `location.stage`, **not** on `message` text. Message strings are not a
  contract.

---

## 5. Cross-cutting invariants (the actual exit gate)

`test_adversarial_invariants.py` parametrises over the **entire corpus**
(every fixture from every category) and asserts the following on each
run's `(exit_code, report, anomalies)` triple:

1. **No exit 10.** Exit `10` from any adversarial input is a failure of
   the system, not the test.
2. **No traceback on stderr** under non-`--debug` mode (regex check). A
   single `MigratorError` summary line is allowed.
3. **No silent CRITICAL.** `report["outcome"] == "success"` ⇒ exit ∈ {0, 8};
   exit `0` ⇒ `top_severity ≠ "critical"`. This is exactly the
   `_decide_exit_code` invariant; M7 enforces it externally.
4. **Schema stability.** `report["schema_version"] == 3` and
   `set(report) ⊇ REPORT_TOP_LEVEL_KEYS` for every adversarial input.
5. **JSON safety.** `json.dumps(report)` succeeds with no `default=` arg
   for every adversarial input (already guaranteed by M4; M7 re-checks
   under hostile conditions).
6. **Anomaly well-formedness.** Every `Anomaly` has non-empty
   `location.stage`, ≥ 1 `evidence` entry, and a `type` in `AnomalyType`.
   This re-asserts the M3 contract under hostile input.
7. **Forbidden vocabulary.** The serialized report contains none of
   `correct|verified|guaranteed|safe|valid` (already enforced for the
   golden path in M5; M7 re-asserts under failure).
8. **Stage attribution.** If `raised is not None`, `raised.stage` ∈
   `{"detect","extract","transform","reconstruct","report","validate"}`.
9. **stdout/stderr discipline** (subprocess layer): under
   `--json-output`, stdout is exactly one JSON document; under
   `--quiet`, stdout is empty regardless of input pathology.

These nine invariants are the M7 exit gate. If any of them fails on any
corpus entry, M7 is **FAIL** — regardless of how many positive
category-specific tests pass.

---

## 6. Handling discoveries during M7

M7 is expected to find defects. Triage rule:

| Discovery                                                | Action                                                                 |
|----------------------------------------------------------|------------------------------------------------------------------------|
| Missing `AnomalyType` for a real failure mode            | Add the enum value in `core/context.py` + emit it from the right stage |
| Stage swallows an exception                              | Convert to `MigratorError(stage=…)` with a structured anomaly          |
| Report key missing under failure                         | Fix `build_report` to always populate it (M4 contract)                |
| Validation marks a clearly-broken run `passed`           | Strengthen the relevant check in `validation/` (M5 contract)          |
| CLI exits `10` on a recoverable input                    | Map the failure to its real stage; `10` must remain rare               |
| Detection silently picks a side on contradictory signals | Lower `confidence_band` and emit `Contradiction` (M1 contract)        |

Each discovery: open the fix in the owning module, keep the M7 test that
proved it, do **not** widen scope into new features. If a discovery would
require a new pipeline stage or new exit code, it is **out of scope** —
record it as a caveat in `ROADMAP.json::current_position.caveats` and
defer.

---

## 7. What is implemented vs. what remains missing

Implemented today (already verified by M1–M6):
- Per-row extraction isolation, `failed_rows`, structured anomalies.
- PRAGMA pre-flight, `ExtractionError` for CRITICAL pre-flight failures.
- Detection contradictions, confidence bands, refusal on low confidence.
- `_decide_exit_code` purity and the exit-`8` silent-CRITICAL guard.
- Report schema v3, JSON-safety, forbidden-vocabulary check (golden path).
- Validation trichotomy (`passed/failed/inconclusive`),
  `checks_not_performed` (incl. reconstruction-parity skips).

**Not yet implemented** (M7 will build):
- `tests/adversarial/` directory and `conftest.py` builders.
- Five category test files (`10.1`–`10.5`).
- `test_adversarial_invariants.py` enforcing §5 over the full corpus.
- A subprocess-based runner helper that captures `(exit_code, stdout,
  stderr)` and parses the report from stdout under `--json-output`.

Explicitly **out of scope** for M7 (do not start):
- Performance/memory work (M8).
- Logging cleanup (M8).
- Filling in the transformation/reconstruction stubs.
- Unifying detection's pre-M3 `Evidence/Contradiction` model with
  `Anomaly` (long-standing caveat; M7 only asserts both surfaces remain
  structured, not that they merge).

---

## 8. Failure modes M7 itself must avoid

Meta-risks for the test suite:

1. **Tautological tests.** Asserting "an anomaly was emitted" without
   asserting *which* one. Mitigation: every category test pins
   `evidence.kind` and `location.stage`.
2. **Corpus rot.** Fixtures drift away from real Chroma 0.6 shape.
   Mitigation: `build_minimal_valid_chroma_06` is the *only* source of a
   "valid baseline"; every corruption builder takes its output and
   mutates it. The valid baseline is itself smoke-tested (it must pass
   `analyze` with exit `0`).
3. **Hidden non-determinism.** `tmp_path`-based, no global state, no
   network, no time.sleep, no random without a fixed seed. Symlink-loop
   and large-file fixtures are skipped on platforms that cannot represent
   them, and the skip is itself asserted (no silent skips).
4. **Test code masking production code.** No `try/except` in tests
   around the pipeline call — exceptions either surface as a stage-tagged
   `MigratorError` (assertable) or fail the test.
5. **CliRunner trap.** Already documented in M6 caveats: stdout/stderr
   separation under Click ≥ 8.2 must be tested via real `subprocess`,
   not `CliRunner`. M7 inherits this rule.

---

## 9. Exit-gate checklist (what "M7 done" means)

M7 is **PASS** only when **all** of the following hold:

- [ ] `tests/adversarial/` exists with the five category files and the
      invariants file listed in §3.1.
- [ ] At least one fixture per row of the §4 table is exercised.
- [ ] Every adversarial run has its `(exit_code, report, anomalies)`
      triple checked by **all nine** invariants in §5.
- [ ] No invariant is xfailed, skipped, or guarded by
      `pytest.mark.skipif` without a recorded platform reason.
- [ ] Every defect surfaced by M7 is fixed in the owning production
      module (not in test code) **or** explicitly recorded as an
      out-of-scope caveat in `ROADMAP.json`.
- [ ] Full test suite (existing 191 + M7 additions) is green.
- [ ] `TODO.json` phase 10 sub-tasks `10.1`–`10.5` are flipped to `done`
      with a `note` pointing to the test file that proves each one.
- [ ] `ROADMAP.json::current_position` is updated to `M7_done_M8_next`
      **only after** the seven items above are satisfied.

Until every box is ticked, M7 status remains `todo` and M8 must not
start. Partial completion is **CONDITIONAL FAIL**, not "in progress".
