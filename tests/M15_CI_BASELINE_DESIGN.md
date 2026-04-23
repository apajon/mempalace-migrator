# M15 — CI & Verification Baseline: Implementation Strategy

Status: **design only** (no CI workflow committed yet).
Scope: phase 18 in `TODO.json` (`18.1 .. 18.5`).
Predecessors satisfied: M1–M14 exit gates (see
`ROADMAP.json::current_position` → `M14_done`, doc-surface parity
enforced by `tests/docs/test_doc_surface.py`).
Successor: M16 (Versioning & Release Discipline).

This document fixes the *shape* of M15 work. M15 is an
**infrastructure milestone**. Like M14 it adds:

- **No** new pipeline stage.
- **No** new CLI subcommand, flag, or positional argument.
- **No** new `AnomalyType`, `Severity`, `CheckFamily`, or
  `SkippedReason` value.
- **No** new exit code.
- **No** new production module under `src/mempalace_migrator/`.
- **No** new runtime behaviour. M15 only runs what M1–M14 already
  built, on a machine that is not the author's laptop.

M15 is a **reproducibility proof obligation**: the honesty contract
M1–M14 spent thirteen milestones building must survive the jump from
"passes on my box" to "passes on a clean runner on every push and
pull request, and fails loudly when it doesn't".

---

## 1. M15 in one sentence

> Every assertion M1–M14 made about the code (unit, adversarial,
> hardening, doc-surface) runs automatically on every push and every
> pull request against a fresh environment, with a single, explicit,
> non-zero exit code on any regression; the migrate happy path is
> exercised end-to-end in that same environment; no manual step is
> required between `git push` and a green or red badge.

If, after M15, any of the following is observable on `main`:

- A push or PR lands without CI having been required to pass.
- A CI job passes while any test under `tests/` fails, is skipped
  without an `@pytest.mark.skip` reason already accepted in M1–M14,
  or is collected-but-not-run.
- A CI job passes while `mempalace-migrator --help` exits non-zero,
  crashes on import, or emits forbidden vocabulary (§4).
- A CI job passes while `mempalace-migrator migrate` on the
  M13 e2e fixture fails, mutates the source path, or leaves no
  manifest at `<target>/mempalace-bridge-manifest.json`.
- A CI job passes while `ruff check` or `mypy` (if either is wired in
  §5) reports errors that M15 claims are clean.
- A developer can merge a branch whose CI run is red, by any
  mechanism other than an explicit admin override.
- The CI configuration installs dependencies from a range that no
  longer resolves to `chromadb>=1.5.7,<2` (e.g. silent yanked
  release), without the run going red.

...then **M15 is not done**, regardless of what a badge may show.

---

## 2. Why this milestone exists (honesty contract)

M8 locked a runtime envelope (logging discipline, memory, latency,
stability) as a **recorded baseline** so drift would be *detected*,
not *discovered*. That detection only happens if the baseline is
actually re-run. Today it isn't — it runs when a human remembers.

M14 extended the honesty contract to documentation: README, CLI
help, and report schema must stay in sync with the code. That parity
only holds if the parity tests actually execute on every change.
Today they don't — they run when a human remembers.

M15 closes the loop. It does not invent any new guarantee; it makes
the guarantees the project **already claims** (exit codes, parity
checks, adversarial invariants, hardening baselines, doc-surface
parity) machine-enforced at the single point where code enters the
shared repository.

Equivalently: M15 is the milestone after which "green on main" is a
claim the project is allowed to make. Before M15, `main` can be red
without anyone noticing.

---

## 3. Boundaries (what M15 may NOT touch)

| Touch | Allowed? | Why |
|-------|----------|-----|
| `src/mempalace_migrator/**` | **No** | M15 is infrastructure-only. Any production change is a defect report against the owning phase. |
| `tests/**` behavioural assertions | **No** | Existing tests are the spec CI is running. |
| New adversarial fixtures | **No** | That is M12 / later hardening phases. |
| `pyproject.toml` | **Yes, limited** — only to add an optional `[project.optional-dependencies] ci` group **iff** needed for lint/type jobs, and only if those jobs would otherwise require `pip install` of tools not already in `dev`. No version-pin changes to runtime deps. | CI is allowed to depend on tooling; it may not weaken the runtime contract. |
| `README.md` | **Yes, one badge line** in the header, plus a one-paragraph §11 "CI" block referencing the workflow by path. No other rewrites. | §11 mirrors §10 "Guarantees" from M14: CI is a guarantee about the repo, not about the code. |
| `ROADMAP.json`, `TODO.json` | **Yes**, status updates + design-doc references only | Standard milestone bookkeeping. |
| `.github/workflows/**` | **Yes — the primary deliverable** | New directory; no pre-existing workflows to migrate. |
| Branch-protection rules (GitHub UI) | **Yes**, and required by §9 item 7 | Admin setting; documented in §5.5. |
| New top-level tools (`tox.ini`, `noxfile.py`, `Makefile`) | **No** | Explicit non-goal (§6). CI calls `pytest` and `pip` directly. |
| Docker, container images, matrix across OSes | **No** | Explicit non-goal (§6). Single-runner, single-Python-version baseline. |
| Release publishing, PyPI, tag-triggered workflows | **No** | That is M16. |
| Coverage thresholds, badges, external services | **No** | That is M18 / later. |

---

## 4. Forbidden vocabulary (mirrors M7 / M14)

M14 extended the M7 vocabulary ban to Markdown docs and CLI help
strings. M15 extends it to CI output surfaces the project itself
controls:

- The workflow file `name:` and every job/step `name:` string.
- Any echoed message inside a shell step.
- The README badge alt-text and the §11 CI paragraph.

The canonical list is still
`tests/adversarial/_invariants.py::FORBIDDEN_VOCABULARY`. M14's test
(`tests/docs/test_doc_surface.py`) already scans Markdown. M15 adds
**one** new assertion to the same module (or a sibling file under
`tests/docs/`) that also scans `.github/workflows/*.yml` for the
same vocabulary. No new forbidden words are introduced by M15.

A CI step named `"verify migration"` or `"ensure correctness"` would
be as much of a contract violation as the same phrase in README.

---

## 5. Deliverables (task-level)

Each deliverable maps 1:1 to a `TODO.json` phase-18 task. Every
deliverable is **evidence-based**: it cites the test or command that
will run, not a description of it.

### 5.1 Task 18.1 — `setup_github_actions`

**Output:** one file, `.github/workflows/ci.yml`. Single workflow,
single job on `ubuntu-latest`, single Python version
(`3.12`, matching `pyproject.toml::requires-python = ">=3.12"`). No
matrix. No caching beyond `actions/setup-python`'s built-in pip
cache. No secrets. No external services.

Triggers:

```yaml
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
```

No `schedule:`, no `workflow_dispatch:` in M15. Both are additions
for later milestones when there is a reason for them.

Job skeleton (illustrative, not final):

```yaml
jobs:
  verify:            # (note: the job name is still subject to §4 check)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: python -m pip install --upgrade pip
      - run: pip install -e ".[dev]"
      - run: pytest -q
```

Explicit constraints:

- All actions pinned by **major version tag** (`@v4`, `@v5`) — not by
  commit SHA (scope creep into supply-chain hardening: later
  milestone), not by floating `@main` (unreliable).
- `pip install -e ".[dev]"` is the only install command; it installs
  the exact same runtime pin (`chromadb>=1.5.7,<2`) the developer
  uses.
- `pytest -q` runs the **full** suite. No `-k`, no `--ignore`, no
  `-m "not slow"`. Hardening baselines run in CI; if they are too
  flaky to run in CI, that is a defect against M8, not an excuse
  to skip them in M15.
- Working directory is the repo root. No `cd tests/`. No
  `PYTHONPATH=` hacks.
- No step shall emit forbidden vocabulary (§4).

### 5.2 Task 18.2 — `cli_smoke_test`

**Output:** a dedicated **step** inside `ci.yml` (not a new test
file), after `pytest`, of the form:

```yaml
- run: mempalace-migrator --help
- run: mempalace-migrator analyze --help
- run: mempalace-migrator inspect --help
- run: mempalace-migrator migrate --help
- run: mempalace-migrator report --help
```

Rationale:

- The `pyproject.toml` `[project.scripts]` entry must resolve to an
  importable module on a fresh runner. A broken packaging manifest
  (missing `src/` layout, missing `__init__.py`, typo in the entry
  point) will not be caught by `pytest` if tests import the package
  directly by path. The `--help` invocations catch it.
- Each subcommand is exercised so that a `click` decorator mistake
  that only surfaces on command load (e.g. duplicate flag name) is
  caught.
- `--help` is guaranteed by `click` to exit `0`; CI relies on that
  and no more. If any invocation exits non-zero, CI fails.

M15 does **not** add a pytest wrapper that re-runs `--help` through
`subprocess`. Existing CLI tests already cover behaviour; the CI
step covers packaging surface, which is strictly a runner-side
concern.

### 5.3 Task 18.3 — `migrate_smoke_test`

**Output:** one additional CI step that exercises the M13 end-to-end
happy path in the CI environment:

```yaml
- run: python -m tests.ci_smoke.migrate_smoke
```

Where `tests/ci_smoke/migrate_smoke.py` is a **thin** wrapper
(single file, no new production code, no new fixture) that:

1. Reuses the M13 fixture factory already imported by
   `tests/test_migrate_e2e.py` (e.g. `build_minimal_chroma_06_source`
   — if the helper lives under `tests/` only, M15 exposes it via a
   second import path; it does not duplicate it).
2. Invokes the CLI entry point in-process via
   `click.testing.CliRunner` **or** via `subprocess.run(["mempalace-migrator", "migrate", src, "--target", tgt])`
   — whichever matches the existing M13 style. No new invocation
   idiom is introduced.
3. Asserts:
   - exit code `0`;
   - `<target>/mempalace-bridge-manifest.json` exists and parses as
     JSON;
   - the report's `reconstruction` section is not `None` and the
     `validation.parity` block contains the four required parity
     checks out of `checks_not_performed`.
4. Exits `0` on success, `1` on any assertion failure. Any uncaught
   exception is fatal (no `try/except Exception: pass`).

Strict constraints:

- No network access during this smoke. The fixture is built in a
  `tempfile.TemporaryDirectory()` on the runner, exactly as
  `test_migrate_e2e.py` does today.
- No reliance on a pre-built palace checked into the repo. Any such
  fixture would be a packaging regression against M7/M13.
- No sleeping, no retries. If the migrate path is flaky on CI, the
  defect is filed against M8 / M13, not masked here.
- This step is **additive**. It does not replace the existing
  `pytest` run of `tests/test_migrate_e2e.py` — the pytest run
  verifies invariants; the CI step verifies the installed entry
  point.

If the design ends up duplicating `test_migrate_e2e.py` verbatim,
M15 drops 18.3's wrapper and instead calls the existing test
directly, e.g. `pytest -q tests/test_migrate_e2e.py::test_migrate_e2e_happy_path -x`
as an explicit CI step. This is an acceptable fallback and is
preferred if the alternative would introduce test-logic duplication.

### 5.4 Task 18.4 — `validation_smoke_test`

**Output:** one additional CI step (or assertion inside the 18.3
wrapper, not a second script) that confirms, on the M13 fixture:

- `report["validation"]["parity"]` lists the four parity checks
  (`target_record_count_parity`, `target_id_set_parity`,
  `target_document_parity`, `target_metadata_parity`) with
  `status != "skipped"`.
- `report["validation"]["checks_not_performed"]` does **not**
  contain any of those four ids.
- `report["confidence_summary"]["band"] == "HIGH"` (the M13 fixture
  is the canonical HIGH case).
- No entry in `report["anomalies"]` has `severity == "CRITICAL"`.

This is the single CI assertion that guards the M5.5 / M11 honesty
contract: target-parity is not silently skipped. A regression that
slips a parity check back into `checks_not_performed` without a
`reconstruction_not_run` reason will flip this smoke red.

M15 does **not** reintroduce the forbidden "verified" vocabulary in
the assertion messages. Failure messages use the same wording style
as `tests/test_validation_parity.py`: *"expected 0 `checks_not_performed` parity entries for a reconstructed run; got N: {...}"*.

### 5.5 Task 18.5 — `fail_fast_policy`

**Output:** a combination of workflow configuration and repo
settings:

1. `ci.yml` has no `continue-on-error: true` anywhere. Any step
   failing fails the job.
2. `ci.yml` job uses the default `fail-fast: true` (no strategy
   matrix in M15, so this is trivially true).
3. A single required status check is configured on the `main`
   branch (GitHub branch-protection setting): `verify`. PRs cannot
   be merged with a red or missing `verify` check, except by repo
   admins using the explicit override.
4. The project enforces **no** auto-merge on red. Auto-merge itself
   is out of scope; the rule is that if it is ever enabled (later
   milestone), it must respect the required check.
5. Branch-protection configuration is **documented** in a new
   single-paragraph section of `README.md §11 "CI"` or, preferably,
   in `ARCHITECTURE.md` (whichever already documents contribution
   flow). Documentation is the only artefact of a GitHub UI setting
   that this repository can own.

If branch-protection cannot be set by the current repository role at
milestone time, §5.5 item 3 is **pending**, and M15 is **not done**.
No weakening is accepted. M15 explicitly does not claim "CI is set
up" on the basis of a workflow file alone; a workflow that no one
is required to pass is decoration, not a gate.

---

## 6. Non-goals (explicit)

- **No** multi-OS matrix (Windows, macOS). Ubuntu-latest only.
- **No** multi-Python matrix. `3.12` only; adding `3.13` is a later
  milestone when the supported range grows.
- **No** containerised runner, no `docker build`, no
  reusable-workflow extraction.
- **No** coverage upload, no `codecov`, no third-party badges other
  than the native GitHub Actions status badge.
- **No** security scanning (`dependabot`, `trivy`, CodeQL). Security
  hardening is M17 or later; CI here is for *correctness*
  regression.
- **No** auto-formatting step that rewrites files. `ruff format` is
  permitted only in `--check` mode if it is added at all.
- **No** release workflow, tag triggers, PyPI publish, artefact
  upload. M16 owns that surface.
- **No** scheduled nightly runs. Scheduling is the next hardening
  lever after correctness-on-push is stable.
- **No** `ruff` / `mypy` job *unless* the repository already passes
  them cleanly today. If either currently reports errors, M15 does
  **not** paper over them with `|| true`; it either (a) wires the
  job in a clean state if the errors are zero, or (b) files a
  defect against the owning phase and defers the lint job to that
  phase's repair. M15 does not fix lint errors itself.
- **No** rewriting of any `*_DESIGN.md`. Those are immutable after
  their milestone closes.
- **No** new dependencies unless strictly required by a job M15
  actually wires (ruff/mypy are already in `dev` — no change
  needed).

---

## 7. Testability of an infrastructure milestone

M15 is testable via two orthogonal surfaces:

**7.1 Local tests that describe CI.**

A new test module, `tests/ci/test_workflow_surface.py` (single file,
no production code), asserts:

1. `.github/workflows/ci.yml` exists and parses as YAML.
2. The workflow has exactly one job, triggered on both `push` and
   `pull_request` against `main`.
3. The job's `runs-on` is `ubuntu-latest` and the `python-version`
   is `"3.12"` — both matching `pyproject.toml`'s
   `requires-python` floor (string-equality on the minor, not
   parsed semver; M15 rejects implicit version upgrades via CI).
4. The workflow contains a step that runs `pytest` (substring match
   on the `run:` string, not structural).
5. The workflow contains a step that invokes
   `mempalace-migrator --help` (covers 18.2).
6. The workflow contains a step that runs the migrate smoke entry
   point defined by 18.3 (substring match on its module path or on
   the explicit `tests/test_migrate_e2e.py::...` node id, whichever
   form 18.3 lands).
7. No step uses `continue-on-error: true`.
8. No step name and no `run:` string contains a member of
   `FORBIDDEN_VOCABULARY` (§4).

This module runs inside the same `pytest -q` that CI executes, so
"CI tests CI". If `ci.yml` drifts from these assertions, the CI run
that introduced the drift fails — before the drift can be merged.

**7.2 Actual CI runs.**

The secondary surface — that the workflow *actually executes* as
designed — is only verifiable by running it. M15's exit gate (§9)
therefore requires that at least one successful CI run has occurred
on `main` after the workflow lands, **and** at least one CI run has
been observed to go red on a deliberate regression (a throwaway
branch with a broken test, discarded after demonstration).

M15 does **not** automate that red-path proof. The proof is recorded
as a caveat in §11 of this doc at milestone close, with a link to
the (failed) CI run. This is analogous to M8's hardening baseline:
the recording is the artefact.

---

## 8. Failure modes M15 must not introduce

- **False-green via skipped steps.** Any `continue-on-error`,
  `if: always()` guarding a failing step, or `|| true` is a contract
  violation. Caught by §7.1 item 7 and by manual review.
- **False-green via wrong Python version.** A runner on 3.13 that
  silently passes when the local developer is on 3.12 hides version
  drift. Caught by §7.1 item 3.
- **Passing CI, broken packaging.** A test suite that imports by path
  instead of by installed entry point will go green even if the
  `pyproject.toml` entry point is broken. Caught by §5.2 CLI smoke
  and by §5.3 migrate smoke (which invokes via the installed
  console script when that idiom is used).
- **Network dependency.** Any step that silently downloads at
  runtime beyond `pip install` creates flakiness that will be
  mistaken for correctness regressions. Pip's own resolver is
  allowed; anything else is not. No `curl`, `wget`, `git clone` of
  external repos.
- **Flakiness-driven retries.** M15 does **not** wire
  `nick-fields/retry@...` or any retry action. A flaky test is a
  defect against the owning milestone.
- **CI drift from developer environment.** Addressed by using only
  `pip install -e ".[dev]"` — no separate `requirements-ci.txt`, no
  `pip install pytest==...` version pinning inside the workflow.
- **Leaking secrets.** M15 introduces no secret. `${{ secrets.* }}`
  must not appear in `ci.yml`. Asserted informally by review; if
  §7.1 grows to include it, it is a substring scan on the YAML
  text.
- **Documentation drift vs workflow.** README §11 references
  `ci.yml` by path; if that path is moved, §7.1 item 1 fails first.
- **Scope creep into M16.** Any step that publishes, tags, uploads
  artefacts, writes to a registry, or posts to an external service
  is rejected.

---

## 9. Exit-gate checklist (what "M15 done" means)

M15 is done **only** when every one of the following holds on `main`:

1. `tests/M15_CI_BASELINE_DESIGN.md` committed (this file).
2. `TODO.json` phase 18 references this design doc in a `design_doc`
   field mirroring M10–M14 convention.
3. `.github/workflows/ci.yml` committed, triggered on push and pull
   request against `main`, running the full `pytest -q` on
   Ubuntu-latest with Python 3.12 against `pip install -e ".[dev]"`.
4. CLI smoke (§5.2): every subcommand's `--help` returns `0` inside
   the same CI job.
5. Migrate smoke (§5.3): M13 happy path succeeds inside the same
   CI job, manifest present, `reconstruction` section populated.
6. Validation smoke (§5.4): the four parity checks are present and
   not skipped, `confidence_summary.band == "HIGH"`, no CRITICAL
   anomaly — asserted inside the smoke script or an existing e2e
   test invoked by CI.
7. Branch protection on `main` requires the `verify` check to pass
   (§5.5 item 3). Confirmed by the admin who applied it; absence of
   this setting blocks M15.
8. `tests/ci/test_workflow_surface.py` landed with the eight
   assertions of §7.1, all green under the local `pytest -q` run.
9. At least one successful CI run is recorded on `main` after the
   workflow lands (run URL noted in §11 of this doc at milestone
   close).
10. At least one deliberately-broken branch has been observed going
    red under the same workflow (run URL noted in §11). The red-path
    proof is required because "green-on-green" alone cannot
    distinguish a real gate from a no-op workflow.
11. README gains a single §11 "CI" paragraph + one status badge
    line. No other README content changes by M15.
12. `ROADMAP.json::current_position.milestone` advanced to
    `M15_done` **only after** 1–11 are all true.
13. No forbidden vocabulary in `ci.yml`, `README §11`, or any step
    name. Asserted by §7.1 item 8 and re-run by the existing
    `tests/docs/test_doc_surface.py` scan if it is extended to
    workflow files; if not extended, the assertion lives in §7.1
    only.
14. `git diff` on `src/mempalace_migrator/**` is empty across the
    entire M15 delta. No production code change.

If any of 1–14 is false, M15 is **not** done, regardless of how
green the badge looks.

---

## 10. What M15 explicitly does NOT prove

- M15 does **not** prove the code is correct. It only proves the
  tests M1–M14 wrote are actually executed on every change, on a
  machine the developer does not control.
- M15 does **not** prove the test suite is complete. Coverage is
  not measured. Gaps present on M14 close remain gaps.
- M15 does **not** prove reproducibility across OSes, Python
  versions, or architectures. Single Ubuntu / single Python 3.12
  only.
- M15 does **not** prove supply-chain integrity. Actions are
  version-tagged, not SHA-pinned. Dependency updates that yank /
  re-publish under the same version are not caught.
- M15 does **not** prove the CI environment is secret-free beyond
  what review catches; `secrets.*` is simply absent from the
  workflow.
- M15 does **not** provide artefact distribution. No wheel is
  uploaded. That is M16 / M19.
- M15 does **not** lock CI for future phases. M16–M19 may add
  workflows; they must each justify their job and pass §7.1's
  invariants (or extend §7.1 consistently).

---

## 11. Caveats that will carry forward past M15

(Populated at milestone close. Expected entries:

- `verify` run URL demonstrating the first successful CI run on
  `main` (§9 item 9).
- `verify` run URL demonstrating the red-path proof on a
  deliberately-broken throwaway branch (§9 item 10).
- Whether branch protection on `main` was applied by an admin with
  the authority to do so, or is pending (§9 item 7). If pending, M15
  is **not** done — this caveat cannot be used to mark M15 complete.
- Any pre-existing test that is flaky under CI but green locally;
  tagged as a defect against the owning milestone (M8 for hardening,
  M12 for adversarial, M13 for e2e), **not** silenced in M15.
- Whether `ruff` / `mypy` were wired as CI jobs; if not, explicit
  note that M15 deliberately did not fix pre-existing lint/type
  errors and deferred them.

These are **documented**, not **fixed**, by M15.)

Recorded evidence as of 2026-04-23:

- Successful CI run URL (PR #1):
  https://github.com/apajon/mempalace-migrator/actions/runs/24847898031/job/72739999009?pr=1
- Branch protection on `main` is active with required status checks and
  `Require a pull request before merging` enabled.
- Red-path (deliberate regression) CI run URL (PR #2, closed without merging):
  https://github.com/apajon/mempalace-migrator/actions/runs/24848463041/job/72742034403
  The `verify` job failed on `test_deliberate_regression` as expected,
  confirming the gate is real. PR #2 was closed without merging.
- `ruff` / `mypy` CI jobs were not wired in M15. Pre-existing lint/type errors
  are not fixed by this milestone; repair is deferred to the owning phase.

All §9 items 1–14 satisfied. M15 is done.
