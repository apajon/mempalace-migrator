"""Pure metadata normalisation for the transformation stage.

Accepts a raw metadata dict (as produced by DrawerRecord.metadata) and
returns a (normalised_dict, drop_reason_or_None) pair.

Rules (from M9 design §5):
  - str, int, float, bool  → pass through unchanged (bool before int check)
  - bool masquerading as int → preserved as bool (type(v) is bool guard)
  - int outside [-2**63, 2**63-1] → coerce to str(v); issue appended
  - float NaN / ±Inf → return drop reason 'non_finite_float'
  - None → return drop reason 'metadata_value_none'
  - list, tuple, dict, set, other → return drop reason
      'unsupported_metadata_value_type'
  - non-str key → return drop reason 'non_string_metadata_key'

Return value:
  (normalised_dict, drop_reason, coercion_details)

  normalised_dict      — the cleaned dict (may be {} on drop)
  drop_reason          — one of TRANSFORM_DROP_REASONS or None
  coercion_details     — list of dicts, one per coerced value:
                           {key, original_repr, new_value}

Callers must inspect drop_reason first; if it is non-None the drawer
must be dropped and the dict value is meaningless.

This module imports nothing outside the standard library (no chromadb,
no sqlite3, no os, no pathlib, no shutil, no tempfile).
"""

from __future__ import annotations

import math
from typing import Any

# Stable int range that SQLite and chroma 1.x both support.
_INT_MIN = -(2**63)
_INT_MAX = 2**63 - 1

NormaliseResult = tuple[
    dict[str, Any],  # normalised metadata (meaningful only when drop_reason is None)
    str | None,  # drop_reason (from TRANSFORM_DROP_REASONS) or None
    list[dict],  # coercion_details — populated on MEDIUM anomaly path
]


def normalize_metadata(raw: dict[str, Any]) -> NormaliseResult:
    """Normalise a raw metadata dict; return (clean_dict, drop_reason, coercions).

    Stops at the first drop-worthy failure (first-failure-wins policy, matching
    the extraction stage's per-row contract).
    """
    out: dict[str, Any] = {}
    coercions: list[dict] = []

    for key, value in raw.items():
        # Non-string key — hard drop
        if not isinstance(key, str):
            return {}, "non_string_metadata_key", []

        # bool before int (bool IS-A int in Python)
        if type(value) is bool:
            out[key] = value
            continue

        if isinstance(value, int):
            if _INT_MIN <= value <= _INT_MAX:
                out[key] = value
            else:
                coerced = str(value)
                coercions.append(
                    {
                        "key": key,
                        "original_repr": repr(value)[:200],
                        "new_value": coerced,
                        "reason": "int_out_of_range",
                    }
                )
                out[key] = coerced
            continue

        if isinstance(value, float):
            if not math.isfinite(value):
                return {}, "non_finite_float", []
            out[key] = value
            continue

        if isinstance(value, str):
            out[key] = value
            continue

        if value is None:
            return {}, "metadata_value_none", []

        # list, tuple, dict, set, and any other type
        return {}, "unsupported_metadata_value_type", []

    return out, None, coercions
