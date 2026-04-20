"""M8 task 11.3 — memory envelope.

For each baseline corpus entry the test:

  1. Runs the CLI once and reads the child-process peak RSS via
     ``resource.getrusage(RUSAGE_CHILDREN)`` (POSIX only).
  2. Asserts the peak RSS is ≤ ``committed_peak * MEMORY_TOLERANCE_FACTOR``.

The committed values live in ``tests/hardening/baselines/runtime_envelope.json``
(field ``peak_rss_raw``).  If the file is absent the tests are skipped with
reason ``baseline_missing``.  On non-POSIX platforms the tests are skipped with
reason ``non_posix_rss_unsupported``.

**Measurement isolation**: ``resource.getrusage(RUSAGE_CHILDREN)`` is
cumulative within a process.  A fresh wrapper subprocess is launched for each
measurement so its ``RUSAGE_CHILDREN`` reflects only one CLI child, not the
accumulated test-suite history.

Linux ``ru_maxrss`` is in kilobytes; macOS in bytes.  The platform note stored
in the baseline (``peak_rss_platform_note``) is used for an informational
assertion that the baseline was generated on the same OS family.  Tests are
skipped rather than failed when the platform note mismatches, to avoid
false failures on cross-platform CI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.adversarial.conftest import CorpusEntry
from tests.hardening.conftest import BASELINE_CORPUS, load_runtime_envelope

# ---------------------------------------------------------------------------
# POSIX guard
# ---------------------------------------------------------------------------

try:
    import resource as _resource  # noqa: F401 — only used to confirm availability

    _HAS_RESOURCE = True
except ImportError:
    _HAS_RESOURCE = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Tolerance is read from the committed baseline file (tolerance.rss_bytes_pct).
# Fallback of 25 % is only used when the key is absent (e.g. legacy baselines).
_MEMORY_TOLERANCE_FALLBACK_PCT = 25

_SRC = Path(__file__).resolve().parents[2] / "src"

# ---------------------------------------------------------------------------
# Measurement wrapper
#
# Run as a *fresh* subprocess so RUSAGE_CHILDREN reflects exactly one child
# (the CLI under test) rather than the accumulated history of the test suite.
# ---------------------------------------------------------------------------

_WRAPPER_SCRIPT = """\
import json, os, resource, subprocess, sys
cli_args = json.loads(sys.argv[1])
src_path = sys.argv[2]
env = {**os.environ, "PYTHONPATH": src_path}
subprocess.run(cli_args, capture_output=True, text=True, env=env, timeout=60)
print(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
"""


def _measure_peak_rss(cli_args: list[str]) -> int:
    """Return peak RSS (raw platform units) for a single CLI invocation."""
    env = {**os.environ, "PYTHONPATH": str(_SRC)}
    result = subprocess.run(
        [sys.executable, "-c", _WRAPPER_SCRIPT, json.dumps(cli_args), str(_SRC)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    return int(result.stdout.strip())


def _ids(entries: tuple[CorpusEntry, ...]) -> list[str]:
    return [e.cid for e in entries]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", BASELINE_CORPUS, ids=_ids(BASELINE_CORPUS))
def test_peak_rss_within_envelope(entry: CorpusEntry, tmp_path):
    """Peak RSS for *entry* must not exceed committed_peak * memory_tolerance_factor."""
    if not _HAS_RESOURCE:
        pytest.skip(reason="non_posix_rss_unsupported")

    envelope = load_runtime_envelope()
    by_cid = {rec["cid"]: rec for rec in envelope.get("entries", [])}

    if entry.cid not in by_cid:
        pytest.skip(reason=f"baseline_missing: no committed RSS for {entry.cid!r}")

    rec = by_cid[entry.cid]
    committed_rss = rec.get("peak_rss_raw")
    if committed_rss is None:
        pytest.skip(reason=f"baseline_missing: peak_rss_raw is null for {entry.cid!r}")

    # Skip silently when the baseline was generated on a different platform
    # (mixing linux_kb and macos_bytes would produce meaningless comparisons).
    platform_note = rec.get("peak_rss_platform_note", "")
    if sys.platform == "linux" and "linux" not in platform_note:
        pytest.skip(reason=f"platform_mismatch: baseline note={platform_note!r}, current=linux")
    if sys.platform == "darwin" and "macos" not in platform_note:
        pytest.skip(reason=f"platform_mismatch: baseline note={platform_note!r}, current=darwin")

    tolerance_pct: float = (envelope.get("tolerance") or {}).get("rss_bytes_pct", _MEMORY_TOLERANCE_FALLBACK_PCT)
    memory_tolerance_factor = 1.0 + tolerance_pct / 100.0
    limit = int(committed_rss * memory_tolerance_factor)

    palace = entry.builder(tmp_path)
    cli_args = [sys.executable, "-m", "mempalace_migrator.cli.main", "--json-output", entry.pipeline, str(palace)]
    live_rss = _measure_peak_rss(cli_args)

    assert live_rss <= limit, (
        f"[{entry.cid}] RSS regression: "
        f"live={live_rss} > limit={limit} "
        f"(committed={committed_rss} × {memory_tolerance_factor:.2f})"
    )
