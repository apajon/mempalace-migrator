# Detection — Design

Single source of truth for "what palace am I looking at?". Every other
stage trusts this answer or refuses to run. No other module performs
its own format sniffing.

---

## 1. Scope and non-goals

In scope:

- Classify a source directory as `chroma_0_6`, `chroma_1_x`, or `unknown`.
- Extract a `source_version` string when, and only when, a manifest says so.
- Produce an ordered, append-only `evidence` list documenting every fact
  and inconsistency observed.
- Produce a numeric `confidence` in `[0.0, 1.0]` whose semantics are
  defined below.

Out of scope:

- Deciding whether to abort the pipeline (the pipeline step does that by
  comparing `confidence` and `source_version` against
  `MIN_ACCEPT_CONFIDENCE` and `SUPPORTED_VERSION_PAIRS`).
- Reading rows. Detection only reads `sqlite_master` and `PRAGMA
  table_info(collections)`. It does not count or parse user data beyond
  the row counts already needed to distinguish "empty" from "populated".
- Writing anything. Detection is read-only and side-effect free.
- Healing, repairing, or guessing a missing/garbled manifest.

---

## 2. Data model

```
Evidence (frozen)
  source : 'manifest' | 'structure' | 'filesystem'
  kind   : 'fact' | 'inconsistency' | 'missing'
  detail : str   # short, machine-greppable

Contradiction (frozen)
  grade            : 'BENIGN' | 'SOFT' | 'HARD' | 'SEVERE' | 'MANIFEST_INTERNAL'
  reason           : str   # stable machine-readable tag
  manifest_class   : str
  structural_class : str   # for MANIFEST_INTERNAL: repurposed as
                           # "right side" of the intra-manifest conflict
                           # (i.e. the class derived from chromadb_version)

DetectionResult (frozen)
  palace_path     : str
  classification  : 'chroma_0_6' | 'chroma_1_x' | 'unknown'
  confidence      : float in [0.0, 1.0]
  confidence_band : 'LOW' | 'MEDIUM' | 'HIGH'   # derived from confidence
  source_version  : str | None     # only set from manifest
  evidence        : tuple[Evidence, ...]
  contradictions  : tuple[Contradiction, ...]   # see §10 for grades
  unknowns        : tuple[str, ...]             # aggregated 'missing' signals
```

Invariants enforced by construction:

- `evidence` is ordered. Order reflects the order of inspection
  (filesystem → manifest → structure → reconciliation). It is the
  primary debugging artifact.
- `source_version` is **never** inferred from structure. It is `None`
  unless the manifest carried a parseable `chromadb_version`.
- `classification == 'chroma_1_x'` requires manifest evidence. Structure
  alone cannot produce this value (see §4 rule R3).
- `confidence >= 0.9` requires manifest evidence (see §4 rule R1).
- `confidence` and `classification` are independent of one another only
  through the rules in §5; the function that builds the result is the
  single place those rules are applied.
- `confidence_band` is a categorical view of `confidence` with two
  break points: `HIGH` iff `confidence >= MIN_ACCEPT_CONFIDENCE` (0.9),
  `MEDIUM` iff `0.6 <= confidence < 0.9`, `LOW` otherwise. `HIGH` is by
  construction the band the pipeline gate accepts.
- `contradictions` enumerates **every** contradiction observed:
  manifest-vs-structure (grades per §10) and intra-manifest
  (`MANIFEST_INTERNAL`). `contradictions == ()` means "no contradiction
  detected", with no caveats — consumers do not need to parse evidence
  strings.
- `unknowns` aggregates every `evidence` entry whose `kind == 'missing'`
  into stable `"<source>:<detail>"` strings, in insertion order.
  Surfaces what detection could *not* determine without forcing
  consumers to filter the evidence list themselves.

`DetectionResult.to_dict` is the canonical JSON shape consumed by
`reporting`. No other serialisation exists.

---

## 3. Inputs and signal sources

Three signal sources, inspected in fixed order:

1. **Filesystem** — does `palace_path` exist, is it a directory.
2. **Manifest** — `mempalace-bridge-manifest.json` at the palace root.
   Read fields: `compatibility_line` (string), `chromadb_version`
   (string). Anything else is ignored at this stage.
3. **Structure** — `chroma.sqlite3` at the palace root, opened with
   `mode=ro`. Inspected: `sqlite_master` table list, `PRAGMA
   table_info(collections)`, `COUNT(*)` on `collections` and
   `embeddings`.

Each signal source is implemented in one private helper. The public
entry point (`detect_palace_format`) is the only function that combines
them. There is no second pathway.

---

## 4. Hard rules

These are non-negotiable. They are enforced in code, not in prose.

- **R1 — Manifest is the only high-confidence source.**
  `confidence >= 0.9` requires manifest evidence. Structural inspection
  alone caps confidence below the pipeline's `MIN_ACCEPT_CONFIDENCE`.
  Result: a palace without a manifest is not silently accepted.

- **R2 — `source_version` comes only from the manifest.**
  No structural pattern is interpreted as a version number. A palace
  with no manifest has `source_version = None` regardless of its
  schema.

- **R3 — Structure cannot promote to `chroma_1_x`.**
  The 1.x typed-config column is treated as a *signal*, not a verdict.
  Seeing it without a manifest yields `unknown` with low confidence and
  an evidence entry recording what was observed. Rationale: the same
  column name can appear in patched 0.6 builds; we refuse to commit on
  this signal alone.

- **R4 — Manifest contradicts structure → downgrade, do not flip.**
  Manifest classification wins. Confidence is capped (§5). An
  `inconsistency` evidence entry is appended. The pipeline step decides
  whether the resulting confidence still clears
  `MIN_ACCEPT_CONFIDENCE`.

- **R5 — `unknown` is the default.**
  Any path that cannot positively identify a format returns `unknown`.
  There is no "best guess" branch.

- **R6 — One result, one path.**
  Exactly one `DetectionResult` is produced per call. There is no retry
  loop, no fallback classifier, no second-chance heuristic. If a
  helper throws, it is a bug.

---

## 5. Confidence logic

Confidence is a single scalar with defined break points. It is **not**
a probability. It is a gate value designed so that `MIN_ACCEPT_CONFIDENCE
= 0.9` admits exactly the cases listed here.

Manifest-only confidence (set by `_classify_from_manifest`):

| Manifest state                                                | confidence |
|---------------------------------------------------------------|------------|
| `compatibility_line` and `chromadb_version` agree             | `1.00`     |
| `chromadb_version` parseable, `compatibility_line` absent     | `0.95`     |
| `compatibility_line` present, `chromadb_version` absent       | `0.70`     |
| `compatibility_line` and `chromadb_version` disagree          | `0.40`     |
| Manifest missing / unreadable / not JSON / not an object      | `0.00`     |

Structural-only confidence (set by `_classify_from_structure`):

| Structural state                                              | confidence |
|---------------------------------------------------------------|------------|
| All 0.6 tables present, both row counts > 0                   | `0.60`     |
| All 0.6 tables present, exactly one of the two counts is 0    | `0.50`     |
| All 0.6 tables present, both counts are 0                     | `0.45`     |
| All 0.6 tables present, row count query failed                | `0.40`     |
| Typed-config column observed (1.x signal, refused per R3)     | `0.30`     |
| Required 0.6 tables missing                                   | `0.10`     |
| `chroma.sqlite3` is empty (zero bytes)                        | `0.05`     |
| `chroma.sqlite3` missing / unopenable / `sqlite_master` bad   | `0.00`     |

Reconciliation (`detect_palace_format`):

- If the manifest produced a non-`unknown` classification, the manifest
  values are used as the base confidence and classification.
- If manifest and structure both produced non-`unknown` classifications
  and they disagree, an `inconsistency` evidence entry is appended and
  confidence is capped at `0.6`. Classification is **not** flipped (R4).
- Structural confidence is never added to manifest confidence; it never
  promotes manifest confidence either. They are alternatives, not terms.

Why these numbers:

- `0.9` was chosen as the gate so a manifest with only a parseable
  `compatibility_line` (no version) cannot pass — versions are required
  for the supported-pair gate.
- Structural ceilings sit below the gate by construction. Removing the
  manifest from a previously-passing palace must cause the pipeline to
  refuse the run, not silently accept it (R1, R2).
- `0.6` is the inconsistency cap: the manifest is still preferred, but
  the result is below the gate, so the pipeline aborts unless an
  operator looks at the evidence list.

---

## 6. Edge cases

Each row defines the externally observable result. The list is the
contract; tests should cover one case per row.

| Condition                                                     | classification | confidence cap | source_version | Evidence appended                          |
|---------------------------------------------------------------|----------------|----------------|----------------|--------------------------------------------|
| `palace_path` does not exist                                  | `unknown`      | `0.00`         | `None`         | `filesystem/missing`                        |
| `palace_path` is a file, not a directory                      | `unknown`      | `0.00`         | `None`         | `filesystem/fact`                           |
| Directory exists, manifest missing, DB missing                | `unknown`      | `0.00`         | `None`         | `manifest/missing`, `structure/missing`     |
| Manifest missing, DB present and valid 0.6                    | `chroma_0_6`   | `<= 0.60`      | `None`         | `manifest/missing`, `structure/fact`        |
| Manifest missing, DB present but empty (0 bytes)              | `unknown`      | `0.05`         | `None`         | `structure/fact` (empty)                    |
| Manifest missing, DB present, required tables absent          | `unknown`      | `0.10`         | `None`         | `structure/missing` (tables)                |
| Manifest missing, DB present, typed-config column observed    | `unknown`      | `0.30`         | `None`         | `structure/fact` (typed marker)             |
| Manifest unreadable (OS error)                                | `unknown`      | `0.00`+struct  | `None`         | `manifest/fact` (cannot read)               |
| Manifest not valid JSON                                       | `unknown`      | `0.00`+struct  | `None`         | `manifest/inconsistency` (bad JSON)         |
| Manifest top-level not an object                              | `unknown`      | `0.00`+struct  | `None`         | `manifest/inconsistency`                    |
| Manifest fields missing (`compatibility_line`, `chromadb_version`) | `unknown` | `0.20`+struct  | `None`         | two `manifest/missing` entries              |
| Manifest only carries `compatibility_line`                    | per line       | `0.70`         | `None`         | `manifest/fact`, `manifest/missing` version |
| Manifest only carries parseable `chromadb_version`            | per version    | `0.95`         | version string | `manifest/fact`, `manifest/missing` line    |
| Manifest line and version agree                               | per both       | `1.00`         | version string | two `manifest/fact`                         |
| Manifest line and version disagree                            | `unknown`      | `0.40`         | version string | `manifest/fact`+`manifest/inconsistency`    |
| Manifest says 0.6, structure says 0.6                         | `chroma_0_6`   | manifest val   | version string | manifest evidence + `structure/fact`        |
| Manifest says 0.6, structure says nothing useful              | `chroma_0_6`   | manifest val   | version string | manifest evidence + `structure/missing`     |
| Manifest says 0.6, structure has typed-config column          | `chroma_0_6`   | `0.60`         | version string | `structure/inconsistency`                   |
| Manifest says 1.x, structure says 0.6 (counts > 0)            | `chroma_1_x`   | `0.60`         | version string | `structure/inconsistency`                   |
| Manifest says 1.x, structure has 0.6 schema only              | `chroma_1_x`   | `0.60`         | version string | `structure/inconsistency`                   |
| Manifest carries a version we don't support (e.g. `0.5.0`)    | `unknown`      | manifest val   | `"0.5.0"`      | `manifest/fact`                             |
| Inconsistent rows: collections rows > 0, embeddings rows == 0 | from manifest  | per rules      | from manifest  | `structure/inconsistency`                   |

"`+struct`" means the structural confidence is used because the manifest
contributed `unknown`.

Cases explicitly rejected by R3: a directory with a 1.x-shaped DB and
**no** manifest is `unknown` with `confidence = 0.30`. The pipeline
will refuse it. This is intentional — see "rationale" in R3.

---

## 7. Failure surface

Detection itself does not raise. Any condition it cannot positively
identify becomes `unknown` with a corresponding evidence entry. Three
exception classes are caught and recorded as evidence rather than
propagated:

- `OSError` reading the manifest → `manifest/fact` (cannot read).
- `json.JSONDecodeError` → `manifest/inconsistency` (bad JSON).
- `sqlite3.Error` / `sqlite3.DatabaseError` opening or querying the DB
  → `structure/fact` or `structure/inconsistency` (depending on whether
  the failure is an open failure or a schema-read failure).

The pipeline step (`step_detect`) is the *only* place that converts a
detection outcome into a `DetectionError` — for unsupported
classification, low confidence, or unsupported version pair. Detection
returns; the pipeline decides.

This split keeps detection callable from tests and tools without
needing a `MigrationContext` or a try/except around it.

---

## 8. Single source of truth

- The classification, confidence, version, and evidence for a run are
  produced by exactly one call to `detect_palace_format` and stored
  once on `MigrationContext.detected_format`.
- The supported version pairs live in one tuple
  (`SUPPORTED_VERSION_PAIRS`). The pipeline gate, the report, and any
  future CLI surface read from that tuple — no parallel list.
- The acceptance threshold is one constant (`MIN_ACCEPT_CONFIDENCE`).
- The marker strings (`MANIFEST_FILENAME`, `SQLITE_FILENAME`,
  `TYPED_MARKER`, `CHROMA_0_6`, `CHROMA_1_X`, `UNKNOWN`) live in this
  module. Other modules import them; they do not redefine them.

If a future stage needs to know "is this 0.6", it reads
`ctx.detected_format.classification`. It does not reopen the SQLite
file to check.

---

## 9. Testing contract

Each row in §6 is one test. Additional tests:

- Manifest+structure agree on 0.6 → `confidence == 1.0` and
  classification `chroma_0_6`.
- Manifest+structure disagree → confidence capped at `0.6`,
  `inconsistency` evidence present, classification taken from manifest.
- Removing the manifest from a passing fixture flips the pipeline
  outcome from accept to reject (guards R1).
- Renaming the typed-config column into a 0.6-shaped DB without a
  manifest does not yield `chroma_1_x` (guards R3).
- A manifest with a `chromadb_version` outside
  `SUPPORTED_VERSION_PAIRS` returns successfully from detection but is
  rejected by the pipeline step (guards the split in §7).

Detection tests never need a real `MigrationContext`; the function
signature is `(Path) -> DetectionResult`.

---

## 10. Contradiction policy (refinement)

This section refines §4 (R4) and §5 (reconciliation). It is the
authoritative description of how manifest evidence and structural
evidence interact when they disagree. The earlier "manifest wins, cap
at 0.6" rule is too coarse — it produces high-enough confidence in
cases where the substrate fundamentally disagrees with the manifest.

### 10.1 Why the previous rule is insufficient

The original rule only fires when both `manifest_class` and
`structural_class` are non-`unknown` and differ. That ignores three
real failure modes:

- Manifest says `chroma_0_6` and structure returns `unknown` because the
  required 0.6 tables are **missing**. The previous logic accepts the
  manifest's confidence verbatim. The substrate is not a 0.6 palace at
  all.
- Manifest says `chroma_0_6` and structure returns `unknown` because
  the typed-config column (a 1.x signal) is present. Per R3 we still
  refuse to classify as 1.x, but per R4 we are also not flagging the
  conflict — the manifest's confidence is preserved unchanged.
- Manifest says `chroma_0_6` and structure returns `chroma_0_6` but
  with **inconsistent row counts** (one of `collections` /
  `embeddings` is empty, the other is not). Today this is recorded as
  a structural `inconsistency`, but it does not feed back into the
  reconciled confidence at all.

In each case the detector reports a confidence the operator should not
trust. The fix is to grade the disagreement and cap explicitly per
grade.

### 10.2 Revised policy

The detector classifies the relationship between manifest and structure
into one of five grades. The grade is computed by a single helper and
is the only input to the cap.

| Grade           | Trigger                                                                 | Classification kept | Confidence cap | New evidence                                       |
|-----------------|-------------------------------------------------------------------------|---------------------|----------------|----------------------------------------------------|
| **AGREE**       | Manifest non-unknown; structure non-unknown; classes match.             | manifest            | no cap         | none added (existing facts already record both)   |
| **BENIGN**      | Manifest non-unknown; structure `unknown` from a *neutral* cause: missing DB or unreadable DB. | manifest            | `0.85`         | `structure/missing` (already present); no new entry |
| **SOFT**        | Manifest = 0.6; structure = 0.6 but row counts inconsistent (exactly one of `collections`/`embeddings` is 0). | manifest (`chroma_0_6`) | `0.80`     | `structure/inconsistency` (row counts) — already emitted; reconciliation re-emits with `manifest_vs_structure` tag |
| **HARD**        | Manifest non-unknown; structure non-unknown; classes differ. **OR** Manifest = 0.6 and typed-config (1.x marker) is present. | manifest            | `0.60`         | `structure/inconsistency` describing the disagreement |
| **SEVERE**      | Manifest = 0.6 and structure proves the substrate is not 0.6: required 0.6 tables missing, or `chroma.sqlite3` is empty (0 bytes). | **`unknown`**       | `0.40`         | `structure/inconsistency` describing the contradiction |

Notes:

- "Cap" means `confidence = min(confidence, cap)`. The manifest-only
  confidence is the input.
- AGREE intentionally leaves manifest confidence intact (e.g. `1.0` when
  line + version cohere). Structure confirms; it does not amplify.
- BENIGN exists so a perfectly normal "manifest present, DB happens to
  be missing right now" case still falls **below** `MIN_ACCEPT_CONFIDENCE`
  by default (`0.85 < 0.9`). The pipeline refuses; the operator can
  see why from the evidence list rather than getting a green light on
  no substrate.
- SOFT and HARD both keep classification `chroma_0_6` and produce a
  confidence below the gate. The pipeline will refuse them. The
  difference is documentary: SOFT means "the palace is 0.6-shaped but
  oddly populated"; HARD means "the manifest and the schema disagree
  about which version this is".
- SEVERE is the only grade that **flips classification to `unknown`**.
  When the substrate disproves the manifest (no 0.6 tables, or no DB
  contents at all), the manifest is treated as untrustworthy. The
  resulting confidence (`0.40`) is well below the gate and the
  classification is no longer a positive identification.

### 10.2bis MANIFEST_INTERNAL grade (intra-manifest conflict)

The five grades above describe the manifest-vs-structure relationship.
A separate sixth grade, `MANIFEST_INTERNAL`, describes a contradiction
**inside** the manifest itself.

| Grade                 | Trigger                                                                                          | Classification | Confidence | Contradiction emitted                                                       |
|-----------------------|--------------------------------------------------------------------------------------------------|----------------|------------|-----------------------------------------------------------------------------|
| **MANIFEST_INTERNAL** | `compatibility_line` and `chromadb_version` both parseable but resolve to different classes.     | `unknown`      | `0.40`     | `grade=MANIFEST_INTERNAL`, `reason=line_vs_version`, both sides recorded.   |

Notes:

- A `MANIFEST_INTERNAL` contradiction makes `manifest_class == unknown`
  in the reconciliation step. The substrate-vs-manifest grading in
  §10.2 therefore does **not** run (there is nothing to compare
  against). The `Contradiction` is emitted by
  `_classify_from_manifest` directly, before reconciliation.
- `confidence_band` is `LOW` (0.40 < 0.6). The pipeline gate refuses
  the run.
- `source_version` is still set from the manifest's
  `chromadb_version` field when parseable (R2 unchanged: only the
  manifest can set it). The classification is `unknown`; the version
  string is preserved purely as a debugging aid.
- For `MANIFEST_INTERNAL`, the `Contradiction` field-name pair
  `(manifest_class, structural_class)` is repurposed as
  `(line-derived class, version-derived class)`. This is documented in
  the `Contradiction` docstring.
- A consumer iterating `result.contradictions` therefore sees every
  contradiction in one place — both manifest-vs-structure and
  intra-manifest. There is no scenario in which the field is empty
  while a detection-time contradiction exists.

### 10.3 R3 is unchanged

The typed-config column observed without a manifest still yields
`unknown` with `confidence = 0.30` (§5). The new grading only changes
behaviour when a manifest is present: with a 0.6 manifest plus a
typed-config column, the result is now HARD — classification stays
`chroma_0_6`, confidence is capped at `0.60`, and an
`inconsistency` evidence entry is emitted. We never promote to
`chroma_1_x` from structure. This is a tightening of R4, not a
relaxation of R3.

### 10.4 Updated confidence rules (delta vs §5)

Reconciliation in `detect_palace_format` becomes:

1. Compute `manifest_class`, `manifest_conf`, `manifest_version` from
   the manifest helper.
2. Compute `structural_class`, `structural_conf`, plus a small
   `structural_signals` record from the structure helper.
   `structural_signals` exposes the booleans the grading needs:
   `db_present`, `db_empty`, `required_tables_present`,
   `typed_marker_present`, `row_counts_inconsistent`.
3. If `manifest_class == UNKNOWN`, return `(structural_class,
   structural_conf, None, evidence)` exactly as today. The manifest
   contributed nothing; structure stands on its own (and per R1/R3 can
   never reach the gate by itself).
4. Otherwise, classify the grade per §10.2 and apply the cap.

The structural-only and manifest-only tables in §5 are unchanged. Only
the reconciliation step is changed. There is one new constant per cap
(`CAP_BENIGN = 0.85`, `CAP_SOFT = 0.80`, `CAP_HARD = 0.60`,
`CAP_SEVERE = 0.40`) defined alongside `MIN_ACCEPT_CONFIDENCE`.

### 10.5 Concrete examples

Each example is one observable result and one test.

1. **Manifest 0.6.3 + line agrees + DB has 0.6 tables and rows** → AGREE
   → `chroma_0_6`, confidence `1.00`, `source_version="0.6.3"`. No
   inconsistency.

2. **Manifest 0.6.3 + DB missing** → BENIGN → `chroma_0_6`, confidence
   `0.85`, `source_version="0.6.3"`, evidence includes
   `structure/missing` for `chroma.sqlite3`. Pipeline refuses
   (`0.85 < 0.9`).

3. **Manifest 0.6.3 + DB has 0.6 tables, `embeddings` count=0,
   `collections` count=3** → SOFT → `chroma_0_6`, confidence `0.80`,
   `source_version="0.6.3"`, evidence includes
   `structure/inconsistency` (row counts) and a reconciliation
   `structure/inconsistency` tagged
   `manifest_vs_structure: row_counts_inconsistent`.

4. **Manifest 0.6.3 + DB has 0.6 tables AND a typed-config column on
   `collections`** → HARD → `chroma_0_6`, confidence `0.60`,
   `source_version="0.6.3"`, evidence includes the typed-marker fact
   and a reconciliation `structure/inconsistency` tagged
   `manifest_vs_structure: typed_marker_present`. Classification is
   **not** flipped to `chroma_1_x` (R3).

5. **Manifest says `chromadb-1.x` + DB is unmistakably 0.6** → HARD →
   `chroma_1_x`, confidence `0.60`, `source_version` from manifest.
   Classification follows the manifest (R4); the operator sees the
   contradiction and the pipeline refuses below the gate.

6. **Manifest 0.6.3 + DB present but required 0.6 tables missing** →
   SEVERE → **`unknown`**, confidence `0.40`,
   `source_version="0.6.3"` (the field still records what the manifest
   said, but classification is no longer `chroma_0_6`), evidence
   includes `structure/missing` (tables) and a reconciliation
   `structure/inconsistency` tagged
   `manifest_vs_structure: required_tables_missing`.

7. **Manifest 0.6.3 + `chroma.sqlite3` is 0 bytes** → SEVERE →
   `unknown`, confidence `0.40`. Same shape as (6); reconciliation
   tag `manifest_vs_structure: db_empty`.

The pipeline's behaviour is unchanged for AGREE; for every other grade
the run is refused at the detection gate. The tightening only removes
false positives — it never creates new accepted cases.

### 10.6 Minimal code changes

Only two functions change. No data model change is required beyond
adding a tiny structural-signals tuple internal to the detector.

1. `_classify_from_structure` — return a third element alongside
   `(class, confidence)`: a `StructuralSignals` namedtuple with
   `db_present: bool`, `db_empty: bool`,
   `required_tables_present: bool`, `typed_marker_present: bool`,
   `row_counts_inconsistent: bool`. All fields are derived from
   information the function already collects; no new SQL is issued.

2. `detect_palace_format` — replace the current reconciliation block
   with a call to a new private helper `_grade_contradiction(manifest_class,
   structural_class, structural_signals) -> Grade` and a single
   `match`/`if` that applies the cap from §10.2 and, for `SEVERE`,
   sets `classification = UNKNOWN`. The helper appends one
   `structure/inconsistency` evidence entry per non-AGREE grade with
   a stable tag (`manifest_vs_structure: <reason>`). The original
   "manifest wins, cap at 0.6" branch is deleted.

3. New module-level constants:
   `CAP_BENIGN = 0.85`, `CAP_SOFT = 0.80`, `CAP_HARD = 0.60`,
   `CAP_SEVERE = 0.40`, plus a `Grade` enum with members
   `AGREE`, `BENIGN`, `SOFT`, `HARD`, `SEVERE`. The enum is private
   (`_Grade`) — it is not exported. Callers continue to read
   `classification`, `confidence`, and `evidence` only.

No change to: `Evidence`, `DetectionResult`, `_classify_from_manifest`,
`SUPPORTED_VERSION_PAIRS`, `MIN_ACCEPT_CONFIDENCE`, the pipeline step,
the report shape. The grading helper is fully unit-testable in
isolation: input is two classes and a signals tuple, output is a grade.

### 10.7 Test additions

One test per grade in §10.2 plus one regression test per example in
§10.5. Plus:

- Removing the manifest from any HARD/SEVERE fixture must yield
  `unknown` with structural-only confidence (regression on §10.4 step 3).
- Replacing the manifest with one whose `chromadb_version` is outside
  `SUPPORTED_VERSION_PAIRS` does not change the grade — gate enforcement
  remains the pipeline's job (§7).
