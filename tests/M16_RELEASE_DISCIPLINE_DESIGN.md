# M16 — Versioning & Release Discipline: Implementation Strategy

Status: **design only** (no implementation yet as of 2026-04-23).
Scope: phase 19 in `TODO.json` (`19.1 .. 19.5`).
Predecessors satisfied: M1–M15 exit gates (see
`ROADMAP.json::current_position` → `M15_done`, CI baseline enforced by
`.github/workflows/ci.yml` and `tests/ci/test_workflow_surface.py`).
Successor: M17 (Trust & Safety Hardening).

This document fixes the *shape* of M16 work. Like M14 and M15, M16 is
an **infrastructure / discipline milestone**. It adds:

- **No** new pipeline stage.
- **No** new CLI subcommand, flag, or positional argument.
- **No** new `AnomalyType`, `Severity`, `CheckFamily`, or
  `SkippedReason` value.
- **No** new exit code.
- **No** change to the report schema. `schema_version` stays at `5`.
- **No** change to runtime behaviour of `analyze` / `inspect` /
  `migrate`.

M16 is a **traceability proof obligation**: every artefact the project
emits (report JSON `tool_version`, `--version` string, wheel metadata,
git tag, GitHub Release page, changelog entry) must point to the same
single version string, derived from one source of truth, and any
published release must be reproducible from the tag it claims to be.

---

## 1. M16 in one sentence

> For every version the project claims in any artefact — `pyproject.toml`,
> `mempalace_migrator.__version__`, `report["tool_version"]`,
> `mempalace-migrator --version` (if added), the git tag, the GitHub
> Release title, and the `CHANGELOG.md` entry — the same value must
> appear, be derivable from one source, be tied to an immutable commit,
> and be auditable by a downstream consumer who only has the installed
> wheel.

If, after M16, any of the following is observable on `main`:

- Two distinct version strings can be read from the same installed
  artefact (e.g. `pip show mempalace-migrator` shows `0.1.0` while
  `report["tool_version"]` shows `0.2.0`).
- A git tag `vX.Y.Z` exists on `main` for which no `CHANGELOG.md`
  entry is present, or for which the entry contains forbidden
  vocabulary (§4).
- A `CHANGELOG.md` entry claims a version that is not tagged.
- The version-derivation test added in §7.1 is absent, green under
  drift, or guarded by a skip marker.
- A release is published via the GitHub UI without an attached git
  tag pointing to a commit whose `pyproject.toml::version` equals
  the release title.
- The README badge in §5.5 references a non-existent release, or
  its URL is not pinned to `v*` tag events.
- The repository claims "first release" while the migrate happy-path
  smoke (M15 §5.3) is not green at the tagged commit.

…then **M16 is not done**, regardless of what a badge or Releases page
may show.

---

## 2. Why this milestone exists (honesty contract)

M14 aligned documentation with implementation. M15 made the test
battery machine-enforced on every PR. Both milestones assume the
reader can identify *which version* of the code the documentation and
test results belong to. Today, that assumption is violated:

- `pyproject.toml` declares `version = "0.1.0"`.
- `src/mempalace_migrator/__init__.py` hard-codes
  `__version__ = "0.1.0"`.
- `src/mempalace_migrator/reporting/report_builder.py` hard-codes
  `TOOL_VERSION = "0.1.0"`.
- No git tag exists. No GitHub Release exists.
- No `CHANGELOG.md` exists.

A downstream consumer opening a migration report has no way to bind
`tool_version = "0.1.0"` to a specific commit, a specific test-suite
state, or a specific chromadb pin range. The string is decoration
until M16 makes it traceable.

M16 does **not** invent a new guarantee. It binds the guarantees M1–M15
already claim to a **commit-addressable artefact** (a tag), so that
the claim "this report was produced by the code that passed the M15 CI
baseline at tag `v0.1.0`" can be verified by anyone.

Equivalently: M16 is the milestone after which a bug report against
`tool_version = 0.1.0` is actionable without first asking the reporter
"which commit did you build from?".

---

## 3. Boundaries (what M16 may / may NOT touch)

| Touch | Allowed? | Why |
|-------|----------|-----|
| `pyproject.toml::[project].version` | **Yes** — this is the single source of truth M16 establishes | Required by §5.1. |
| `src/mempalace_migrator/__init__.py` | **Yes, narrowly** — replace the hard-coded literal with a derivation from package metadata (see §5.1). This is a defect fix discovered by M16 planning; the current triple-source is drift-prone and already blocks the M16 traceability claim. | The fix is strictly local, touches one line of logic, and is covered by the new test in §7.1. It adds no runtime behaviour. |
| `src/mempalace_migrator/reporting/report_builder.py::TOOL_VERSION` | **Yes, narrowly** — same derivation pattern; `TOOL_VERSION` becomes a module-level constant initialised from `mempalace_migrator.__version__` (or equivalently from `importlib.metadata.version("mempalace-migrator")`). | Same justification. `report["tool_version"]` must track the installed wheel, not a literal that a release engineer might forget to bump. |
| Any other file in `src/mempalace_migrator/**` | **No** | M16 is discipline-only beyond the version-derivation fix above. Any other production change is a defect report against the owning phase, not an M16 deliverable. |
| `tests/**` behavioural assertions | **No** | M1–M15 tests are the spec. M16 only adds a single new test module under `tests/release/` (or co-located `tests/test_version_consistency.py`). |
| New adversarial fixtures, hardening baselines | **No** | That is M12 / later. |
| `README.md` | **Yes** — one new badge line (§5.5) and one paragraph §12 "Releases" cross-referencing `CHANGELOG.md`. No other rewrites; the forbidden-vocabulary scan (M14) still applies. |
| `CHANGELOG.md` | **Yes — the primary documentation deliverable** | New file at repo root, "Keep a Changelog 1.1.0" format, semver-linked. |
| `ROADMAP.json`, `TODO.json` | **Yes**, status updates + `design_doc` reference only | Standard milestone bookkeeping. |
| `.github/workflows/**` | **Optional, minimal** — at most one new `release.yml` that triggers on `push: tags: ['v*']` to build the wheel, attach it to the GitHub Release, and verify tag↔version consistency. If omitted, the release is published manually via the GitHub UI with a documented checklist (§5.4). **Either path is acceptable**; both must satisfy §9. | Automating release is a quality-of-life improvement, not a correctness requirement. M16 is done either way, provided §9 holds. |
| Git tags, GitHub Releases | **Yes, and required by §9** | Admin / maintainer operation, documented in §5.4. |
| PyPI publishing | **No** (deferred) | `M19 — Packaging & Distribution` owns PyPI. M16's scope is explicitly internal traceability + GitHub Release. §6 restates this. |
| Signed tags / SBOMs / provenance attestations | **No** | Supply-chain hardening is later. M16 uses lightweight annotated tags only. |
| Version bumping automation (`bump2version`, `release-please`, `commitizen`) | **No** | Explicit non-goal (§6). `pyproject.toml` is the source; bumps are manual and reviewed. |

---

## 4. Forbidden vocabulary (mirrors M7 / M14 / M15)

M14 extended the M7 vocabulary ban to Markdown docs and CLI help
strings. M15 extended it to CI workflow step names and echoed
messages. M16 extends it to **every new surface it introduces**:

- `CHANGELOG.md` entries (every `Added` / `Changed` / `Fixed` bullet).
- The GitHub Release title and body.
- The annotated-tag message.
- Any `release.yml` step name, echoed string, or release-notes
  template.
- The §12 "Releases" paragraph in `README.md`.
- The badge alt-text in §5.5.

The canonical list remains
`tests/adversarial/_invariants.py::FORBIDDEN_VOCABULARY`. M14's test
(`tests/docs/test_doc_surface.py`) already scans Markdown files under
the repo. M16 relies on that test — it does **not** add a second scan
— but the M16 test module (§7.1) explicitly asserts
`CHANGELOG.md` is picked up by the existing Markdown-scan glob. If it
is not, M16 patches the glob (a one-line test change, not a new
assertion family).

A `CHANGELOG.md` line of the form `- Fixed: parity validation now
correctly verifies target integrity` would be as much of a contract
violation as the same phrase in README.

---

## 5. Deliverables (task-level)

Each deliverable maps 1:1 to a `TODO.json` phase-19 task. Every
deliverable is **evidence-based**: it cites the test, file path, or
command that will run, not a description of it.

### 5.1 Task 19.1 — `introduce_semver` + single source of truth

**Output:** a documented versioning policy and a single authoritative
derivation path.

Policy (to be captured in `README.md §12` and `CHANGELOG.md` header):

- The project follows **Semantic Versioning 2.0.0**.
- The **public contract** for semver purposes is:
  - The CLI surface of `mempalace-migrator` (subcommands, flags,
    exit codes listed in `README.md §5`).
  - The report schema (`schema_version`, `REPORT_TOP_LEVEL_KEYS`).
  - The supported source/target version pair
    (`chromadb 0.6.3 → 1.5.7`).
- Any change to any of the three above is **MAJOR**. The `0.x.y`
  pre-1.0 convention applies: **MINOR** may break the public
  contract while the project is still at `0.x`. This is documented
  explicitly; no-one should mistake `0.2.0 → 0.3.0` for a safe bump.
- `schema_version` (report field) is **orthogonal** to the tool
  version. A `tool_version` bump does not require a
  `schema_version` bump and vice versa. Both are exposed in the
  report; both are traceable.
- The chromadb pin range (`>=1.5.7,<2`) is not versioned by M16;
  widening or tightening it is a public-contract change and
  requires an M16-style bump.

Source-of-truth derivation:

- `pyproject.toml::[project].version` is the **only** place a
  version literal is written.
- `src/mempalace_migrator/__init__.py::__version__` is derived:

  ```python
  from importlib.metadata import PackageNotFoundError, version

  try:
      __version__ = version("mempalace-migrator")
  except PackageNotFoundError:  # source checkout without install
      __version__ = "0.0.0+unknown"
  ```

  The fallback string `"0.0.0+unknown"` is **deliberately** outside
  any semver range the project will ever publish; a report showing
  it is a loud signal that the tool was not installed from a built
  wheel. No anomaly is emitted for this case — it is a developer
  diagnostic, not a user-facing condition.
- `reporting/report_builder.py::TOOL_VERSION` becomes:

  ```python
  from mempalace_migrator import __version__ as TOOL_VERSION
  ```

  i.e. a single re-export, not a second literal.
- `reconstruction/reconstructor.py` already reads
  `TOOL_VERSION` via `from mempalace_migrator.reporting.report_builder import TOOL_VERSION`;
  no change required there — the derivation propagates.

**Explicitly NOT included in 19.1:** adding a
`mempalace-migrator --version` flag. That is a CLI surface change and
would belong to M18 (UX CLI). If a consumer needs the version, the
report already carries it, and `pip show mempalace-migrator` already
prints it.

### 5.2 Task 19.2 — `initial_version`

**Output:** `pyproject.toml::version = "0.1.0"` remains, and the
derivation fix in §5.1 lands **in the same commit** so that the three
read-points become internally consistent at `0.1.0`.

Rationale for `v0.1.0` (not `v0.0.1`, not `v1.0.0`):

- The codebase already advertises `0.1.0` in three places. Renaming
  to `v0.0.1` would rewrite history and lose the connection to
  the M1–M15 audit trail.
- `v1.0.0` would falsely imply API stability. The public contract
  is not frozen: M17–M19 may yet change it. `0.x` honestly advertises
  pre-stability.
- No prior tag exists, so `v0.1.0` is the first commit-addressable
  version the project has ever had. It is not a "retroactive"
  version — it is the version the code has been claiming since M3.

**Explicitly NOT included in 19.2:** back-dating tags to earlier
milestones. There will be exactly one tag created by M16: `v0.1.0` at
the commit that closes M16.

### 5.3 Task 19.3 — `add_changelog`

**Output:** one new file, `CHANGELOG.md`, at repo root, following
**Keep a Changelog 1.1.0** structure.

Required sections:

```markdown
# Changelog

All notable changes to this project are recorded here.
The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — YYYY-MM-DD

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

### Constraints
- Only the source → target pair
  `chromadb 0.6.3 → 1.5.7` is supported.
- Single-collection palaces only.
- No retrieval / usage / MCP-runtime parity (explicit roadmap
  non-goals).

[Unreleased]: https://github.com/apajon/mempalace-migrator/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/apajon/mempalace-migrator/releases/tag/v0.1.0
```

Strict rules for every entry (now and future):

- Every heading `## [X.Y.Z]` must correspond 1:1 to an existing git
  tag `vX.Y.Z`. The test in §7.1 asserts this.
- Every `## [X.Y.Z]` must carry an ISO-8601 release date.
- Bullet lines must not contain forbidden vocabulary (§4).
- The `[Unreleased]` section exists and may be empty between
  releases; this is normal and not an error.
- Link references at the bottom use the `compare/vA...vB` and
  `releases/tag/vX.Y.Z` URL shapes so a reader can diff between
  released versions.

**Explicitly NOT included in 19.3:**

- Per-PR or per-commit changelog entries. A changelog is a human
  summary, not a git-log dump.
- Automated generation from commit messages (would require a
  commit-message convention not yet established; explicit non-goal).
- Categorising entries beyond Keep-a-Changelog's standard set
  (`Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`,
  plus the project-specific `Constraints` for documenting scope
  invariants).

### 5.4 Task 19.4 — `create_release`

**Output:** the git tag `v0.1.0` and the GitHub Release page bound
to it.

Path A (manual, acceptable):

1. On `main`, after §5.1–§5.3 have landed and CI is green, a
   maintainer runs:

   ```
   git tag -a v0.1.0 -m "mempalace-migrator 0.1.0"
   git push origin v0.1.0
   ```

   The tag message must not contain forbidden vocabulary (§4).

2. In the GitHub UI, the maintainer creates a Release targeting tag
   `v0.1.0`, with:
   - Title: `v0.1.0`.
   - Body: copy of the `## [0.1.0]` section from `CHANGELOG.md`,
     verbatim. No paraphrase, no added marketing copy.
   - "Set as the latest release": yes.
   - "Set as a pre-release": no (the project accepts `0.x` as
     production-ready-for-its-stated-scope).

3. No binary asset is attached in Path A. The source tarball that
   GitHub auto-generates for every tag is sufficient; `pip install
   git+https://github.com/apajon/mempalace-migrator.git@v0.1.0`
   works off that tarball.

Path B (automated, optional): a single new workflow
`.github/workflows/release.yml` triggered on `push: tags: ['v*']`
that:

- Checks out the tag.
- Installs the package via `pip install -e ".[dev]"` (same as CI).
- Asserts `pyproject.toml::version == ${GITHUB_REF_NAME#v}` and
  `mempalace_migrator.__version__ == pyproject.toml::version`.
  If either differs, the job fails and the release is **not**
  created.
- Builds a wheel via `python -m build` (adds `build` to
  `[project.optional-dependencies].dev` — the only dependency
  addition M16 is allowed).
- Creates / updates the GitHub Release via
  `softprops/action-gh-release@v2` (pinned by major tag, as in M15)
  with the body read from `CHANGELOG.md` (the `## [X.Y.Z]` section
  corresponding to the tag, parsed by a short shell/awk snippet,
  not by a new Python helper — no production code for release
  automation).
- Attaches the built wheel as a release asset.
- Does **not** publish to PyPI. `twine upload` must not appear in
  the workflow.

If Path B is chosen, the `tests/release/test_release_workflow_surface.py`
module (§7.1) asserts the workflow shape analogous to M15's
`test_workflow_surface.py`. If Path A is chosen, the test module
only exists to enforce §5.1/§5.3 consistency and §7.1 items 4–6.

**Explicitly NOT included in 19.4:**

- PyPI publication (`twine`, `pypa/gh-action-pypi-publish`,
  trusted publishing). M19 owns that.
- SBOM generation, provenance attestation, `cosign` signing.
- Automated CHANGELOG diffing or enforcement of "release notes
  contain at least one bullet". The human-review gate for the
  Release body is the CHANGELOG section, which is already
  reviewed before tagging.

### 5.5 Task 19.5 — `add_release_badge`

**Output:** one additional badge line in `README.md`, immediately
after the existing CI badge, of the form:

```markdown
[![Release](https://img.shields.io/github/v/release/apajon/mempalace-migrator?display_name=tag&sort=semver)](https://github.com/apajon/mempalace-migrator/releases/latest)
```

Constraints:

- The badge must point at the `releases/latest` URL, so a reader
  clicking it always reaches a commit-addressable artefact.
- `sort=semver` is required; without it shields.io sorts
  lexicographically and `v0.10.0` appears older than `v0.2.0`.
- The alt-text (`Release`) is scanned by M14's forbidden-vocabulary
  test; it contains no banned word.
- No additional badges (downloads, stars, coverage, license) are
  added by M16. Badge creep belongs to M18.

§12 "Releases" paragraph in `README.md`:

- One short paragraph, ≤ 5 lines, pointing to `CHANGELOG.md` and to
  the GitHub Releases page.
- States the semver policy in one sentence ("pre-1.0; MINOR bumps
  may break the public contract listed in §5.1 of the release
  design doc").
- Does not repeat the changelog content.

---

## 6. Non-goals (explicit)

- **No** PyPI release. `twine upload` / trusted publishing is
  strictly M19.
- **No** Docker image or container registry push.
- **No** signed tags, no GPG key infrastructure, no `cosign`,
  no SBOM, no provenance. Supply-chain hardening is post-M17.
- **No** auto-generated changelog from commit messages. No
  conventional-commit enforcement.
- **No** release-please / release-drafter bot. Release notes are
  the `CHANGELOG.md` section, reviewed by hand.
- **No** `--version` CLI flag (M18 / UX).
- **No** schema-version bump. `schema_version` stays at `5`. If
  M16 incidentally exposes a gap in the report schema, the gap is
  a defect report against M4 / M11, not an M16 deliverable.
- **No** rewriting of any `*_DESIGN.md`. Those are immutable after
  their milestone closes.
- **No** backport tags for M1–M15. Only `v0.1.0` is created.
- **No** branch for release maintenance (`release/0.1.x`). Single
  `main` branch; patch releases, if any, are tagged directly from
  `main` at the appropriate commit.
- **No** change to `pyproject.toml::requires-python`, `dependencies`,
  or any other field besides `version`.

---

## 7. Testability of a release-discipline milestone

M16 is testable via two orthogonal surfaces, analogous to M15.

### 7.1 Local tests that describe the release surface

A new test module, `tests/release/test_version_consistency.py`
(single file, no production code beyond the §5.1 derivation fix),
asserts:

1. `importlib.metadata.version("mempalace-migrator")` equals
   `mempalace_migrator.__version__`. Drift here is a broken
   installation.
2. `mempalace_migrator.__version__` equals
   `mempalace_migrator.reporting.report_builder.TOOL_VERSION`.
   Drift here reverses the §5.1 fix.
3. `mempalace_migrator.__version__` equals the value parsed from
   `pyproject.toml::[project].version` (parsed with the stdlib
   `tomllib`). Drift here means the derivation chain was broken
   by a refactor.
4. `CHANGELOG.md` exists, parses as Markdown, and contains at
   least one `## [X.Y.Z]` heading matching the semver regex
   `^\d+\.\d+\.\d+$` (no `v` prefix inside the Markdown heading,
   per Keep-a-Changelog; the `v` prefix appears only in git tags
   and URLs).
5. Every `## [X.Y.Z]` heading in `CHANGELOG.md` (excluding
   `[Unreleased]`) has a matching git tag `vX.Y.Z` present in the
   checkout, **or** — if the test runs in a shallow CI clone
   without tags — the assertion is skipped with an explicit reason
   `"shallow clone, tags unavailable"` that only activates when
   `git tag --list vX.Y.Z` returns empty. No silent skip.
6. Every `## [X.Y.Z]` heading has an ISO-8601 date on the same
   line (`## [0.1.0] — 2026-MM-DD`).
7. No forbidden-vocabulary word (§4) appears in `CHANGELOG.md`.
   (Redundant with M14's scan but kept as a local fast-fail.)
8. If `.github/workflows/release.yml` exists (Path B), assertions
   analogous to M15 `test_workflow_surface.py`:
   - Triggered on `push: tags: ['v*']`.
   - Has a step that asserts tag↔version equality.
   - Has no `continue-on-error: true`.
   - Has no `twine upload` / PyPI-publish action.
   - Contains no forbidden vocabulary.

This module runs inside the same `pytest -q` that M15 CI executes,
so any drift in version consistency is caught before merge.

### 7.2 Actual release run

The secondary surface — that a tag push actually produces a reachable
Release — is only verifiable by doing it. M16's exit gate (§9)
therefore requires:

- At least one git tag `v0.1.0` exists on `main` and points to a
  commit whose CI run was green (URL recorded in §11).
- The GitHub Release page for `v0.1.0` is publicly reachable, its
  title matches the tag, and its body matches the `## [0.1.0]`
  section of `CHANGELOG.md` at that commit (recorded in §11 with
  a link).
- (Path B only) at least one successful `release.yml` run is
  recorded (run URL in §11).

M16 does **not** automate that external-surface proof. The proof is
recorded as a caveat in §11 at milestone close, exactly as M15
recorded its CI run URLs.

---

## 8. Failure modes M16 must not introduce

- **Silent version drift.** Three hardcoded literals today; any
  refactor that reintroduces a second literal re-opens the drift.
  Caught by §7.1 items 1–3.
- **Phantom changelog entries.** A `## [0.2.0]` heading written
  before the tag is pushed, or left behind after a release was
  cancelled. Caught by §7.1 item 5.
- **Phantom tags.** A tag pushed without a changelog entry, e.g.
  via an automation mistake. Caught by §7.1 item 5 in the reverse
  direction (every tag should have a changelog entry) — this is a
  **best-effort** assertion because the test has no way to enumerate
  tags on a shallow clone; the assertion runs only when tags are
  available locally and in CI as configured in M15.
- **Forbidden vocabulary in release surfaces.** Caught by §7.1
  item 7 for `CHANGELOG.md` and by M14's test for `README.md §12`.
  Tag messages and GitHub Release bodies are **not** automatically
  scanned (they live outside the repo's file tree); §9 item 8 makes
  the maintainer responsible for this at tag time, with a textual
  checklist in §5.4.
- **Non-reproducible release.** A tag whose commit fails CI. §9
  item 4 requires the tag commit's CI run to be green. A maintainer
  who tags a red commit violates the exit gate, not the code.
- **Leaking secrets in `release.yml`.** `${{ secrets.GITHUB_TOKEN }}`
  is acceptable (built-in, scoped to the repo). Any user-defined
  secret (`${{ secrets.PYPI_TOKEN }}` etc.) is forbidden in M16
  and caught by §7.1 item 8 substring scan.
- **Scope creep into M17.** Anything that looks like
  supply-chain / signing / provenance is rejected.
- **Scope creep into M19.** Anything that publishes to PyPI or a
  private registry is rejected.
- **CHANGELOG that re-documents the code.** The changelog is a
  *change log*, not a user manual. It cross-references README / design
  docs and must not duplicate them.

---

## 9. Exit-gate checklist (what "M16 done" means)

M16 is done **only** when every one of the following holds on `main`:

1. `tests/M16_RELEASE_DISCIPLINE_DESIGN.md` committed (this file).
2. `TODO.json` phase 19 references this design doc in a `design_doc`
   field mirroring M10–M15 convention.
3. `pyproject.toml::[project].version`,
   `mempalace_migrator.__version__`, and
   `reporting.report_builder.TOOL_VERSION` are all derived from a
   single source (per §5.1); drift is caught by
   `tests/release/test_version_consistency.py` items 1–3.
4. `CHANGELOG.md` exists at repo root in Keep-a-Changelog format,
   with an `## [Unreleased]` section and an `## [0.1.0] — YYYY-MM-DD`
   section summarising M1–M15. No forbidden vocabulary.
5. Git tag `v0.1.0` is pushed to `origin/main` and points to a
   commit whose CI (`.github/workflows/ci.yml`) run was green. The
   run URL is recorded in §11 of this doc.
6. A GitHub Release titled `v0.1.0` exists, is set as the latest
   release, is not a pre-release, and its body matches the
   `## [0.1.0]` CHANGELOG section byte-for-byte (modulo the
   `[X.Y.Z]` heading itself). Release URL recorded in §11.
7. `README.md` gains exactly one new badge (§5.5) and one new §12
   "Releases" paragraph. No other README content changes by M16.
   Forbidden-vocabulary scan (M14) still green.
8. `tests/release/test_version_consistency.py` committed with the
   eight assertions of §7.1, all green under the local `pytest -q`
   run and under the M15 CI workflow on the tag commit.
9. If `.github/workflows/release.yml` is added (Path B), at least
   one successful run on tag `v0.1.0` is recorded, and all
   `test_version_consistency.py` assertions apply to it.
10. `ROADMAP.json::current_position.milestone` advanced to
    `M16_done` **only after** 1–9 are all true.
11. No CLI surface, exit code, `AnomalyType`, `Severity`,
    `CheckFamily`, `SkippedReason`, or `schema_version` change is
    observable in `git diff` across the entire M16 delta. The only
    `src/mempalace_migrator/**` diff is the §5.1 derivation fix in
    two files (`__init__.py`, `reporting/report_builder.py`).
12. The M15 CI run remains green at the tag commit (no regression
    introduced by the derivation fix).

If any of 1–12 is false, M16 is **not** done, regardless of how
the Releases page looks.

---

## 10. What M16 explicitly does NOT prove

- M16 does **not** prove the released code is correct. It only
  proves the code at tag `v0.1.0` is the same code that passed
  M15 CI, and that every downstream artefact points to one
  version.
- M16 does **not** prove the release is reproducible bit-for-bit
  (deterministic builds, `SOURCE_DATE_EPOCH`, hermetic
  `python -m build`). Reproducible-build hardening is post-M17.
- M16 does **not** prove the release is signed or its provenance
  is attested. Unsigned annotated tags only.
- M16 does **not** prove the release is installable from PyPI.
  That is M19.
- M16 does **not** prove the release is supported. There is no
  support policy, no LTS window, no back-port obligation. If the
  current commit breaks, users are expected to upgrade to the
  next tagged version.
- M16 does **not** lock future versioning policy beyond §5.1. M17
  may add Deprecated / Removed sections to the changelog; M18 may
  add a `--version` CLI flag; M19 may add PyPI metadata. Each of
  those is a separate milestone with its own scope.

---

## 11. Caveats that will carry forward past M16

(To be populated at milestone close. Expected entries:

- Successful CI run URL at the `v0.1.0` commit (§9 item 5).
- GitHub Release URL for `v0.1.0` (§9 item 6).
- Which release path was chosen (A = manual, B = `release.yml`),
  with a one-line justification.
- Whether `tests/release/test_version_consistency.py` item 5
  (tag ↔ changelog parity) runs in CI or is skip-gated on tag
  availability; if skip-gated, explicit note that the M15 CI clone
  does not fetch tags by default and the assertion is therefore
  locally-enforced only.
- Any discovered drift between the three pre-M16 hardcoded version
  literals (all three are `"0.1.0"` today; this is asserted before
  the derivation fix lands, and documented as a caveat if non-zero).
- Explicit reminder that PyPI publication remains M19; any user
  asking for `pip install mempalace-migrator` from PyPI is
  correctly told "not yet".

These are **documented**, not **fixed**, by M16.)

Evidence slots (to be filled at milestone close):

- Successful CI run URL on `main` at the commit that closes M16:
  _Pending — CI run triggered by push to main at `07800fd`. URL available
  at https://github.com/apajon/mempalace-migrator/actions once complete._
- `v0.1.0` tag commit SHA: `07800fd7afa75bb701ea904bb48687c7ead93dba`
- `v0.1.0` GitHub Release URL: _Pending — tag pushed; Release must be
  created manually in the GitHub UI (Path A). See §5.4 Path A checklist._
- Release path chosen: **A (manual)**. Justification: `release.yml` (Path B)
  is optional per §3; no additional automation is needed for a single
  one-file release. Path A satisfies all exit-gate requirements.
- `test_version_consistency.py` item 5 (tag ↔ changelog parity): runs
  without skip in the local clone because `git tag --list` returns `v0.1.0`.
  In CI shallow clones (`fetch-depth: 0` is not set in `ci.yml`), the tag
  may be absent; the assertion will skip gracefully with
  "git not available" only if the `git` binary is missing. A future CI
  hardening pass should add `fetch-depth: 0` and `fetch-tags: true` to
  ensure item 5 is enforced in CI as well as locally.
- Pre-M16 version drift: all three hardcoded literals were `"0.1.0"` —
  no drift existed. The derivation fix is a forward discipline measure,
  not a correction of observed drift.
- PyPI: not published (M19). `pip install mempalace-migrator` from PyPI
  will fail; this is expected and documented in §6.

---

## 12. Task status after design acceptance

Upon acceptance of this design document, `TODO.json` phase 19 task
statuses remain:

| Task  | Label                 | Status |
|-------|-----------------------|--------|
| 19.1  | introduce_semver      | todo   |
| 19.2  | initial_version       | todo   |
| 19.3  | add_changelog         | todo   |
| 19.4  | create_release        | todo   |
| 19.5  | add_release_badge     | todo   |

None is `done`. Acceptance of the design is not acceptance of the
implementation. `phase.design_doc` is the only field updated at this
point: it now points to `tests/M16_RELEASE_DISCIPLINE_DESIGN.md`.

`ROADMAP.json::current_position.milestone` stays at `M15_done`;
`next_target` stays at `M16` with `next_target_reason` updated to
reference this design document instead of the prior bullet list.
