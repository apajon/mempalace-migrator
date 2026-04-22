# TODO

_Last updated: 2026-04-22 — M13 done, 844 tests green_

---

## Completed Phases (M0–M13)

### Phase 0 — Positioning `done`

- [x] Separate repos
- [x] Define experimental positioning
- [x] Remove misleading claims

### Phase 1 — README `partial`

- [x] Write honest positioning
- [x] Add explicit warnings
- [ ] Add real output examples

### Phase 2 — Architecture `done`

- [x] Define pipeline stages
- [x] Implement MigrationContext
- [x] Module separation

### Phase 3 — Foundation Code `done`

- [x] Project structure
- [x] Initial modules
- [x] Context base implementation

### Phase 4 — Detection / M1 `done`

- [x] Multi-signal detection
- [x] Evidence collection
- [x] Confidence scoring
- [x] Contradiction handling
- [x] UNKNOWN format handling
- [x] No silent fallback

### Phase 5 — Extraction / M2 `done`

- [x] Partial read support
- [x] Invalid JSON tolerance
- [x] Corrupted SQLite handling
- [x] Record-level isolation
- [x] No global crash
- [x] Structured anomalies output

### Phase 6 — Anomaly Model / M3 `done`

- [x] Define anomaly enum types
- [x] Define severity levels
- [x] Define location schema
- [x] Attach evidence model
- [x] Remove unstructured logs

### Phase 7 — Reporting / M4 `done`

- [x] Global stats
- [x] Anomaly aggregation
- [x] Confidence summary
- [x] Human-readable summary
- [x] Structured JSON output

### Phase 8 — Validation / M5 `done`

- [x] Structural validation
- [x] Internal consistency checks
- [x] Heuristic plausibility
- [x] Confidence-based output
- [x] Avoid 'correctness' claims

### Phase 9 — CLI / M6 `done`

- [x] Input handling
- [x] Output display
- [x] Execution modes (`analyze`, `inspect`, `report`)
- [x] Exit-code policy (`_decide_exit_code`)

### Phase 10 — Adversarial Testing / M7 `done`

- [x] Corrupted JSON / blob cases
- [x] Broken SQLite cases (pre-flight + mid-scan)
- [x] Mixed-format / contradictory-signal inputs
- [x] Inconsistent data (duplicates / cross-set membership)
- [x] Extreme edge cases (empty, 0-byte, manifest-only)
- [x] Cross-cutting invariants over full adversarial corpus

### Phase 11 — Final Hardening / M8 `done`

- [x] Logging discipline (structural ban via AST walk)
- [x] Performance baseline (drift detector)
- [x] Memory envelope (peak RSS)
- [x] Stability invariants

### Phase 12 — Transformation Stage / M9 `done`

- [x] `TransformedBundle` dataclass
- [x] Metadata normalisation (`normalize_metadata`)
- [x] Drawer integrity analysis (`analyze_drawers`)
- [x] `transform(ctx)` entry point
- [x] Pipeline wiring (`step_transform`)
- [x] `AnomalyType` registration
- [x] Report section
- [x] Purity contract test (no chromadb import)

### Phase 13 — Reconstruction Stage / M10 `done`

- [x] chromadb dependency (`>=1.5.7,<2`)
- [x] Target safety check (`ensure_target_is_safe`)
- [x] `ReconstructionResult` dataclass
- [x] Atomic batch insert
- [x] Target manifest writer
- [x] Pipeline wiring (`step_reconstruct`, `MIGRATE_PIPELINE`)
- [x] `AnomalyType` registration
- [x] CLI `migrate SOURCE --target TARGET` subcommand
- [x] Report section
- [x] Source-mtime invariant test
- [x] Atomicity test (mid-batch failure injection)

### Phase 14 — Target Parity Validation / M11 `done`

- [x] `AnomalyType` registration (target parity)
- [x] Extend `CheckFamily` and `SkippedReason` literals
- [x] `validation/parity.py` — read-only target reader
- [x] `target_record_count_parity` (lifted)
- [x] `target_id_set_parity` (lifted)
- [x] `target_document_hash_parity` (new)
- [x] `target_metadata_parity` (new)
- [x] `target_embedding_presence` (new, best-effort)
- [x] Wire `validate()` to call `run_parity_checks`
- [x] Shrink `EXPLICITLY_NOT_CHECKED`
- [x] Tests: parity checks happy/sad paths
- [x] Tests: AST allowlist for chromadb imports
- [x] Tests: target mtime invariant
- [x] Tests: validation skip-list shape and report integration

### Phase 15 — Write-Path Adversarial + Hardening / M12 `done`

- [x] Adversarial: target safety
- [x] Adversarial: mid-batch chromadb failure
- [x] Adversarial: transformation rejects-all
- [x] M7 invariants on extended corpus
- [x] Baseline: migrate-success entry
- [x] Stability: report-signature on migrate
- [x] Logging discipline on new modules
- [x] Atomic rollback guarantee re-assertion
- [x] Batch-size stress
- [x] Duplicate-ID adversarial ingestion failure
- [x] Insert-count and read-back verification

### Phase 16 — End-to-End Migration Usability Gate / M13 `done`

- [x] Successful migrate command contract
- [x] No skipped parity checks after successful migrate
- [x] Confidence and honesty gate
- [x] Repeatability on fresh targets
- [x] End-to-end source invariance
- [x] Readable target smoke test
- [x] Current-position promotion rule (16.7 self-consistency)

---

## Upcoming Phases (M14–M18)

### M14 — Truth Alignment & Public Surface

- [ ] audit_doc_vs_code
- [ ] rewrite_readme
- [ ] document_cli_surface
- [ ] document_guarantees
- [ ] document_limitations

### M15 — CI & Verification Baseline

- [ ] setup_github_actions
- [ ] cli_smoke_test
- [ ] migrate_smoke_test
- [ ] validation_smoke_test
- [ ] fail_fast_policy

### M16 — Versioning & Release Discipline

- [ ] introduce_semver
- [ ] initial_version
- [ ] add_changelog
- [ ] create_release
- [ ] add_release_badge

### M17 — Trust & Safety Hardening

- [ ] adversarial_inputs
- [ ] partial_migration_safety
- [ ] idempotence_check
- [ ] anomaly_completeness
- [ ] validation_trust

### M18 — UX CLI & Developer Experience

- [ ] cli_help_cleanup
- [ ] error_messages
- [ ] logging_cleanup
- [ ] example_workflow
- [ ] sample_dataset

