"""Local permutation engines for massive-scale artifact generation.

These engines take AI-generated seeds (10-50 per type) and expand them
into thousands of unique artifacts through combinatorial permutation.
"""

from services.generators.filename_permutator import FilenamePermutator, expand_filename_pattern
from services.generators.content_variator import ContentVariator, vary_content
from services.generators.bulk_downloads import BulkDownloadsGenerator, ExpandedDownload
from services.generators.bulk_documents import BulkDocumentsGenerator, ExpandedDocument
from services.generators.bulk_media import BulkMediaGenerator, ExpandedMediaFile
from services.generators.bulk_browsing import (
    BulkBrowsingGenerator,
    ExpandedHistoryEntry,
    ExpandedSearchTerm,
    ExpandedBookmark,
)

__all__ = [
    # Core permutators
    "FilenamePermutator",
    "ContentVariator",
    "expand_filename_pattern",
    "vary_content",
    
    # Bulk generators
    "BulkDownloadsGenerator",
    "BulkDocumentsGenerator",
    "BulkMediaGenerator",
    "BulkBrowsingGenerator",
    
    # Data classes
    "ExpandedDownload",
    "ExpandedDocument",
    "ExpandedMediaFile",
    "ExpandedHistoryEntry",
    "ExpandedSearchTerm",
    "ExpandedBookmark",
]
