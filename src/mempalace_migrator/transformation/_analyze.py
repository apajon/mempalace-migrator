"""Pure structural analysis over a sequence of TransformedDrawers.

Computes the summary statistics stored in TransformedSummary. This module
contains no anomaly emission — it only computes and returns values. Callers
(transformer.py) decide what to do with the results.

This module imports nothing outside the standard library (no chromadb,
no sqlite3, no os, no pathlib, no shutil, no tempfile).
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Sequence

from mempalace_migrator.transformation._types import LengthProfile, TransformedDrawer, TransformedSummary

_SAMPLE_CAP = 20


def _percentile_nearest_rank(sorted_values: list[int], pct: float) -> int:
    """Nearest-rank percentile on a pre-sorted list. Returns 0 for empty list."""
    n = len(sorted_values)
    if n == 0:
        return 0
    rank = math.ceil(pct / 100.0 * n) - 1
    rank = max(0, min(rank, n - 1))
    return sorted_values[rank]


def _build_length_profile(drawers: Sequence[TransformedDrawer]) -> LengthProfile:
    if not drawers:
        return LengthProfile(min=0, max=0, mean=0.0, p50=0, p95=0)
    lengths = sorted(len(d.document) for d in drawers)
    return LengthProfile(
        min=lengths[0],
        max=lengths[-1],
        mean=round(sum(lengths) / len(lengths), 2),
        p50=_percentile_nearest_rank(lengths, 50),
        p95=_percentile_nearest_rank(lengths, 95),
    )


def _build_wing_room_counts(
    drawers: Sequence[TransformedDrawer],
) -> tuple[tuple[str, str, int], ...]:
    counts: Counter[tuple[str, str]] = Counter()
    for d in drawers:
        wing = d.metadata.get("wing")
        room = d.metadata.get("room")
        if isinstance(wing, str) and isinstance(room, str):
            counts[(wing, room)] += 1
    return tuple(sorted((w, r, c) for (w, r), c in counts.items()))


def build_summary(
    drawers: Sequence[TransformedDrawer],
    *,
    dropped_count: int,
    coerced_count: int,
) -> TransformedSummary:
    """Compute TransformedSummary from the final accepted drawers list."""
    all_keys: set[str] = set()
    for d in drawers:
        all_keys.update(d.metadata.keys())

    sample_ids = tuple(sorted(d.id for d in drawers)[:_SAMPLE_CAP])
    metadata_keys = tuple(sorted(all_keys))
    wing_room_counts = _build_wing_room_counts(drawers)
    length_profile = _build_length_profile(drawers)

    return TransformedSummary(
        drawer_count=len(drawers),
        sample_ids=sample_ids,
        metadata_keys=metadata_keys,
        wing_room_counts=wing_room_counts,
        length_profile=length_profile,
        dropped_count=dropped_count,
        coerced_count=coerced_count,
    )
