"""M17 task 20.4 — anomaly completeness sweep across the full corpus.

Inv. 9: for every corpus entry whose CLI run exits non-zero, at least one
anomaly must have ``location.stage`` equal to ``report.failure.stage``.

This is a deliberate duplication of runtime vs. the invariant sweep in
``test_adversarial_invariants.py``.  The duplication is intentional:
it separates "invariant adherence" (M7) from "anomaly coverage" (M17).

Each corpus entry is run exactly once via the real subprocess CLI.
The migrate entries receive a fresh per-invocation target directory so
no state leaks between parametrised runs.

If any entry fails Inv. 9, the fix belongs in the production module that
owns the failure stage — never in this test.  Do NOT widen the check or
add xfail markers to make CI green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.adversarial._invariants import (
    check_failure_has_anomaly_at_stage,
    check_no_unexpected_exit_code,
)
from tests.adversarial.conftest import CORPUS, CorpusEntry, run_cli


def _ids(entries: tuple[CorpusEntry, ...]) -> list[str]:
    return [e.cid for e in entries]


@pytest.mark.parametrize("entry", CORPUS, ids=_ids(CORPUS))
def test_anomaly_completeness(entry: CorpusEntry, tmp_path: Path) -> None:
    """Non-zero exit ⇒ ≥1 anomaly at failure.stage (Inv. 9)."""
    palace = entry.builder(tmp_path / "src")

    args: list[str] = ["--json-output"]
    args.append(entry.pipeline)
    args.append(str(palace))
    if entry.pipeline == "migrate":
        target = tmp_path / "target"
        args.extend(["--target", str(target)])

    result = run_cli(args)

    check_no_unexpected_exit_code(entry.cid, result.returncode, result.stderr)

    if not result.stdout.strip():
        # No JSON report (only happens for non-zero exits without --json-output
        # falling through, which shouldn't occur but guard it gracefully).
        if result.returncode != 0:
            pytest.fail(
                f"[{entry.cid}] non-zero exit {result.returncode} with empty stdout; "
                f"cannot check anomaly completeness.\nstderr={result.stderr!r}"
            )
        return

    report = json.loads(result.stdout)
    check_failure_has_anomaly_at_stage(entry.cid, report, result.returncode)
