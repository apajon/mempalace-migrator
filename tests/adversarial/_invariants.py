"""Pure invariant check functions shared by M7 (adversarial corpus) and
M8 (hardening / stability) test suites.

Each function raises ``AssertionError`` on violation.  Arguments are plain
data (strings, dicts, ints) — no pytest fixtures or test-framework
dependencies.  This makes the logic importable by any test file.
"""

from __future__ import annotations

import json
import re
from typing import Any

from mempalace_migrator.core.context import AnomalyType
from mempalace_migrator.reporting.report_builder import REPORT_SCHEMA_VERSION, REPORT_TOP_LEVEL_KEYS

# ---------------------------------------------------------------------------
# Constants re-exported for convenience
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_CRITICAL_ANOMALY = 8
EXIT_UNEXPECTED = 10

_TRACEBACK_RE = re.compile(r"^Traceback \(most recent call last\):", re.MULTILINE)

_FORBIDDEN_WORDS = ("correct", "verified", "guaranteed", "valid")
_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(_FORBIDDEN_WORDS) + r")\b",
    re.IGNORECASE,
)

_KNOWN_STAGES = frozenset({"detect", "extract", "transform", "reconstruct", "validate", "report"})
_VALID_ANOMALY_TYPES = frozenset(t.value for t in AnomalyType)

# ---------------------------------------------------------------------------
# Individual check functions — Inv. 1 through Inv. 9
# ---------------------------------------------------------------------------


def check_no_unexpected_exit_code(cid: str, rc: int, stderr: str) -> None:
    """Inv. 1 — exit 10 must never appear."""
    assert rc != EXIT_UNEXPECTED, (
        f"[{cid}] CLI returned EXIT_UNEXPECTED (10); "
        f"adversarial input revealed an unmodelled failure mode.\n"
        f"stderr={stderr!r}"
    )


def check_exit_code_in_allowed_set(cid: str, rc: int, allowed: frozenset[int], stderr: str) -> None:
    """Inv. ent. — exit code must be within the per-entry allowed set."""
    assert rc in allowed, f"[{cid}] exit code {rc} not in allowed set {sorted(allowed)}.\n" f"stderr={stderr!r}"


def check_no_traceback_on_stderr(cid: str, stderr: str) -> None:
    """Inv. 2 — no Python traceback on stderr (without --debug)."""
    assert not _TRACEBACK_RE.search(stderr), (
        f"[{cid}] Python traceback escaped to stderr without --debug.\n" f"stderr={stderr!r}"
    )


def check_no_silent_critical(cid: str, report: dict[str, Any] | None, rc: int) -> None:
    """Inv. 3 — outcome=success ⇒ exit ∈ {0, 8}; exit 0 ⇒ top_severity != 'critical'."""
    if report is None:
        return
    outcome = report.get("outcome")
    top_sev = (report.get("anomaly_summary") or {}).get("top_severity", "none")
    if outcome == "success":
        assert rc in (EXIT_OK, EXIT_CRITICAL_ANOMALY), f"[{cid}] outcome=success but exit {rc} not in {{0, 8}}"
    if rc == EXIT_OK:
        assert top_sev != "critical", f"[{cid}] exit 0 but top_severity=critical (silent-CRITICAL guard violated)"


def check_schema_stability(cid: str, report: dict[str, Any] | None) -> None:
    """Inv. 4 — schema_version == REPORT_SCHEMA_VERSION; all top-level keys present."""
    if report is None:
        return
    assert report.get("schema_version") == REPORT_SCHEMA_VERSION, (
        f"[{cid}] schema_version={report.get('schema_version')!r} " f"!= REPORT_SCHEMA_VERSION={REPORT_SCHEMA_VERSION}"
    )
    missing = [k for k in REPORT_TOP_LEVEL_KEYS if k not in report]
    assert not missing, f"[{cid}] report missing top-level keys: {missing}"


def check_json_safety(cid: str, report: dict[str, Any] | None) -> None:
    """Inv. 5 — report round-trips through json.dumps with no default=."""
    if report is None:
        return
    json.dumps(report)  # raises TypeError on non-JSON-safe value


def check_anomaly_well_formedness(cid: str, report: dict[str, Any] | None) -> None:
    """Inv. 6 — each anomaly has a registered type, non-empty stage, ≥1 evidence."""
    if report is None:
        return
    for i, a in enumerate(report.get("anomalies") or []):
        assert a.get("type") in _VALID_ANOMALY_TYPES, f"[{cid}] anomaly[{i}].type={a.get('type')!r} not in AnomalyType"
        loc = a.get("location") or {}
        stage = loc.get("stage", "")
        assert isinstance(stage, str) and stage.strip(), f"[{cid}] anomaly[{i}].location.stage is empty"
        evidence = a.get("evidence") or []
        assert isinstance(evidence, list) and len(evidence) >= 1, f"[{cid}] anomaly[{i}] has no evidence entries"


def check_no_forbidden_vocabulary(cid: str, report: dict[str, Any] | None) -> None:
    """Inv. 7 — serialised report contains none of correct|verified|guaranteed|valid."""
    if report is None:
        return
    matches = _FORBIDDEN_RE.findall(json.dumps(report))
    assert not matches, f"[{cid}] forbidden correctness vocabulary in JSON output: {matches}"


def check_failure_stage_is_known(cid: str, report: dict[str, Any] | None) -> None:
    """Inv. 8 — failure.stage ∈ known stages."""
    if report is None:
        return
    failure = report.get("failure")
    if failure is None:
        return
    assert (
        failure.get("stage") in _KNOWN_STAGES
    ), f"[{cid}] failure.stage={failure.get('stage')!r} not in {_KNOWN_STAGES}"


def check_all_structural(cid: str, report: dict[str, Any] | None, rc: int, stderr: str) -> None:
    """Run Inv. 2–8 in sequence.  Stops at the first failure."""
    check_no_traceback_on_stderr(cid, stderr)
    check_no_silent_critical(cid, report, rc)
    check_schema_stability(cid, report)
    check_json_safety(cid, report)
    check_anomaly_well_formedness(cid, report)
    check_no_forbidden_vocabulary(cid, report)
    check_failure_stage_is_known(cid, report)
    check_anomaly_well_formedness(cid, report)
    check_no_forbidden_vocabulary(cid, report)
    check_failure_stage_is_known(cid, report)
