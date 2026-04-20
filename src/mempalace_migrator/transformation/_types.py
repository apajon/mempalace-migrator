"""Frozen dataclasses for the transformation stage output bundle.

No logic here. All dicts stored inside these dataclasses must be JSON-safe
(values are str | int | float | bool only — enforced by transformer.py).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TransformedDrawer:
    """A single drawer that passed all normalisation checks."""

    id: str  # non-empty, no control chars
    document: str  # non-empty string
    metadata: dict  # dict[str, str|int|float|bool]


@dataclass(frozen=True)
class LengthProfile:
    """Document-length statistics over the full drawer set.

    Lengths are Unicode code-point counts (len(document)), not byte lengths.
    Percentiles are computed on the full ordered list (nearest-rank method,
    low-edge index: rank = ceil(pct/100 * N) - 1, clamped to [0, N-1]).
    """

    min: int
    max: int
    mean: float  # rounded to 2 decimal places
    p50: int
    p95: int


@dataclass(frozen=True)
class TransformedSummary:
    drawer_count: int
    sample_ids: tuple  # tuple[str, ...] — capped at 20, sorted
    metadata_keys: tuple  # tuple[str, ...] — union across drawers, sorted
    wing_room_counts: tuple  # tuple[tuple[str, str, int], ...] — (wing, room, n), sorted
    length_profile: LengthProfile
    dropped_count: int  # input drawers excluded by transformation
    coerced_count: int  # drawers that survived with ≥1 coerced metadata value


@dataclass(frozen=True)
class TransformedBundle:
    collection_name: str  # always EXPECTED_COLLECTION_NAME
    collection_metadata: dict  # dict[str, str|int|float|bool], empty in M9
    drawers: tuple  # tuple[TransformedDrawer, ...]
    summary: TransformedSummary
