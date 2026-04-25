"""Office Recent MRU registry service.

Writes Microsoft Office Most-Recently-Used file lists into NTUSER.DAT so
forensic tools see a realistic document-access history for the persona.

Registry paths written
----------------------
``NTUSER.DAT`` hive:
* ``Software\\Microsoft\\Office\\16.0\\Word\\User MRU\\LiveId_*\\File MRU``
* ``Software\\Microsoft\\Office\\16.0\\Excel\\User MRU\\LiveId_*\\File MRU``
* ``Software\\Microsoft\\Office\\16.0\\PowerPoint\\User MRU\\LiveId_*\\File MRU``

Each ``File MRU`` key contains REG_SZ values named ``Item 1`` … ``Item N``
with the path encoded in the Office MRU format:
    ``[F00000000][T<FILETIME_HEX>][O00000000]*<drive>:\\path\\to\\file.ext``
"""

from __future__ import annotations

import logging
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional, Tuple

from services.base_service import BaseService
from services.registry.hive_writer import (
    HiveOperation,
    HiveWriter,
    HiveWriterError,
    RegistryValueType,
)

logger = logging.getLogger(__name__)

# NTUSER.DAT relative path
_NTUSER_HIVE: str = "Users/{username}/NTUSER.DAT"

# Office MRU key base
_OFFICE_MRU_BASE: str = r"Software\Microsoft\Office\16.0\{app}\User MRU\LiveId_0\File MRU"

# FILETIME epoch
_FILETIME_EPOCH: int = 116_444_736_000_000_000

# App → typical file extensions + subfolders
_APP_CONFIG: Dict[str, Dict[str, Any]] = {
    "Word": {
        "exts": [".docx", ".doc", ".docm"],
        "folders": ["Documents", "Documents\\Reports", "Documents\\Proposals", "Desktop"],
        "count_range": (10, 30),
    },
    "Excel": {
        "exts": [".xlsx", ".xls", ".xlsm"],
        "folders": ["Documents", "Documents\\Finance", "Documents\\Data", "Desktop"],
        "count_range": (8, 25),
    },
    "PowerPoint": {
        "exts": [".pptx", ".ppt"],
        "folders": ["Documents", "Documents\\Presentations", "Desktop"],
        "count_range": (5, 15),
    },
}

_DOC_NAMES: List[str] = [
    "Q4_Report", "Budget_FY2025", "Meeting_Notes", "Project_Timeline",
    "Annual_Review", "Client_Proposal", "Expense_Report", "Team_Update",
    "Strategy_2025", "Sprint_Review", "Weekly_Status", "Roadmap",
    "Sales_Dashboard", "Product_Spec", "Design_Doc", "Requirements",
    "Retrospective", "Action_Items", "Board_Presentation", "Risk_Register",
    "KPI_Dashboard", "Customer_Analysis", "Market_Research", "Competitive_Review",
]


class OfficeMruError(Exception):
    """Raised when Office MRU generation fails."""


class OfficeMruService(BaseService):
    """Writes Microsoft Office 16.0 MRU file lists into NTUSER.DAT.

    Generates a realistic set of recently-opened Office documents for each
    Office application, driven by the persona's document activity.

    Args:
        hive_writer:  Low-level registry hive writer.
        audit_logger: Shared audit logger.
    """

    def __init__(self, hive_writer: HiveWriter, audit_logger: Any) -> None:
        self._hive_writer = hive_writer
        self._audit_logger = audit_logger

    @property
    def service_name(self) -> str:
        return "OfficeMruService"

    def apply(self, ctx: "ServiceContext") -> None:
        """Write Office MRU entries driven by scheduler FILE_CREATE events.

        Raises:
            OfficeMruError: On write failure.
        """
        username = ctx.identity_bundle.user.username
        hive_path = _NTUSER_HIVE.format(username=username)

        rng = ctx.scheduler.child_rng("OfficeMruService") if ctx.scheduler else Random(
            hash(username + "office_mru")
        )

        # Collect document paths from expansion bundle (preferred) or generate
        if ctx.expansion and ctx.expansion.documents:
            doc_paths = [
                str(d.relative_path)
                for d in ctx.expansion.documents
                if Path(str(d.relative_path)).suffix.lower()
                in (".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt")
            ]
        else:
            doc_paths = []

        from core.time_utils import sched_now
        now = sched_now(ctx)

        try:
            ops = self.build_operations(username, hive_path, doc_paths, rng, now)
            self._hive_writer.execute_operations(ops)
        except HiveWriterError as exc:
            raise OfficeMruError(f"Office MRU write failed: {exc}") from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_office_mru",
            "username": username,
            "operations_count": len(ops),
        })
        logger.info("Office MRU written: %d operations for %s", len(ops), username)

    def build_operations(
        self,
        username: str,
        hive_path: str,
        existing_doc_paths: List[str],
        rng: Random,
        now: datetime,
    ) -> List[HiveOperation]:
        """Build all Office MRU registry operations without writing.

        Pure function — suitable for isolated testing.
        """
        ops: List[HiveOperation] = []
        drive_root = f"C:\\Users\\{username}\\"

        for app, cfg in _APP_CONFIG.items():
            key_path = _OFFICE_MRU_BASE.format(app=app)
            count = rng.randint(*cfg["count_range"])

            # Filter existing paths to this app's extensions
            app_exts = set(cfg["exts"])
            app_paths = [
                p for p in existing_doc_paths
                if Path(p).suffix.lower() in app_exts
            ]

            # Top up with generated names if needed
            while len(app_paths) < count:
                folder = rng.choice(cfg["folders"])
                name = rng.choice(_DOC_NAMES)
                ext = rng.choice(cfg["exts"])
                win_path = f"C:\\Users\\{username}\\{folder}\\{name}{ext}"
                app_paths.append(win_path)

            rng.shuffle(app_paths)
            selected = app_paths[:count]

            for i, path in enumerate(selected, start=1):
                # Last-accessed time: staggered back from now
                last_access = now - timedelta(hours=rng.randint(1, 720))
                ft = _to_filetime(last_access)
                ft_hex = f"{ft:016X}"

                mru_value = f"[F00000000][T{ft_hex}][O00000000]*{path}"
                ops.append(
                    HiveOperation(
                        hive_path=hive_path,
                        key_path=key_path,
                        value_name=f"Item {i}",
                        value_data=mru_value,
                        value_type=RegistryValueType.REG_SZ,
                    )
                )

            # MRUList value: "Item 1\0Item 2\0..."
            mru_list = [f"Item {i}" for i in range(1, len(selected) + 1)]
            ops.append(
                HiveOperation(
                    hive_path=hive_path,
                    key_path=key_path,
                    value_name="MRUList",
                    value_data="\x00".join(mru_list),
                    value_type=RegistryValueType.REG_SZ,
                )
            )

        return ops


def _to_filetime(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    ticks = int((dt - epoch).total_seconds() * 1e7)
    return ticks + _FILETIME_EPOCH
