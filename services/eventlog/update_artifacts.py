"""Windows Update artifact service.

Writes Windows Update history artifacts across two surfaces:
  1. **Registry** — ``SOFTWARE`` hive keys that Windows Update Manager (WUA)
     populates to record installed KB updates.
  2. **Event Log** — ``Windows/System32/winevt/Logs/System.evtx`` appended
     with WUA event IDs (19, 20, 43, 44) so event-log forensics show a
     realistic update history.

This module is a **pure operation builder** — it builds :class:`HiveOperation`
and :class:`EvtxRecord` lists, then delegates all I/O to the injected
:class:`HiveWriter` and :class:`EvtxWriter`.

Registry target paths
----------------------
``SOFTWARE`` hive:
* ``Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\Results\\Install``
    — LastSuccessTime (REG_SZ, ISO-8601)
* ``Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\Results\\Detect``
    — LastSuccessTime
* ``Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update``
    — AUOptions (DWORD, 4=auto-download+install)
* ``Microsoft\\Windows\\CurrentVersion\\WindowsUpdate``
    — AccountDomainSid, PingID, SusClientId, SusClientIDValidation

Event Log target
----------------
* ``Windows/System32/winevt/Logs/System.evtx``
    — EID 19: Update download started
    — EID 20: Update download completed
    — EID 43: Update install started
    — EID 44: Update install completed

Data source
-----------
``data/kb_updates.json`` — list of realistic KB numbers, titles, and dates.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Sequence

from services.base_service import BaseService
from services.eventlog.evtx_writer import EvtxRecord, EvtxWriter, EvtxWriterError
from services.registry.hive_writer import (
    HiveOperation,
    HiveWriter,
    HiveWriterError,
    RegistryValueType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SOFTWARE_HIVE: str = "Windows/System32/config/SOFTWARE"
_SYSTEM_EVTX: str = "Windows/System32/winevt/Logs/System.evtx"

# Registry key paths
_WU_AUTO_UPDATE: str = (
    r"Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update"
)
_WU_RESULTS_INSTALL: str = (
    r"Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\Results\Install"
)
_WU_RESULTS_DETECT: str = (
    r"Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\Results\Detect"
)
_WU_ROOT: str = (
    r"Microsoft\Windows\CurrentVersion\WindowsUpdate"
)

# WUA event IDs (System channel, provider: Microsoft-Windows-WindowsUpdateClient)
_EID_DOWNLOAD_START: int = 19
_EID_DOWNLOAD_DONE: int = 20
_EID_INSTALL_START: int = 43
_EID_INSTALL_DONE: int = 44

_PROVIDER_WUA: str = "Microsoft-Windows-WindowsUpdateClient"
_CHANNEL: str = "System"

# AU options: 4 = auto-download and auto-install
_AU_OPTIONS_AUTO: int = 4

# Number of updates to surface per profile type
_UPDATES_BY_PROFILE: Dict[str, int] = {
    "home": 8,
    "office": 14,
    "developer": 18,
}

# KB update data file
_KB_DATA_FILE: str = "kb_updates.json"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class UpdateArtifactsError(Exception):
    """Raised when update artifact operations fail."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class UpdateArtifacts(BaseService):
    """Writes Windows Update registry keys and event log entries.

    Combines registry HiveOperation writes (via HiveWriter) and EVTX event
    records (via EvtxWriter) to produce a realistic update history surface.

    Dependencies (injected):
        hive_writer:  Low-level registry hive writer.
        evtx_writer:  EVTX binary event log writer.
        audit_logger: Shared audit logger for traceability.
        data_dir:     Path to the ``data/`` directory containing
                      ``kb_updates.json``.

    Args:
        hive_writer:  ``HiveWriter`` instance.
        evtx_writer:  ``EvtxWriter`` instance.
        audit_logger: ``AuditLogger`` instance.
        data_dir:     ``pathlib.Path`` to the data directory.
    """

    def __init__(
        self,
        hive_writer: HiveWriter,
        evtx_writer: EvtxWriter,
        audit_logger: Any,
        data_dir: Path,
    ) -> None:
        self._hive_writer = hive_writer
        self._evtx_writer = evtx_writer
        self._audit_logger = audit_logger
        self._data_dir = data_dir
        self._kb_data: List[Dict[str, Any]] = self._load_kb_data()

    # -- BaseService interface ----------------------------------------------

    @property
    def service_name(self) -> str:
        """Return the unique service name."""
        return "UpdateArtifacts"

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            profile_type:  str — ``"home"``, ``"office"``, or ``"developer"``.
            computer_name: str — the VM computer name.
            install_date:  datetime — simulated Windows install date.

        Raises:
            UpdateArtifactsError: On missing context keys or write failure.
        """
        for key in ("profile_type", "computer_name", "install_date"):
            if not context.get(key):
                raise UpdateArtifactsError(
                    f"Missing required '{key}' in context"
                )
        self.write_update_artifacts(
            profile_type=context["profile_type"],
            computer_name=context["computer_name"],
            install_date=context["install_date"],
        )

    # -- public API ---------------------------------------------------------

    def write_update_artifacts(
        self,
        profile_type: str,
        computer_name: str,
        install_date: datetime,
    ) -> None:
        """Build and write all Windows Update artifacts.

        Performs registry writes first, then EVTX event writes.

        Args:
            profile_type:  One of ``"home"``, ``"office"``, ``"developer"``.
            computer_name: VM computer name.
            install_date:  UTC datetime of simulated Windows install.

        Raises:
            UpdateArtifactsError: On any write failure.
        """
        if install_date.tzinfo is None:
            install_date = install_date.replace(tzinfo=timezone.utc)

        updates = self._select_updates(profile_type, computer_name)
        reg_ops = self.build_registry_operations(
            computer_name, install_date, updates
        )
        evtx_records = self.build_evtx_records(
            computer_name, install_date, updates
        )

        # Registry writes
        try:
            self._hive_writer.execute_operations(reg_ops)
        except HiveWriterError as exc:
            raise UpdateArtifactsError(
                f"Registry write failed for update artifacts: {exc}"
            ) from exc

        # EVTX writes (appended to System.evtx)
        try:
            self._evtx_writer.write_records(evtx_records, _SYSTEM_EVTX)
        except EvtxWriterError as exc:
            raise UpdateArtifactsError(
                f"EVTX write failed for update artifacts: {exc}"
            ) from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_update_artifacts",
            "profile_type": profile_type,
            "computer_name": computer_name,
            "kb_count": len(updates),
            "registry_ops": len(reg_ops),
            "evtx_records": len(evtx_records),
        })
        logger.info(
            "Update artifacts written: %d KBs, %d reg ops, %d evtx records",
            len(updates), len(reg_ops), len(evtx_records),
        )

    def build_registry_operations(
        self,
        computer_name: str,
        install_date: datetime,
        updates: List[Dict[str, Any]],
    ) -> List[HiveOperation]:
        """Build Windows Update registry operations.

        Pure function — suitable for isolated testing.

        Args:
            computer_name: VM computer name.
            install_date:  Windows install datetime.
            updates:       Selected KB update records from kb_updates.json.

        Returns:
            List of :class:`HiveOperation`.
        """
        ops: List[HiveOperation] = []

        # Most-recent update timestamp
        if updates:
            last_date = max(
                datetime.strptime(u["date"], "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
                for u in updates
            )
        else:
            last_date = install_date

        last_success = last_date.strftime("%Y-%m-%dT%H:%M:%SZ")

        # AU Results — LastSuccessTime
        ops.append(HiveOperation(
            hive_path=_SOFTWARE_HIVE,
            key_path=_WU_RESULTS_INSTALL,
            value_name="LastSuccessTime",
            value_data=last_success,
            value_type=RegistryValueType.REG_SZ,
        ))
        ops.append(HiveOperation(
            hive_path=_SOFTWARE_HIVE,
            key_path=_WU_RESULTS_DETECT,
            value_name="LastSuccessTime",
            value_data=last_success,
            value_type=RegistryValueType.REG_SZ,
        ))

        # Auto Update options
        ops.append(HiveOperation(
            hive_path=_SOFTWARE_HIVE,
            key_path=_WU_AUTO_UPDATE,
            value_name="AUOptions",
            value_data=_AU_OPTIONS_AUTO,
            value_type=RegistryValueType.REG_DWORD,
        ))

        # WU client identifiers (deterministic per computer name)
        sus_client_id = self._derive_sus_client_id(computer_name)
        ops.append(HiveOperation(
            hive_path=_SOFTWARE_HIVE,
            key_path=_WU_ROOT,
            value_name="SusClientId",
            value_data=sus_client_id,
            value_type=RegistryValueType.REG_SZ,
        ))
        ops.append(HiveOperation(
            hive_path=_SOFTWARE_HIVE,
            key_path=_WU_ROOT,
            value_name="SusClientIDValidation",
            value_data=self._derive_validation_blob(sus_client_id),
            value_type=RegistryValueType.REG_BINARY,
        ))

        return ops

    def build_evtx_records(
        self,
        computer_name: str,
        install_date: datetime,
        updates: List[Dict[str, Any]],
    ) -> List[EvtxRecord]:
        """Build WUA System event log records.

        Pure function — suitable for isolated testing.

        Args:
            computer_name: VM computer name.
            install_date:  Windows install datetime.
            updates:       Selected KB update records.

        Returns:
            Ordered list of :class:`EvtxRecord`.
        """
        if install_date.tzinfo is None:
            install_date = install_date.replace(tzinfo=timezone.utc)

        rng = Random(hash(computer_name))
        records: List[EvtxRecord] = []

        for update in updates:
            kb = update["kb"]
            title = update["title"]
            update_date = datetime.strptime(
                update["date"], "%Y-%m-%d"
            ).replace(tzinfo=timezone.utc)

            # Download phase
            dl_start = update_date.replace(
                hour=rng.randint(1, 4),
                minute=rng.randint(0, 59),
            )
            dl_done = dl_start + timedelta(
                minutes=rng.randint(2, 15)
            )
            # Install phase
            inst_start = dl_done + timedelta(
                minutes=rng.randint(1, 5)
            )
            inst_done = inst_start + timedelta(
                minutes=rng.randint(5, 30)
            )

            records.append(self._make_download_start(
                computer_name, kb, title, dl_start
            ))
            records.append(self._make_download_done(
                computer_name, kb, title, dl_done
            ))
            records.append(self._make_install_start(
                computer_name, kb, title, inst_start
            ))
            records.append(self._make_install_done(
                computer_name, kb, title, inst_done
            ))

        # Sort chronologically
        records.sort(key=lambda r: r.timestamp)
        return records

    # -- private helpers ----------------------------------------------------

    def _select_updates(
        self,
        profile_type: str,
        computer_name: str,
    ) -> List[Dict[str, Any]]:
        """Select a deterministic subset of KB updates for this profile.

        Args:
            profile_type:  Profile type string.
            computer_name: Used as RNG seed for reproducibility.

        Returns:
            Subset of update entries from kb_updates.json.
        """
        count = _UPDATES_BY_PROFILE.get(profile_type, 10)
        rng = Random(hash(computer_name + profile_type))
        available = list(self._kb_data)
        rng.shuffle(available)
        return available[:min(count, len(available))]

    def _load_kb_data(self) -> List[Dict[str, Any]]:
        """Load and validate kb_updates.json.

        Returns:
            List of update entry dicts.

        Raises:
            UpdateArtifactsError: If file is missing or malformed.
        """
        path = self._data_dir / _KB_DATA_FILE
        if not path.is_file():
            raise UpdateArtifactsError(
                f"KB update data file not found: {path}"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise UpdateArtifactsError(
                f"Failed to parse {_KB_DATA_FILE}: {exc}"
            ) from exc
        if "updates" not in data or not isinstance(data["updates"], list):
            raise UpdateArtifactsError(
                f"{_KB_DATA_FILE} must contain an 'updates' list"
            )
        return data["updates"]

    @staticmethod
    def _derive_sus_client_id(computer_name: str) -> str:
        """Derive a deterministic SUS client GUID from computer name."""
        import hashlib
        d = hashlib.sha256(
            (computer_name + ":sus").encode("utf-8")
        ).hexdigest()
        return (
            f"{{{d[:8]}-{d[8:12]}-{d[12:16]}-{d[16:20]}-{d[20:32]}}}"
        )

    @staticmethod
    def _derive_validation_blob(sus_id: str) -> bytes:
        """Derive a 28-byte binary validation blob from the SUS client ID."""
        import hashlib
        return hashlib.sha256(sus_id.encode("utf-8")).digest()[:28]

    # -- EvtxRecord builders ------------------------------------------------

    def _make_download_start(
        self,
        computer: str,
        kb: str,
        title: str,
        ts: datetime,
    ) -> EvtxRecord:
        """EID 19 — Update download started."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_DOWNLOAD_START,
            level=4,
            provider=_PROVIDER_WUA,
            computer=computer,
            timestamp=ts,
            event_data={
                "updateTitle": title,
                "updateGuid": f"{{{_kb_to_guid(kb)}}}",
            },
            keywords="0x8000000000000000",
            task=1,
            opcode=0,
        )

    def _make_download_done(
        self,
        computer: str,
        kb: str,
        title: str,
        ts: datetime,
    ) -> EvtxRecord:
        """EID 20 — Update download completed."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_DOWNLOAD_DONE,
            level=4,
            provider=_PROVIDER_WUA,
            computer=computer,
            timestamp=ts,
            event_data={
                "updateTitle": title,
                "updateGuid": f"{{{_kb_to_guid(kb)}}}",
                "errorCode": "0x0",
            },
            keywords="0x8000000000000000",
            task=1,
            opcode=0,
        )

    def _make_install_start(
        self,
        computer: str,
        kb: str,
        title: str,
        ts: datetime,
    ) -> EvtxRecord:
        """EID 43 — Update install started."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_INSTALL_START,
            level=4,
            provider=_PROVIDER_WUA,
            computer=computer,
            timestamp=ts,
            event_data={
                "updateTitle": title,
                "updateGuid": f"{{{_kb_to_guid(kb)}}}",
            },
            keywords="0x8000000000000000",
            task=2,
            opcode=0,
        )

    def _make_install_done(
        self,
        computer: str,
        kb: str,
        title: str,
        ts: datetime,
    ) -> EvtxRecord:
        """EID 44 — Update install completed successfully."""
        return EvtxRecord(
            channel=_CHANNEL,
            event_id=_EID_INSTALL_DONE,
            level=4,
            provider=_PROVIDER_WUA,
            computer=computer,
            timestamp=ts,
            event_data={
                "updateTitle": title,
                "updateGuid": f"{{{_kb_to_guid(kb)}}}",
                "errorCode": "0x0",
                "updateRevisionNumber": "201",
            },
            keywords="0x8000000000000000",
            task=2,
            opcode=0,
        )


# ---------------------------------------------------------------------------
# Module helper
# ---------------------------------------------------------------------------

def _kb_to_guid(kb: str) -> str:
    """Derive a deterministic GUID from a KB number string."""
    import hashlib
    d = hashlib.md5(kb.encode("utf-8")).hexdigest()
    return f"{d[:8]}-{d[8:12]}-{d[12:16]}-{d[16:20]}-{d[20:32]}"
