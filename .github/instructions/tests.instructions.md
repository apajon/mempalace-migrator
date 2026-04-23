---
description: "Use when writing or modifying tests in tests/, including adversarial (M7), hardening (M8), reconstruction, transformation, parity, CLI, and CI surface tests. Covers the corpus-driven pattern, subprocess CLI runner, exit-code contract, and baseline management."
applyTo: "tests/**"
---

# Test Conventions — mempalace-migrator

The suite is organized by milestone (M7 adversarial, M8 hardening, M9
transformation, M10 reconstruction, M11 parity, …). Respect the existing
structure; do not reshuffle directories.

## Runner & Invocation

- Framework: `pytest` (8+), optional `pytest-xdist`.
- CLI tests **must use the subprocess runner** `run_cli()` from
  `tests/adversarial/conftest.py`. Do **not** use Click's `CliRunner` — it
  mixes stdout/stderr on Click ≥ 8.2 and breaks the JSON-report contract.
- Assert on `CliResult.returncode`, parse reports with
  `CliResult.parse_report()`.

## Exit Codes

Use the `EXIT_*` constants from `tests/adversarial/conftest.py` (mirror of
`mempalace_migrator.cli.main`). Never hardcode integers. The contract:

| Code | Meaning                |
|------|------------------------|
| 0    | OK                     |
| 1    | Usage error            |
| 2    | Detection failed       |
| 3    | Extraction failed      |
| 4    | Transform failed       |
| 5    | Reconstruct failed     |
| 6    | Report failed          |
| 7    | Validate failed        |
| 8    | Critical anomaly       |
| 9    | Report file error      |
| 10   | Unexpected (forbidden) |

Exit 10 is a bug signal — an adversarial/hardening test must never accept
it unless the `CorpusEntry` explicitly lists it in `allowed_exit_codes`.

## Adversarial Corpus (M7)

- All adversarial inputs live in the `CORPUS` registry in
  `tests/adversarial/conftest.py`. Every adversarial test parametrises over
  `CORPUS` (or a filtered subset).
- To add a new adversarial case: append a `CorpusEntry` with a stable
  `cid`, a pure `builder(tmp_path)`, the target `pipeline`, and an explicit
  `allowed_exit_codes` set. Do not generate adversarial bytes anywhere
  else.
- The invariants file `tests/adversarial/_invariants.py` /
  `test_adversarial_invariants.py` must see the new entry automatically —
  verify by running the invariants test after adding an entry.

## Hardening Baselines (M8)

- Baselines live in `tests/hardening/baselines/` and are consumed by
  `test_performance_baseline.py`, `test_memory_envelope.py`,
  `test_stability_invariants.py`.
- The `BASELINE_CORPUS` is derived: entries whose `allowed_exit_codes ⊆
  {EXIT_OK, EXIT_CRITICAL_ANOMALY}`. Do not hand-maintain it.
- To refresh baselines, use `tests/hardening/rebaseline.py`. Never edit
  the JSON baseline files by hand.
- Report signatures exclude volatile fields (`run_id`, `started_at`,
  `completed_at`, absolute paths, chromadb version, timings). Preserve
  this redaction list in `extract_report_signature`.

## Determinism & Isolation

- Tests must be deterministic: no network, no wall-clock assertions, no
  reliance on dict ordering beyond Python's guarantees.
- Use `tmp_path` for all file I/O. Never write under the repo root.
- Mark slow tests with `@pytest.mark.slow` (registered in `pyproject.toml`).

## What NOT to Do

- Do not introduce `CliRunner`, `capsys`-based CLI assertions, or
  `subprocess.run([...])` duplicates of `run_cli`.
- Do not add adversarial fixtures outside the `CORPUS` registry.
- Do not hardcode exit-code integers.
- Do not delete or silence an adversarial/hardening test to make CI green —
  fix the underlying invariant or escalate.
