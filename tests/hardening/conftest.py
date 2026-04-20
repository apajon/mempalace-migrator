"""M8 hardening test suite — shared fixtures and baseline corpus.

The **baseline corpus** is the subset of the M7 adversarial corpus whose
``allowed_exit_codes`` are contained in ``{EXIT_OK, EXIT_CRITICAL_ANOMALY}``
(i.e. the pipeline always runs to completion without an unrecoverable structural
rejection).  These are the entries suitable for performance, memory, and
stability measurement.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.adversarial.conftest import CORPUS, EXIT_CRITICAL_ANOMALY, EXIT_OK, CorpusEntry, run_cli

# ---------------------------------------------------------------------------
# M8 baseline corpus
# ---------------------------------------------------------------------------

_BASELINE_EXIT_CODES: frozenset[int] = frozenset({EXIT_OK, EXIT_CRITICAL_ANOMALY})

BASELINE_CORPUS: tuple[CorpusEntry, ...] = tuple(e for e in CORPUS if e.allowed_exit_codes <= _BASELINE_EXIT_CODES)

# ---------------------------------------------------------------------------
# Baseline file paths
# ---------------------------------------------------------------------------

BASELINES_DIR: Path = Path(__file__).parent / "baselines"
RUNTIME_ENVELOPE_PATH: Path = BASELINES_DIR / "runtime_envelope.json"
REPORT_SIGNATURES_PATH: Path = BASELINES_DIR / "report_signatures.json"

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def extract_report_signature(report: dict[str, Any], exit_code: int) -> dict[str, Any]:
    """Distil a full report dict down to the stable, non-volatile signature fields.

    Fields excluded: ``run_id``, ``started_at``, ``completed_at`` (they change per
    run and must not affect the comparison).
    """
    anomaly_summary = report.get("anomaly_summary") or {}
    stages = report.get("stages") or {}
    stages_executed = sorted(k for k, v in stages.items() if (v or {}).get("status") == "executed")
    return {
        "schema_version": report.get("schema_version"),
        "outcome": report.get("outcome"),
        "top_severity": anomaly_summary.get("top_severity"),
        "anomaly_counts_by_type": anomaly_summary.get("by_type", {}),
        "stages_executed": stages_executed,
        "exit_code": exit_code,
    }


def load_runtime_envelope() -> dict[str, Any]:
    """Load the committed runtime envelope, skip the test if the file is absent."""
    if not RUNTIME_ENVELOPE_PATH.exists():
        pytest.skip(
            reason="baseline_missing: runtime_envelope.json not committed; run tests/hardening/rebaseline.py",
            allow_module_level=False,
        )
    return json.loads(RUNTIME_ENVELOPE_PATH.read_text(encoding="utf-8"))


def load_report_signatures() -> dict[str, Any]:
    """Load the committed report signatures, skip the test if the file is absent."""
    if not REPORT_SIGNATURES_PATH.exists():
        pytest.skip(
            reason="baseline_missing: report_signatures.json not committed; run tests/hardening/rebaseline.py",
            allow_module_level=False,
        )
    return json.loads(REPORT_SIGNATURES_PATH.read_text(encoding="utf-8"))
