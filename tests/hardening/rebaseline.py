#!/usr/bin/env python3
"""M8 baseline generator — NOT collected by pytest (no test_ prefix).

Run this script from the repository root whenever the production-code
behaviour changes in a way that legitimately alters timing, memory use, or
report content:

    python3 tests/hardening/rebaseline.py

The script materialises each baseline corpus entry in a temporary directory,
runs the CLI ``N_RUNS`` times, and records:

  * ``runtime_envelope.json``  — per-entry median wall-clock (seconds) and peak
    RSS (bytes, POSIX only; ``null`` on non-POSIX platforms).
  * ``report_signatures.json`` — per-entry stable report signature (fields that
    must not change between HEAD runs).

Both files are written to ``tests/hardening/baselines/`` and should be
committed together with any production-code change that causes them to
drift.
"""

from __future__ import annotations

import datetime
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from statistics import median
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap: ensure src/ is on sys.path when run directly
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Also make tests/ importable for BASELINE_CORPUS
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.adversarial.conftest import EXIT_OK, CorpusEntry, run_cli  # noqa: E402
from tests.hardening.conftest import BASELINE_CORPUS, BASELINES_DIR, extract_report_signature  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

N_RUNS = 5  # runs per entry for timing statistics

# Inline measurement wrapper: launched as a fresh subprocess so RUSAGE_CHILDREN
# reflects exactly one CLI child (not the accumulated rebaseline history).
_RSS_WRAPPER = """\
import json, os, resource, subprocess, sys
cli_args = json.loads(sys.argv[1])
src_path = sys.argv[2]
env = {**os.environ, "PYTHONPATH": src_path}
subprocess.run(cli_args, capture_output=True, text=True, env=env, timeout=60)
print(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
"""

# Try to import resource for availability check only
try:
    import resource as _resource

    _HAS_RESOURCE = True
except ImportError:
    _HAS_RESOURCE = False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _measure_run(args: list[str]) -> tuple[float, int | None, str, str, int]:
    """Run CLI with *args*, return (wall_clock_s, peak_rss_bytes_or_none, stdout, stderr, rc)."""
    env = {
        **os.environ,
        "PYTHONPATH": str(_SRC),
    }
    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "mempalace_migrator.cli.main", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    elapsed = time.monotonic() - t0

    rss: int | None = None
    if _HAS_RESOURCE:
        # Measure in a fresh wrapper subprocess so RUSAGE_CHILDREN is isolated
        # to exactly one CLI invocation.
        wrapper_result = subprocess.run(
            [
                sys.executable,
                "-c",
                _RSS_WRAPPER,
                json.dumps([sys.executable, "-m", "mempalace_migrator.cli.main", *args]),
                str(_SRC),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        try:
            rss = int(wrapper_result.stdout.strip())
        except (ValueError, AttributeError):
            pass

    return elapsed, rss, proc.stdout, proc.stderr, proc.returncode


def _baseline_entry(entry: CorpusEntry, palace_path: Path) -> dict[str, Any]:
    """Measure *entry* and return the combined baseline record."""
    args = ["--json-output", entry.pipeline, str(palace_path)]

    wall_times: list[float] = []
    rss_values: list[int] = []
    last_stdout = ""
    last_stderr = ""
    last_rc = -1

    for _ in range(N_RUNS):
        elapsed, rss, stdout, stderr, rc = _measure_run(args)
        wall_times.append(elapsed)
        if rss is not None:
            rss_values.append(rss)
        last_stdout, last_stderr, last_rc = stdout, stderr, rc

    wall_p50 = median(wall_times)
    rss_peak: int | None = max(rss_values) if rss_values else None

    # Parse the last report for the signature
    report: dict[str, Any] | None = None
    if last_stdout.strip():
        try:
            report = json.loads(last_stdout)
        except json.JSONDecodeError:
            pass

    signature = extract_report_signature(report, last_rc) if report is not None else None

    return {
        "cid": entry.cid,
        "wall_clock_seconds_p50": round(wall_p50, 4),
        "peak_rss_raw": rss_peak,
        "peak_rss_platform_note": "linux_kb" if _HAS_RESOURCE else "unavailable",
        "exit_code": last_rc,
        "signature": signature,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)

    recorded_on = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    tolerance = {"wall_clock_pct": 50, "rss_bytes_pct": 25}

    runtime_records: list[dict[str, Any]] = []
    signature_records: list[dict[str, Any]] = []

    total = len(BASELINE_CORPUS)
    for idx, entry in enumerate(BASELINE_CORPUS, 1):
        print(f"[{idx}/{total}] {entry.cid} ({entry.pipeline}) ...", flush=True)
        with tempfile.TemporaryDirectory(prefix=f"rebaseline_{entry.cid}_") as tmp:
            palace = entry.builder(Path(tmp))
            record = _baseline_entry(entry, palace)

        runtime_records.append(
            {
                "cid": record["cid"],
                "wall_clock_seconds_p50": record["wall_clock_seconds_p50"],
                "peak_rss_raw": record["peak_rss_raw"],
                "peak_rss_platform_note": record["peak_rss_platform_note"],
            }
        )
        if record["signature"] is not None:
            signature_records.append(
                {
                    "cid": record["cid"],
                    "signature": record["signature"],
                }
            )
        print(
            f"       wall_p50={record['wall_clock_seconds_p50']:.4f}s  "
            f"rss={record['peak_rss_raw']}  "
            f"exit={record['exit_code']}",
            flush=True,
        )

    # Write runtime_envelope.json
    runtime_path = BASELINES_DIR / "runtime_envelope.json"
    runtime_payload = {
        "schema_version": 1,
        "recorded_on": recorded_on,
        "python_version": python_version,
        "tolerance": tolerance,
        "entries": runtime_records,
    }
    runtime_path.write_text(
        json.dumps(runtime_payload, indent=2),
        encoding="utf-8",
    )
    print(f"\nWrote {runtime_path}")

    # Write report_signatures.json
    sig_path = BASELINES_DIR / "report_signatures.json"
    sig_payload = {
        "schema_version": 1,
        "recorded_on": recorded_on,
        "entries": signature_records,
    }
    sig_path.write_text(
        json.dumps(sig_payload, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {sig_path}")
    print(f"\nBaseline complete: {len(runtime_records)} runtime entries, {len(signature_records)} signature entries.")


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
