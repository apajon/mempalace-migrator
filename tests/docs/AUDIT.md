# M14 — Doc-vs-Code Audit Table (phase 17, task 17.1)

Status: **completed**. Committed as part of the M14 implementation PR.
Source of truth at audit time: HEAD of `main` after M13 (`milestone: M13_done`,
844 tests green).

---

## Audit table

Every claim in `README.md` and `ARCHITECTURE.md` that was checked against the
implementation is listed below with its resolution status.

Columns:
- `doc_location` — file + section
- `claim` — verbatim sentence or paraphrase of the claim
- `backed_by` — test id, enum member, or code symbol that backs the claim
- `status` — `aligned` / `drift-doc-wrong` / `drift-code-wrong` / `stale`

`drift-doc-wrong` entries were fixed in M14 by updating the docs.
`drift-code-wrong` entries are filed as caveats (see §11 of the design doc);
they are not changed in M14.

---

### README §7 exit-code table (pre-M14)

| doc_location | claim | backed_by | status |
|---|---|---|---|
| README §7 (old) | Exit code `0` listed | `cli/main.py::EXIT_OK = 0` | `aligned` |
| README §7 (old) | Exit code `2` listed | `cli/main.py::EXIT_DETECTION_FAILED = 2` | `aligned` |
| README §7 (old) | Exit code `3` listed | `cli/main.py::EXIT_EXTRACTION_FAILED = 3` | `aligned` |
| README §7 (old) | Exit code `6` listed | `cli/main.py::EXIT_REPORT_FAILED = 6` | `aligned` |
| README §7 (old) | Exit code `10` listed | `cli/main.py::EXIT_UNEXPECTED = 10` | `aligned` |
| README §7 (old) | Exit code `1` **absent** | `cli/main.py::EXIT_USAGE_ERROR = 1` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | Exit code `4` **absent** | `cli/main.py::EXIT_TRANSFORM_FAILED = 4` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | Exit code `5` **absent** | `cli/main.py::EXIT_RECONSTRUCT_FAILED = 5` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | Exit code `7` **absent** | `cli/main.py::EXIT_VALIDATE_FAILED = 7` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | Exit code `8` **absent** | `cli/main.py::EXIT_CRITICAL_ANOMALY = 8` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | Exit code `9` **absent** | `cli/main.py::EXIT_REPORT_FILE_ERROR = 9` | `drift-doc-wrong` — **fixed in M14** |

**Resolution:** README §8 (new) "Exit codes" table now lists all 11 codes.
Automated parity test: `tests/docs/test_doc_surface.py::test_exit_code_table_parity`.

---

### README §7 report-shape list (pre-M14)

| doc_location | claim | backed_by | status |
|---|---|---|---|
| README §7 (old) | `detection` listed | `REPORT_TOP_LEVEL_KEYS` | `aligned` |
| README §7 (old) | `extraction` listed | `REPORT_TOP_LEVEL_KEYS` | `aligned` |
| README §7 (old) | `extraction_stats` listed | `REPORT_TOP_LEVEL_KEYS` | `aligned` |
| README §7 (old) | `anomalies` listed | `REPORT_TOP_LEVEL_KEYS` | `aligned` |
| README §7 (old) | `anomaly_summary` listed | `REPORT_TOP_LEVEL_KEYS` | `aligned` |
| README §7 (old) | `explicitly_not_checked` listed | `REPORT_TOP_LEVEL_KEYS` | `aligned` |
| README §7 (old) | `schema_version` **absent** | `REPORT_TOP_LEVEL_KEYS` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | `tool_version` **absent** | `REPORT_TOP_LEVEL_KEYS` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | `supported_version_pairs` **absent** | `REPORT_TOP_LEVEL_KEYS` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | `run_id` **absent** | `REPORT_TOP_LEVEL_KEYS` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | `started_at` **absent** | `REPORT_TOP_LEVEL_KEYS` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | `completed_at` **absent** | `REPORT_TOP_LEVEL_KEYS` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | `outcome` **absent** | `REPORT_TOP_LEVEL_KEYS` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | `failure` **absent** | `REPORT_TOP_LEVEL_KEYS` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | `input` **absent** | `REPORT_TOP_LEVEL_KEYS` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | `transformation` **absent** | `REPORT_TOP_LEVEL_KEYS` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | `reconstruction` **absent** | `REPORT_TOP_LEVEL_KEYS` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | `validation` **absent** | `REPORT_TOP_LEVEL_KEYS` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | `stages` **absent** | `REPORT_TOP_LEVEL_KEYS` | `drift-doc-wrong` — **fixed in M14** |
| README §7 (old) | `confidence_summary` **absent** | `REPORT_TOP_LEVEL_KEYS` | `drift-doc-wrong` — **fixed in M14** |

**Resolution:** README §8 (new) lists all 20 top-level report keys with one-line
contracts. Automated parity test: `tests/docs/test_doc_surface.py::test_report_key_parity`.

---

### README §3 limitations (pre-M14)

| doc_location | claim | backed_by | status |
|---|---|---|---|
| README §3 (old) | "The tool **may** refuse" (non-empty target) | `reconstruction/_safety.py` unconditionally refuses non-empty dirs | `drift-doc-wrong` — **fixed in M14** |
| README §3 (old) | No mention of atomicity / rollback | `tests/adversarial/test_reconstruction_rollback.py` | `drift-doc-wrong` — **fixed in M14** |
| README §3 (old) | No mention of manifest written by `migrate` | `reconstruction/_manifest.py::TARGET_MANIFEST_FILENAME` | `drift-doc-wrong` — **fixed in M14** |
| README §3 (old) | No mention of empty-dict metadata coercion | `reconstruction/_writer.py` (M10 impl. detail) | `drift-doc-wrong` — **fixed in M14** |
| README §3 (old) | No mention of Detection Evidence/Contradiction not unified with Anomaly | `ROADMAP.json::caveats[0]` | `drift-doc-wrong` — **fixed in M14** |
| README §3 (old) | No mention of `inspect` exiting `0` when reconstruction skipped | `ROADMAP.json::caveats[2]` | `drift-doc-wrong` — **fixed in M14** |

---

### README §6 Quickstart (pre-M14)

| doc_location | claim | backed_by | status |
|---|---|---|---|
| README §6 (old) | Only `analyze` and `migrate` commands shown explicitly | `cli/main.py` has 4 subcommands: analyze, inspect, migrate, report | `drift-doc-wrong` — **fixed in M14** |
| README §6 (old) | No per-subcommand contract (reads/writes/exit-codes/artefacts) | `cli/main.py` + pipeline definitions | `drift-doc-wrong` — **fixed in M14** |
| README §6 (old) | "The tool may refuse" in warning block | actual behaviour: tool **refuses** unconditionally | `drift-doc-wrong` — **fixed in M14** |

---

### Forbidden vocabulary (pre-M14)

| doc_location | word | status |
|---|---|---|
| README §1 | "not guaranteed" | `drift-doc-wrong` — **fixed in M14** |
| README §2 | "Not guaranteed" (×4) | `drift-doc-wrong` — **fixed in M14** |
| README §3 | "not guaranteed", "not verified", "valid" (×3) | `drift-doc-wrong` — **fixed in M14** |
| README §6 | "not guaranteed" | `drift-doc-wrong` — **fixed in M14** |
| README §8 (audience) | "not guaranteed" | `drift-doc-wrong` — **fixed in M14** |
| `cli/main.py::_EXIT_CODE_EPILOG` | "not valid JSON" | `drift-doc-wrong` — **fixed in M14** (changed to "not parseable as JSON") |
| ARCHITECTURE.md | (none found) | `aligned` |

**Resolution:** all forbidden words removed from README.md and the CLI epilog.
Automated scan: `tests/docs/test_doc_surface.py::test_no_forbidden_vocabulary_readme` and
`test_no_forbidden_vocabulary_architecture`.

---

### README §4 version pin (pre-M14)

| doc_location | claim | backed_by | status |
|---|---|---|---|
| README §4 (old) | Target ChromaDB listed as `1.5.7` (specific version) | `pyproject.toml`: `chromadb>=1.5.7,<2` | `drift-doc-wrong` — **fixed in M14** (now shows `>=1.5.7,<2` and cites `pyproject.toml`) |

---

### ARCHITECTURE.md (full review)

| doc_location | claim | backed_by | status |
|---|---|---|---|
| ARCHITECTURE §2 | `ANALYZE_PIPELINE = (step_detect, step_extract)` | `core/pipeline.py` | `aligned` |
| ARCHITECTURE §2 | `FULL_PIPELINE` used by `inspect`; reconstruction skipped with no target_path | `core/pipeline.py`, `cli/main.py` | `aligned` |
| ARCHITECTURE §2 | `MIGRATE_PIPELINE` used by `migrate` | `core/pipeline.py`, `cli/main.py` | `aligned` |
| ARCHITECTURE §2 | Only `reconstruction/_writer.py` has module-level chromadb import | `tests/test_reconstruction_purity.py` | `aligned` |
| ARCHITECTURE §2 | `validation` never raises `MigratorError` | `validation/__init__.py` | `aligned` |
| ARCHITECTURE §3 | `MigrationContext` fields listed | `core/context.py` | `aligned` |
| ARCHITECTURE §4 | Pipeline flow diagram | `core/pipeline.py` | `aligned` |
| ARCHITECTURE §5 | Exit-code table (detect→2, extract→3, transform→4, reconstruct→5, report→6, validate→7, success+CRITICAL→8, else→10, success no CRITICAL→0) | `cli/main.py::_EXIT_BY_STAGE` + `_decide_exit_code` | `aligned` |
| ARCHITECTURE §5 | `run_pipeline` always calls `build_report` | `core/pipeline.py::run_pipeline` | `aligned` |
| ARCHITECTURE §5 | No forbidden vocabulary | (scanned) | `aligned` |

**Note on ARCHITECTURE.md §5 "Guarantees" subsection:** the word "guarantee"
(noun, singular) appears in "Silence is never a guarantee." and "correctness claim."
Neither matches the forbidden-word regex `\b(correct|verified|guaranteed|valid)\b`
because "guarantee" ≠ "guaranteed" and "correctness" has no word boundary after
"correct". No change required.

---

## Dropped assertions

Per design doc §7 last paragraph: if any of the five assertions in
`test_doc_surface.py` cannot be written without touching production code, the
assertion is dropped.

**Assertion 5 (CLI help short-help ↔ README first-sentence prefix):** retained.
Implemented as a substring check: each subcommand's full first docstring line is
included verbatim in the corresponding README §7 CLI reference subsection, and the
test asserts that the click `help` first line is present in that section. No
production code change required.

No assertions were dropped.
