"""Transformation layer (M9).

Pure, in-memory normalisation of ExtractionResult.drawers into a
TransformedBundle ready for ChromaDB 1.x ingestion.

No I/O. No chromadb import. No filesystem access.
"""

from mempalace_migrator.transformation._types import (
    LengthProfile,
    TransformedBundle,
    TransformedDrawer,
    TransformedSummary,
)
from mempalace_migrator.transformation.transformer import transform

__all__ = [
    "transform",
    "TransformedBundle",
    "TransformedDrawer",
    "TransformedSummary",
    "LengthProfile",
]
