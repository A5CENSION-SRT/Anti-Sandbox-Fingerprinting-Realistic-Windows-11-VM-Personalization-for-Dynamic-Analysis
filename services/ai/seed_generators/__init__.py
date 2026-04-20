"""AI-powered seed generators for artifact creation.

Each generator produces 10-50 "seeds" via Gemini that are then expanded
into thousands of artifacts by the local permutation engines.
"""

from services.ai.seed_generators.downloads import DownloadSeedGenerator
from services.ai.seed_generators.documents import DocumentSeedGenerator
from services.ai.seed_generators.browsing import BrowsingSeedGenerator
from services.ai.seed_generators.filenames import FilenameSeedGenerator
from services.ai.seed_generators.registry import RegistrySeedGenerator
from services.ai.seed_generators.evtx import EvtxSeedGenerator
from services.ai.seed_generators.prefetch import PrefetchSeedGenerator
from services.ai.seed_generators.media import MediaSeedGenerator

__all__ = [
    "DownloadSeedGenerator",
    "DocumentSeedGenerator",
    "BrowsingSeedGenerator",
    "FilenameSeedGenerator",
    "RegistrySeedGenerator",
    "EvtxSeedGenerator",
    "PrefetchSeedGenerator",
    "MediaSeedGenerator",
]
