# Changelog

All notable changes to this project are recorded here.
The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-04-23

### Added
- Detection of ChromaDB 0.6.x source palaces (M1).
- Resilient extraction with record-level isolation (M2).
- Structured anomaly model with evidence attachment (M3).
- Full-transparency JSON + text reporting (M4, schema_version = 5).
- Non-misleading validation with `checks_not_performed` (M5).
- CLI: `analyze`, `inspect`, `migrate`, `report` (M6).
- Adversarial invariant suite (M7).
- Logging / memory / latency / stability baselines (M8).
- Pure in-memory transformation stage (M9).
- Atomic reconstruction writer (M10), only writer in the pipeline.
- Target-parity validation (M11): record count, id set, document,
  metadata, embedding.
- Write-path adversarial + hardening corpus extension (M12).
- End-to-end migration usability gate (M13).
- Documentation surface parity with implementation (M14).
- CI baseline workflow on pull requests against `main` (M15).
- Version traceability: single source of truth in `pyproject.toml`,
  derived in `__version__` and `report["tool_version"]` (M16).

### Constraints
- Only the source → target pair `chromadb 0.6.3 → 1.5.7` is supported.
- Single-collection palaces only.
- No retrieval / usage / MCP-runtime parity (explicit roadmap non-goals).

[Unreleased]: https://github.com/apajon/mempalace-migrator/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/apajon/mempalace-migrator/releases/tag/v0.1.0
