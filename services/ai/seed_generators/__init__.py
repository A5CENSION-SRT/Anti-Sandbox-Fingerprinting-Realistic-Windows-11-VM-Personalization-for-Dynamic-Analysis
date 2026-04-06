"""AI-powered seed generators for artifact creation.

Each generator produces 10-50 "seeds" via Gemini that are then expanded
into thousands of artifacts by the local permutation engines.
"""

from services.ai.seed_generators.downloads import DownloadSeedGenerator
from services.ai.seed_generators.documents import DocumentSeedGenerator
from services.ai.seed_generators.browsing import BrowsingSeedGenerator
from services.ai.seed_generators.filenames import FilenameSeedGenerator

__all__ = [
    "DownloadSeedGenerator",
    "DocumentSeedGenerator",
    "BrowsingSeedGenerator",
    "FilenameSeedGenerator",
]
