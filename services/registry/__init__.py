"""Registry services package.

Exposes all five registry service classes, the core HiveWriter, and their
error types for convenient import by the orchestrator.
"""

from services.registry.hive_writer import (
    HiveWriter,
    HiveWriterError,
    HiveOperation,
    RegistryValueType,
)
from services.registry.system_identity import SystemIdentity, SystemIdentityError
from services.registry.installed_programs import InstalledPrograms, InstalledProgramsError
from services.registry.network_profiles import NetworkProfiles, NetworkProfilesError
from services.registry.mru_recentdocs import MruRecentDocs, MruRecentDocsError
from services.registry.userassist import UserAssist, UserAssistError

__all__ = [
    "HiveWriter",
    "HiveWriterError",
    "HiveOperation",
    "RegistryValueType",
    "SystemIdentity",
    "SystemIdentityError",
    "InstalledPrograms",
    "InstalledProgramsError",
    "NetworkProfiles",
    "NetworkProfilesError",
    "MruRecentDocs",
    "MruRecentDocsError",
    "UserAssist",
    "UserAssistError",
]
