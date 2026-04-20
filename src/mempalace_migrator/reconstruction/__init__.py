"""Reconstruction layer — the only writer in the pipeline.

Public API re-exported here. The chromadb import lives exclusively in
``_writer.py``; every other module in this package (and the rest of the
project) must remain chromadb-free at module level.
"""

from mempalace_migrator.reconstruction._manifest import TARGET_MANIFEST_FILENAME
from mempalace_migrator.reconstruction._types import ReconstructionResult
from mempalace_migrator.reconstruction.reconstructor import reconstruct

__all__ = ["reconstruct", "ReconstructionResult", "TARGET_MANIFEST_FILENAME"]
