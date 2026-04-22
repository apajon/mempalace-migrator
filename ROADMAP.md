# Roadmap

## Current Position

- **Milestone:** M13_done
- **Description:** End-to-end migration usability validated — all 7 M13 tasks green, 844 tests passing
- **Confidence:** internal only — not publicly proven

## Rules

- Do not skip milestones
- Do not parallelize critical phases early
- Do not optimize before correctness
- Always prefer explicit over implicit behavior
- Surface uncertainty at every stage

## Completed Milestones

### M1 — Detection Reliability (phase 4)

**Goal:** Produce non-guessing, evidence-based format detection

**Exit gate:** Detection outputs are explainable and never misleading

### M2 — Extraction Resilience (phase 5)

**Goal:** Extract maximum usable data from corrupted inputs

**Exit gate:** Extraction never crashes on recoverable errors

### M3 — Truth Model / Anomalies (phase 6)

**Goal:** Define structured truth reporting system

**Exit gate:** All inconsistencies are structurally represented

### M4 — Full Transparency / Reporting (phase 7)

**Goal:** Expose complete system state to the user

**Exit gate:** User can fully understand what happened

### M5 — Safe Interpretation / Validation (phase 8)

**Goal:** Avoid false correctness claims

**Exit gate:** Validation never implies correctness

### M6 — User Access / CLI (phase 9)

**Goal:** Make the tool usable externally

**Exit gate:** User can run end-to-end pipeline

### M7 — System Destruction / Adversarial Testing (phase 10)

**Goal:** Break the system to reveal hidden flaws

**Exit gate:** System fails loudly and clearly

### M8 — Production Credibility / Hardening (phase 11)

**Goal:** Stabilize system behavior

**Exit gate:** System is stable under stress

### M9 — Transformation Stage (phase 12)

**Goal:** Normalise extracted DrawerRecords into a typed bundle ready for ChromaDB 1.x ingestion. Pure function: no I/O, no writes, no chromadb import.

**Exit gate:** `step_transform` produces a `TransformedBundle` deterministically; every drop or coercion is structurally represented as an anomaly; no writer dependency leaks into the transformation module.

### M10 — Reconstruction Stage (phase 13)

**Goal:** Build a fresh ChromaDB 1.5.7 palace at `ctx.target_path` from `ctx.transformed_data` via the public chromadb client API. Strictly atomic.

**Exit gate:** A fresh ChromaDB 1.5.7 palace is built atomically at `--target`; the source is byte-identical before and after the run; any failure leaves no partial target on disk.

### M11 — Target Parity Validation (phase 14)

**Goal:** Lift target parity checks out of `checks_not_performed` and add document/metadata/embedding parity checks. Validation never opens the target in write mode.

**Exit gate:** For every successful migrate run, the target palace is structurally and content-wise identical to the transformed bundle, and any divergence is surfaced as a structured failed/inconclusive validation check.

### M12 — Write-Path Adversarial + Hardening (phase 15)

**Goal:** Extend the M7 adversarial corpus and the M8 baseline envelope to cover the new transform/reconstruct/parity stages.

**Exit gate:** The full M7 invariant battery still holds across the extended corpus, the M8 baseline includes a migrate-success entry, and the write path fails loudly and atomically on every adversarial fixture.

### M13 — End-to-End Migration Usability Gate (phase 16)

**Goal:** Prove that the migrator is actually usable as a complete product on the supported version pair.

**Exit gate:** The migrator is only considered end-to-end usable when a real `migrate` command succeeds on the supported fixture, the reconstructed target is reopenable and parity-checked, the source remains untouched, and the final report contains no hidden reconstruction/parity gap.

---

## Upcoming Milestones

### M14 — Truth Alignment & Public Surface

**Goal:** Align documentation with actual implementation state

**Tasks:**
- audit_doc_vs_code
- rewrite_readme
- document_cli_surface
- document_guarantees
- document_limitations

**Exit criteria:**
- README contains no false or outdated claims
- `migrate` command is clearly documented and usable
- Workflow E2E is understandable without reading code
- Scope and limitations are explicit and strict

---

### M15 — CI & Verification Baseline

**Goal:** Make repository state automatically verifiable

**Tasks:**
- setup_github_actions_ci
- add_lint_and_test_jobs
- cli_smoke_test
- migrate_smoke_test
- validation_smoke_test
- fail_fast_policy

**Exit criteria:**
- CI runs on push and PR
- CI fails on test or CLI regression
- Basic migrate flow executes successfully in CI
- No manual steps required to validate core pipeline

---

### M16 — Versioning & Release Discipline

**Goal:** Introduce traceability and reproducible releases

**Tasks:**
- introduce_semver
- define_versioning_rules
- add_changelog
- create_initial_tag
- publish_github_release
- add_release_badge

**Exit criteria:**
- Versioning scheme documented
- First release tagged (e.g. `v0.1.0`)
- Changelog present and structured
- Release visible and usable externally

---

### M17 — Trust & Safety Hardening

**Goal:** Prove migration reliability under edge conditions

**Tasks:**
- test_corrupted_inputs
- test_missing_fields
- test_inconsistent_metadata
- test_partial_migration_failure
- verify_rollback_behavior
- idempotence_check
- anomaly_completeness_check
- validation_false_positive_check

**Exit criteria:**
- No silent success on corrupted inputs
- Rollback works reliably
- Re-running migrate does not corrupt output
- Validation does not falsely report success

---

### M18 — UX CLI & Developer Experience

**Goal:** Make tool usable without internal knowledge

**Tasks:**
- improve_cli_help
- improve_error_messages
- standardize_logging
- add_example_workflow_doc
- add_sample_dataset

**Exit criteria:**
- CLI usable without reading source code
- Error messages actionable
- Example workflow documented
- User can run full migration from example

---

### M19 — Packaging & Distribution

**Goal:** Make tool installable and distributable

**Tasks:**
- define_python_package
- add_entry_point_cli
- clean_dependencies
- test_installation_flow

**Exit criteria:**
- Tool installable via `pip` or `uv`
- CLI works after install
- No environment-specific hacks required

---

## Non-Goals (explicit out-of-scope)

- Retrieval-parity validation — belongs to a separate post-migration audit tool
- Usage-scenario-parity validation — belongs to a separate post-migration audit tool
- MCP-runtime validation — the migrator does not own the runtime
- Embedding re-computation or embedding-shape validation
- Multi-collection palaces — single-collection invariant enforced
- Migration of palaces other than `chroma_0_6`
