"""NTFS metadata manipulation services.

This package contains services for manipulating NTFS-specific metadata
that cannot be accessed via standard filesystem APIs:

- MftTimestampPatcher: Sets $STANDARD_INFORMATION timestamps via FUSE
- UsnJournalWriter: Appends records to $UsnJrnl:$J
- LogfileWriter: Creates $LogFile stub (optional)

These services require a FUSE-mounted NTFS filesystem and run in Phase B
of the orchestration (after libguestfs unmount).
"""

from services.ntfs.mft_timestamp_patcher import MftTimestampPatcher
from services.ntfs.usn_journal_writer import UsnJournalWriter

__all__ = [
    "MftTimestampPatcher",
    "UsnJournalWriter",
]
