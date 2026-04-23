# M14 — Truth Alignment & Public Surface: Implementation Strategy

Status: **design only** (no documentation changes merged yet).
Scope: phase 17 in `TODO.json` (`17.1 .. 17.5`).
Predecessors satisfied: M1–M13 exit gates (see `ROADMAP.json::current_position`
→ `M13_done`, 844 tests green).
Successor: M15 (CI & Verification Baseline).

This document fixes the *shape* of M14 work. M14 is a **documentation
milestone**. Like M13 it adds:

- **No** new pipeline stage.
- **No** new CLI subcommand, flag, or positional argument.
- **No** new `AnomalyType`, `Severity`, `CheckFamily`, or
  `SkippedReason` value.
- **No** new exit code.
- **No** new production module.
- **No** new runtime behaviour. Any drift caught between docs and code
  is resolved by **updating the docs**, not the code — unless the code
  itself is already outside its documented contract, in which case the
  drift is a defect report filed against the owning phase (not fixed
  in M14).

M14 is a **public-surface proof obligation**: the first human-readable
surface of the tool (README, ARCHITECTURE, CLI `--help`, report schema)
must match — verbatim, not approximately — what the tested code does.

---

## 1. M14 in one sentence

> Every externally observable claim made by the repository (README,
> ARCHITECTURE, CLI `--help`, report schema) matches an existing test
> or an existing enum value; every documented behaviour is reachable;
> every reachable behaviour is documented; no correctness vocabulary
> slips back in.

If, after M14, any of the following is observable on `main`:

- A README sentence with no corresponding test or enum member.
- A CLI subcommand / flag / exit code implemented but absent from
  README §6 or §7.
- A CLI subcommand / flag / exit code documented but absent from
  `cli/main.py`.
- A report top-level key listed in `_REPORT_KEYS` but not described in
  README §7, or described in §7 but not present in `_REPORT_KEYS`.
- An anomaly severity, check family, or skipped reason mentioned in
  README but not present in `core/context.py` / `validation/_types.py`.
- A correctness-suggesting word (see §4) in README, ARCHITECTURE, or
  CLI help text.
- A "supported version" claim that does not match `pyproject.toml`'s
  `chromadb` pin.

...then **M14 is not done**, regardless of what PR descriptions or
commit messages say.

---

## 2. Why this milestone exists (honesty contract)

M1–M13 built a system whose defining property is *structured honesty*:
every drop, every skipped check, every uncertainty is represented
in-band, with a stable enum, at the right severity. That honesty
contract only holds for a user who reads the **report**.

Users read the **README first**. If the README lies — even by
omission, even by using friendlier-sounding words than the code — the
whole honesty architecture collapses at the front door:

- An operator who sees "migrate" in §6 but no exit code `5` in §7
  cannot react correctly to a reconstruction failure.
- An operator who sees "explicitly_not_checked" in §7 but not the
  current contents (post-M11 parity landed, pre-M11 entries removed)
  will either over-trust or under-trust the output.
- An operator who sees "successful" or "verified" in README prose —
  words M7 bans in the report — is being handed back the very
  guarantee the code refuses to give.

M14 closes that gap. It does not add trust; it makes sure the trust
the code already justifies is not silently inflated by the docs.

---

## 3. Boundaries (what M14 may NOT touch)

| Touch | Allowed? | Why |
|-------|----------|-----|
| `src/mempalace_migrator/**` | **No, except docstrings and CLI `--help` strings that are themselves documentation** | M14 is doc-only. Behaviour changes belong to the owning phase. |
| `tests/**` except new doc-parity tests under `tests/docs/` | **No** | Existing tests are the spec M14 is aligning *to*. |
| `pyproject.toml` | **No** | Version pin is the source of truth; docs align to it, not the other way. |
| `ROADMAP.json`, `TODO.json` | **Yes**, status updates + design-doc references only | Standard milestone bookkeeping. |
| `README.md`, `ARCHITECTURE.md`, `LICENSE` | **Yes** | Primary deliverable. |
| New top-level doc files | **Only if §5 explicitly requires one** | No sprawl. |
| New CLI flags or subcommands | **No** | Explicit non-goal. |
| New report keys | **No** | Explicit non-goal. |
| Rewording a claim to hide drift | **No** | Drift is a defect; escalate, don't paper over. |

---

## 4. Forbidden vocabulary (mirrors M7)

M7 bans certain words in the **report**; M14 extends the same ban to
all Markdown docs and to any string emitted by `--help`. The canonical
list (to be re-derived from
`tests/adversarial/_invariants.py::FORBIDDEN_VOCABULARY` at audit
time — not copy-pasted here, to avoid drift):

- "verified", "verification succeeded"
- "correct", "correctness guaranteed"
- "safe", "guaranteed safe"
- "equivalent to source"
- "migration successful" / "successful migration"
- "lossless"

Allowed replacements the code already uses:

- "completed without raising a critical error"
- "no CRITICAL anomalies recorded"
- "structurally consistent with the transformed bundle"
- "reopenable by the pinned chromadb client"

M14 §5.2 includes an automated grep-style assertion (see §7) so that
future PRs cannot reintroduce banned words in docs without tripping a
test.

---

## 5. Deliverables (task-level)

Each deliverable maps 1:1 to a `TODO.json` phase-17 task. Every
deliverable is **evidence-based**: it starts from reading the code,
not from memory.

### 5.1 Task 17.1 — `audit_doc_vs_code`

**Output:** a single audit table, committed as an appendix to this
design doc (appended in an implementation PR, not now), listing every
external claim in `README.md` and `ARCHITECTURE.md` with:

| column | meaning |
|--------|---------|
| `doc_location` | file + section (e.g. `README §7`) |
| `claim` | verbatim sentence or bullet |
| `backed_by` | test id, enum member, or code symbol |
| `status` | `aligned` / `drift-doc-wrong` / `drift-code-wrong` / `stale` |

Known drift candidates (to be confirmed by the audit, not assumed):

- README §7 "Exit codes" table lists `0, 2, 3, 6, 10`. `cli/main.py`
  defines `0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10`. The missing six codes
  (`1, 4, 5, 7, 8, 9`) are reachable and tested.
- README §7 "report contains" list names `detection, extraction,
  anomalies, extraction_stats, anomaly_summary, explicitly_not_checked`.
  `report_builder._REPORT_KEYS` also includes `transformation,
  reconstruction, validation, stages, confidence_summary,
  schema_version`.
- README §3 "The tool may refuse, but do not rely on this" is an
  under-claim: `reconstruction/_safety.py` *does* refuse non-empty
  existing target directories unconditionally (M10 exit gate).
- README §6 does not mention that `migrate` writes a manifest file at
  `<target>/mempalace-bridge-manifest.json` (M10 deliverable).

`drift-code-wrong` entries are **not fixed in M14**; they are filed as
defects against the owning phase and listed in the design doc's §11
"Caveats discovered during M14". The exit gate does not require
zero code drift; it requires *zero undocumented drift*.

### 5.2 Task 17.2 — `rewrite_readme`

**Output:** `README.md` rewritten section-by-section, keeping the
existing nine-section skeleton (1. What this is, 2. What this is NOT,
3. Limitations, 4. Supported scope, 5. Philosophy, 6. Quickstart,
7. Output & reporting, 8. Target audience, 9. Related projects).

Required changes, in order of severity:

1. **§7 exit-code table** — expanded to all 11 codes, each mapped to a
   one-line trigger. Source of truth: `cli/main.py::_EXIT_BY_STAGE`
   plus the module-level `EXIT_*` constants.
2. **§7 report-shape list** — expanded to every key in
   `_REPORT_KEYS`, with a one-line contract per key (what is always
   present, what may be `None`, what is structurally guaranteed by
   existing tests). `schema_version` is called out as the stable
   integer contract that external consumers may rely on.
3. **§3 limitations** — "may refuse" is strengthened to "refuses" for
   the non-empty-target case. A new bullet documents: atomicity
   (partial writes rolled back on any failure after `mkdir`), embedding
   re-derivation by chromadb 1.x, manifest authenticity not verified.
4. **§6 Quickstart** — each of the four subcommands (`analyze`,
   `inspect`, `migrate`, `report`) gets a one-paragraph contract
   stating: reads from where, writes to where (or "no writes"),
   required flags, exit-code range it may produce, and what artefact
   it leaves on disk. `migrate` explicitly mentions the manifest file.
5. **§4 Supported scope** — the version pair row is re-derived from
   `pyproject.toml` (`chromadb>=1.5.7,<2`) and from detection's
   accepted `chromadb_version` set, not restated independently. A
   follow-up M15 task will enforce this by test; M14 only writes the
   prose and notes the follow-up.
6. **No forbidden vocabulary** (§4 of this design doc).

### 5.3 Task 17.3 — `document_cli_surface`

**Output:** a single new section inside `README.md` (between §6 and
§7, renumbered accordingly), titled **"CLI reference"**, containing
one subsection per subcommand with:

- exact invocation (positional args, flags, short/long forms);
- what it reads, what it writes, what it never touches;
- the subset of exit codes it may legitimately produce;
- the subset of `_REPORT_KEYS` it populates (e.g. `analyze` never
  populates `reconstruction`, `validation.parity`, etc.).

The per-command contract is **derived from code**, not invented:
`analyze` → `ANALYZE_PIPELINE`, `inspect` → `FULL_PIPELINE` without
`reconstruct`, `migrate` → `MIGRATE_PIPELINE`, `report` → re-render only.

M14 must not introduce per-command exit-code partitions that are not
already provable from the pipeline composition. If a partition cannot
be proven by the existing test suite, the doc uses the full 0..10
range and a caveat, rather than a narrower claim.

### 5.4 Task 17.4 — `document_guarantees`

**Output:** a single new section `README.md §10 "Guarantees"`, listing
every property the project *does* commit to, each paired with the test
or invariant that proves it. Structure:

| guarantee | enforced by |
|-----------|-------------|
| Source file bytes unchanged after any subcommand | `tests/test_migrate_e2e.py::test_source_unchanged` |
| Target rolled back on any post-`mkdir` failure | `tests/adversarial/test_reconstruction_rollback.py` |
| Every skipped check has a `SkippedReason` | `validation/_types.py::SkippedReason` + `tests/test_validation_parity.py` |
| Exit code `0` implies no CRITICAL anomaly | `cli/main.py::_decide_exit_code` (code 8 guard) + `tests/test_cli*.py` |
| Report schema has a stable integer `schema_version` | `reporting/report_builder.py::REPORT_SCHEMA_VERSION` |
| Detection only accepts the single documented pair | `detection/format_detector.py` + `tests/test_format_detector_*.py` |
| Reconstruction never writes to source | M10 exit-gate tests |

This list is closed: if a property is not in this table, the project
does **not** guarantee it. In particular, the table must **not**
include: retrieval parity, usage-scenario parity, MCP-runtime parity,
embedding equivalence, semantic correctness, or completeness under
corruption.

### 5.5 Task 17.5 — `document_limitations`

**Output:** rewrite of `README §3 "Limitations and risks"` so that
every limitation is traceable to either a `ROADMAP.json::non_goals`
entry or a `current_position.caveats` entry or a real test. Each
limitation gets an explicit *reachability note*: "this can happen on
input X", not "this might theoretically happen".

New or sharpened limitations to surface (pending §5.1 audit
confirmation):

- Detection's pre-M3 `Evidence` / `Contradiction` model is not unified
  with `core/context.Anomaly` (caveat #1).
- `add_anomaly(stage=, context=)` legacy shape still accepted (caveat
  #2).
- `inspect` exits `0` when reconstruction is skipped; the skip is
  surfaced in `stages.reconstruct` with `reason="no_target_path"`
  (caveat #3).
- `CliRunner` stdout/stderr merging under Click 8.2+ (caveat #4).
- Empty-dict metadata is coerced to `None` before `collection.add()`
  because chromadb 1.5.7 rejects empty dicts (caveat #5); this is a
  behaviour, not a bug, and the docs must say so.
- `TARGET_MKDIR_FAILED` exists as a distinct anomaly from
  `TARGET_PATH_NOT_DIRECTORY` (caveat #6).

Deferred M7 findings (caveats #8 / #9 / #10) are listed but tagged as
"known, not scheduled"; M14 does not promise to fix them.

---

## 6. Non-goals (explicit)

- **No** rewriting of any `*_DESIGN.md` file. Those are historical
  records of each milestone's intent; they are immutable after their
  milestone closes.
- **No** new `CHANGELOG.md`. Release discipline is M16.
- **No** API reference generation, Sphinx setup, readthedocs hook.
  External API documentation is a later milestone (M18 / M19).
- **No** screenshots, asciicasts, video, or diagrams beyond what
  `ARCHITECTURE.md` already contains.
- **No** linking to external documentation that is not part of this
  repository, except the existing `mempalace-mcp-bridge` link in §9
  and the canonical chromadb release page (if the §4 scope row needs
  a citation).
- **No** marketing language. The README is an operator manual.

---

## 7. Testability of a documentation milestone

M14 still produces testable artefacts. The proposed new test module is
`tests/docs/test_doc_surface.py` (single new file, no production
code). It asserts:

1. **Exit-code table parity.** Every `EXIT_*` constant in
   `cli/main.py` appears as a row in `README §7`. Every row value
   corresponds to an `EXIT_*` constant. Implemented as a parser + set
   equality, not a regex shortcut. Failing pairs are reported by value.
2. **Report-key parity.** Every key in
   `reporting.report_builder._REPORT_KEYS` is mentioned by name in
   `README §7` (or the new "CLI reference" section). Every key named
   in those sections is in `_REPORT_KEYS`.
3. **Forbidden-vocabulary scan.** `README.md` and `ARCHITECTURE.md`
   contain none of the strings in
   `tests/adversarial/_invariants.py::FORBIDDEN_VOCABULARY`
   (case-insensitive, word-boundary-aware). This is the same assertion
   M7 already runs against report payloads; M14 extends it to docs.
4. **Version-pin consistency.** The `chromadb` version string in
   `README §4` matches the dependency specifier in `pyproject.toml`.
   (A stricter check against the detected `chromadb_version` set is
   an M15 task.)
5. **CLI help drift.** For each of the four subcommands, the
   short-help line exposed by `click` begins with the same summary as
   the first sentence of its `README §6 "CLI reference"` subsection.
   (String-prefix match, not equality, to allow tense variation.)

If any of these five assertions cannot be written without touching
production code, that assertion is **dropped**, not worked around. M14
is doc-only; docs don't earn new production code.

---

## 8. Failure modes M14 must not introduce

- **Silent doc regression.** Fixed by §7.1 / §7.2 / §7.3 tests — drift
  after M14 trips CI, not a future reader.
- **Overclaiming via synonyms.** Mitigated by §7.3 vocabulary scan
  and the closed-list philosophy of §5.4.
- **Underclaiming to hide drift.** If the audit finds the code stricter
  than the docs say (README §3 "may refuse" vs actual "refuses"), the
  docs are strengthened; the code is not weakened to match loose prose.
- **Doc–test circular reference.** Tests must cite enums/constants,
  not README strings. Docs may cite tests for evidence but must not
  paraphrase them.
- **Markdown-format churn.** No table library, no HTML, no
  admonitions. Existing README style is preserved; only the content
  changes.
- **Scope creep into M15.** CI wiring, version badges, release notes,
  and any GitHub Actions file are out of scope. M14 produces the
  tests under `tests/docs/`; M15 wires them into CI.

---

## 9. Exit-gate checklist (what "M14 done" means)

M14 is done **only** when every one of the following holds on `main`:

1. `tests/M14_TRUTH_ALIGNMENT_DESIGN.md` committed (this file).
2. `TODO.json` phase 17 references this design doc in a `design_doc`
   field mirroring M10–M13 convention.
3. Task 17.1 audit appendix committed (may live as
   `tests/docs/AUDIT.md` or as an appendix to this file; one of the
   two, not both).
4. `README.md` rewritten per §5.2 / §5.3 / §5.4 / §5.5. Section
   numbers in §5.2 updated if the new "CLI reference" section is
   inserted.
5. `ARCHITECTURE.md` reviewed; any drift found is either fixed in
   place (doc-only) or filed as a caveat in §5.1.
6. `tests/docs/test_doc_surface.py` landed with the five assertions
   of §7 (minus any that §7's last paragraph forced to be dropped,
   each drop documented in the audit appendix).
7. The full test suite remains green. No test currently passing may
   be weakened or skipped by M14.
8. `ROADMAP.json::current_position.milestone` advanced to `M14_done`
   **only after** 1–7 are all true.
9. No new runtime-observable behaviour introduced. `git diff` on
   `src/mempalace_migrator/**` is empty except for docstring and
   `--help` string changes, each of which is a prose change that
   already tracks code that existed before M14.
10. No forbidden vocabulary in `README.md`, `ARCHITECTURE.md`, or any
    string emitted by `mempalace-migrator --help` / subcommand help.

If any of 1–10 is false, M14 is **not** done, regardless of how
complete the PR description looks.

---

## 10. What M14 explicitly does NOT prove

- M14 does **not** prove the code is correct. It only proves the docs
  do not **claim** more than the code already demonstrates via tests.
- M14 does **not** prove the docs are complete. A reader may still
  need to read tests to understand subtle behaviour. M14 only forbids
  docs that **contradict** or **omit** externally observable surface.
- M14 does **not** lock the CLI surface. Future milestones may add
  flags or subcommands; they must then extend the docs under the same
  rules.
- M14 does **not** provide migration examples on real palaces. A
  sample dataset is M18.

---

## 11. Caveats that will carry forward past M14

(Populated when the audit of §5.1 runs. Initial known entries,
pre-audit, copied from `ROADMAP.json::current_position.caveats` — the
audit may add to this list; it may not remove from it without
evidence.)

- Detection's Evidence / Contradiction model remains un-unified with
  `core/context.Anomaly`.
- Legacy `add_anomaly(stage=, context=)` shape is still accepted.
- `inspect` exits `0` when reconstruction is skipped.
- `CliRunner` cannot separate stdout from stderr under Click 8.2+;
  subprocess tests compensate.
- Empty-dict metadata coerced to `None` before `collection.add()`.
- `TARGET_MKDIR_FAILED` vs `TARGET_PATH_NOT_DIRECTORY` distinction.
- Lazy `chromadb` import in `validation/parity.py` (startup-time
  hardening).
- M7 deferred findings (duplicate-id anomaly granularity; detection
  pre-scan shadowing some extraction error codes; corpus gaps called
  out in `M7_ADVERSARIAL_DESIGN.md §4 row 10.5`).

These are **documented**, not **fixed**, by M14.
