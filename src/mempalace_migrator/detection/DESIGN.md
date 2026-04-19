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

DetectionResult (frozen)
  palace_path    : str
  classification : 'chroma_0_6' | 'chroma_1_x' | 'unknown'
  confidence     : float in [0.0, 1.0]
  source_version : str | None     # only set from manifest
  evidence       : tuple[Evidence, ...]
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
