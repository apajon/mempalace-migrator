# mempalace-migrator

An **experimental** reconstruction tool that attempts to read a MemPalace
stored under ChromaDB `0.6.x` and rebuild an equivalent palace under
ChromaDB `1.x`.

This is a research-grade utility. It is not a migration product.

---

## 1. What this project is

`mempalace-migrator` reads a ChromaDB `0.6.x` SQLite database directly,
extracts the documents and metadata it can identify, and reconstructs a
new ChromaDB `1.x` palace from that extracted material.

It works by **reconstruction**, not by in-place migration:

- The source palace is opened in SQLite read-only URI mode (`mode=ro`)
  and is never modified.
- A new, separate target palace is built from scratch using the public
  ChromaDB `1.x` Python client.
- Extracted records are inserted via the `1.x` client API, which means
  the embedding function used by `1.x` decides how vectors are produced.

There is no shared format between the two versions. The tool does not
upgrade a database; it transcribes what it can read into a new one.

---

## 2. What this project is NOT

- Not a migration tool. There is no rollback, no in-place upgrade, and
  no compatibility shim.
- Not a general ChromaDB conversion utility. It is hard-coded to one
  source structure and one target version.
- Not guaranteed to produce a target palace equivalent to the source.
- Not guaranteed to preserve every record from the source.
- Not guaranteed to terminate successfully on inputs it has not been
  tested against.
- Not a substitute for taking a backup of the source data.

---

## 3. Limitations and risks

Read this section before running the tool.

- **Embedding vectors are not transferred.** ChromaDB `1.x` recomputes
  embeddings using its own embedding function. Search results in the
  reconstructed palace will not be identical to those in the source,
  even when documents are byte-identical.
- **Output may be structurally valid but semantically incorrect.** The
  reconstructed palace may load, accept queries, and return results that
  differ from the source in ways the tool cannot detect.
- **Completeness is not guaranteed.** Rows that fail per-row integrity
  checks are excluded from the reconstruction and listed in the report.
  The tool continues; it does not refuse to produce a partial output.
- **Correctness is not guaranteed.** The tool verifies what it can
  (PRAGMA integrity, ID uniqueness, document presence, metadata
  resolvability). It cannot verify what it does not know to look for.
- **The tool may fail on inputs that other tooling considers valid.**
  Detection requires a manifest with a recognised `chromadb_version`.
  Palaces without one are rejected, even if they are otherwise readable.
- **Tested coverage is narrow.** Only the version pair listed below has
  been exercised. Behaviour on other versions, schemas produced by
  other tooling, or palaces written by patched ChromaDB builds is
  undefined.
- **Concurrent access to the source is not detected reliably.** The
  tool refuses to run when an uncheckpointed WAL file is present, but
  it cannot detect a concurrent reader-writer outside that signal.
- **Manifest authenticity is not verified.** A forged or stale manifest
  will be trusted.

A run that exits with code `0` means the tool completed without raising
a critical error. It does **not** mean the reconstructed palace is
correct.

---

## 4. Supported scope

The tool refuses to run outside this list.

| Source ChromaDB | Target ChromaDB |
|-----------------|-----------------|
| `0.6.3`         | `1.5.7`         |

Detection requires a manifest file (`mempalace-bridge-manifest.json`)
in the source palace directory containing both `compatibility_line` and
`chromadb_version` fields. Without these, the tool aborts before
extraction.

There are no plans for additional version pairs in this repository.
Each new pair requires re-validation against real palaces.

---

## 5. Philosophy

These are the design constraints the codebase is held to. They are
described here so that contributors and users understand why the tool
behaves as it does.

- **Traceability over convenience.** Every excluded row, every
  inconsistency, and every ambiguity is recorded as a structured
  anomaly in the report. The tool does not silently drop data.
- **Explicit reporting over silent success.** Each report contains an
  `explicitly_not_checked` list naming the conditions the tool does
  not verify. Silence in the output is not a guarantee.
- **Strict boundaries over broad support.** Anything outside the
  supported version pair, or below the required detection confidence,
  is rejected. There is no `--force` option.
- **Read-only by construction.** The source database is opened in
  SQLite `mode=ro`. The target palace is built in a separate location.
  There is no codepath that writes to the source.
- **Failure model is documented, not improvised.** Critical conditions
  raise and abort. Per-row issues are collected and reported. The
  difference is defined in code, not left to the caller.

---

## 6. Quickstart

> **Back up the source palace before running this tool.** Even though
> the source is opened read-only, the surrounding workflow (renames,
> moves, scripted cleanup) is the operator's responsibility.
>
> **Do not point the target path at an existing palace** unless you
> intend to overwrite it. The tool may refuse, but do not rely on this.
>
> **Inspect the report.** A successful exit code is not a correctness
> claim.

Install:

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e .
```

Analyse a source palace without writing anything:

```bash
.venv/bin/mempalace-migrator analyze /path/to/source-palace
.venv/bin/mempalace-migrator analyze /path/to/source-palace --json-output
```

`analyze` runs detection and extraction only. It does not produce a
target palace. The reconstruction and validation steps are not yet
implemented in this codebase and are exposed as explicit stubs.

---

## 7. Output and reporting

Each run produces a structured report (printed to stdout, or as JSON
with `--json-output`). The report contains:

- `detection`: classification, numeric confidence, source version,
  evidence list (every fact and inconsistency observed).
- `extraction`: collection name, PRAGMA integrity check result, full
  list of `failed_rows` with the reason each row was excluded.
- `extraction_stats`: `total_rows`, `parsed_rows`, `failed_rows`,
  `parse_rate`.
- `anomalies`: each anomaly is a structured object with
  `type`, `severity` (`low`, `medium`, `high`, `critical`), `stage`,
  `message`, and a `context` dictionary.
- `anomaly_summary`: counts by severity and by type.
- `explicitly_not_checked`: the list of conditions the tool does not
  verify. Always present, always non-empty.

Operators are expected to inspect the report. A non-empty
`failed_rows` list means data was excluded from the reconstruction. A
non-empty `anomalies` list with `severity >= high` means the run
contains conditions that warrant manual review before the output is
trusted for any purpose.

Exit codes:

| Code | Meaning |
|------|---------|
| 0    | Pipeline completed without raising a critical error |
| 2    | Detection failed (unsupported format, version, or insufficient confidence) |
| 3    | Extraction failed at a critical pre-flight check |
| 6    | Report could not be built |
| 10   | Unexpected error (use `--debug` to surface the traceback) |

---

## 8. Target audience

This tool is intended for operators who:

- understand the structural differences between ChromaDB `0.6.x` and
  `1.x`,
- can read SQLite directly to verify what the tool reports,
- accept that the output may need to be discarded after inspection,
- do not need a turnkey upgrade path.

If you need a supported migration product, this is not it.

---

## 9. Related projects

- **[mempalace-mcp-bridge](https://github.com/apajon/mempalace-mcp-bridge)**
  — the stable bridge between MemPalace and the Model Context Protocol.
  It is the production-oriented project. `mempalace-migrator` exists
  separately so that experimental reconstruction work does not affect
  the bridge's stability or its supported scope.

---

## 10. Status

Foundation only. Detection, extraction, structured reporting, and the
strict-scope CLI are implemented. Transformation, reconstruction, and
validation are present as explicit stubs and perform no work.

Until the reconstruction layer is implemented, the only useful command
is `analyze`, which produces a report describing what a reconstruction
attempt would have to deal with.
