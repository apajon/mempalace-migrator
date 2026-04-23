"""mempalace-migrator: constrained MemPalace reconstruction tool.

Foundation only. Transformation, reconstruction, and validation are stubs.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__: str = _pkg_version("mempalace-migrator")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0.dev0"
    __version__ = "0.0.0.dev0"
