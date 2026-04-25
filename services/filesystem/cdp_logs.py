"""ConnectedDevicesPlatform (CDP) log stub generator.

Creates synthetic CDP log files in:
    ``%LOCALAPPDATA%\\Microsoft\\Windows\\ConnectedDevicesPlatform\\L.<username>\\``

CDP logs record cross-device activity (phone link, nearby sharing, clipboard
sync) and are a valuable forensic artifact that a machine with no such files
looks suspiciously unused.

Files generated
---------------
* ``ActivitiesCache.db`` — SQLite database with activity records
* ``AccountName`` — plain-text username file
* ``connected_devices.json`` — JSON-shaped device pairing record

The SQLite schema matches the CDP ``Activities`` table that Windows 10/11
creates.  Forensic tools (WxTCmd, KAPE) parse this table.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from typing import Any, List

from services.base_service import BaseService

logger = logging.getLogger(__name__)

_CDP_REL: str = (
    "Users/{username}/AppData/Local/Microsoft/Windows/ConnectedDevicesPlatform/L.{username}"
)

# Activities table schema — matches Windows CDP ActivitiesCache.db
_CDP_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS Activity (
    [Id] TEXT PRIMARY KEY,
    [AppId] TEXT,
    [PackageName] TEXT,
    [ActivityType] INTEGER,
    [ActivityStatus] INTEGER DEFAULT 0,
    [ParentActivityId] TEXT,
    [Tag] TEXT,
    [Group] TEXT,
    [MatchId] TEXT,
    [LastModifiedTime] INTEGER,
    [ExpirationTime] INTEGER,
    [Payload] BLOB,
    [Priority] INTEGER DEFAULT 0,
    [IsLocalOnly] INTEGER DEFAULT 0,
    [PlatformDeviceId] TEXT,
    [CreatedInCloud] INTEGER DEFAULT 0,
    [StartTime] INTEGER,
    [EndTime] INTEGER,
    [LastModifiedOnClient] INTEGER DEFAULT 0,
    [GroupAppActivityId] TEXT,
    [ClipboardPayload] BLOB,
    [EnterpriseId] TEXT,
    [OriginalPayload] BLOB,
    [OriginalLastModifiedOnClient] INTEGER,
    [ETag] TEXT
);
CREATE TABLE IF NOT EXISTS ActivityOperation (
    [OperationOrder] INTEGER PRIMARY KEY,
    [Id] TEXT,
    [OperationType] INTEGER,
    [AppId] TEXT,
    [PackageName] TEXT,
    [ActivityType] INTEGER,
    [ParentActivityId] TEXT,
    [Tag] TEXT,
    [Group] TEXT,
    [MatchId] TEXT,
    [LastModifiedTime] INTEGER,
    [ExpirationTime] INTEGER,
    [Payload] BLOB,
    [Priority] INTEGER DEFAULT 0,
    [IsLocalOnly] INTEGER DEFAULT 0,
    [PlatformDeviceId] TEXT,
    [CreatedInCloud] INTEGER DEFAULT 0,
    [StartTime] INTEGER,
    [EndTime] INTEGER,
    [LastModifiedOnClient] INTEGER DEFAULT 0,
    [GroupAppActivityId] TEXT,
    [ClipboardPayload] BLOB,
    [OriginalPayload] BLOB,
    [OriginalLastModifiedOnClient] INTEGER,
    [ETag] TEXT
);
"""

# Common Windows app PackageIds
_APP_IDS: List[str] = [
    "{windows.immersivecontrolpanel_cw5n1h2txyewy}",
    "{windows.lockapp_cw5n1h2txyewy}",
    "Microsoft.MicrosoftEdge.Stable_8wekyb3d8bbwe",
    "Microsoft.Windows.Photos_8wekyb3d8bbwe",
    "Microsoft.OutlookForWindows_8wekyb3d8bbwe",
    "Microsoft.OneDrive_8wekyb3d8bbwe",
    "Microsoft.WindowsNotepad_8wekyb3d8bbwe",
    "Microsoft.MSPaint_8wekyb3d8bbwe",
    "Microsoft.ScreenSketch_8wekyb3d8bbwe",
    "Microsoft.Windows.ContentDeliveryManager_cw5n1h2txyewy",
]

# FILETIME epoch
_FILETIME_EPOCH: int = 116_444_736_000_000_000


def _dt_to_filetime(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return int((dt - epoch).total_seconds() * 1e7) + _FILETIME_EPOCH


class CdpLogsError(Exception):
    """Raised when CDP log generation fails."""


class CdpLogsService(BaseService):
    """Generates ConnectedDevicesPlatform logs with synthetic activity records.

    Args:
        mount_manager: Resolves paths against the mounted image root.
        audit_logger:  Shared audit logger.
    """

    def __init__(self, mount_manager: Any, audit_logger: Any) -> None:
        self._mount = mount_manager
        self._audit_logger = audit_logger

    @property
    def service_name(self) -> str:
        return "CdpLogsService"

    def apply(self, ctx: "ServiceContext") -> None:
        """Generate CDP log artifacts.

        Raises:
            CdpLogsError: On write failure.
        """
        username = ctx.identity_bundle.user.username
        timeline_days = ctx.persona.timeline_days

        rng = (
            ctx.scheduler.child_rng("CdpLogs")
            if ctx.scheduler
            else Random(hash(username + "cdp_logs"))
        )

        cdp_dir_rel = _CDP_REL.format(username=username)

        try:
            cdp_dir = self._mount.resolve(cdp_dir_rel)
            cdp_dir.mkdir(parents=True, exist_ok=True)

            # AccountName file
            (cdp_dir / "AccountName").write_text(username, encoding="utf-8")

            from core.time_utils import sched_now as _sched_now
            sched_now = _sched_now(ctx)

            # connected_devices.json
            devices = self._gen_device_list(rng, sched_now)
            (cdp_dir / "connected_devices.json").write_text(
                json.dumps(devices, indent=2), encoding="utf-8"
            )

            # ActivitiesCache.db
            db_path = cdp_dir / "ActivitiesCache.db"
            record_count = self._build_activities_db(db_path, username, timeline_days, rng, sched_now)

        except Exception as exc:
            raise CdpLogsError(f"Failed to generate CDP logs: {exc}") from exc

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "write_cdp_logs",
            "username": username,
            "cdp_dir": cdp_dir_rel,
        })
        logger.info("CDP logs written for %s", username)

    # -- builders -----------------------------------------------------------

    def _build_activities_db(
        self,
        db_path: Path,
        username: str,
        timeline_days: int,
        rng: Random,
        now: datetime | None = None,
    ) -> int:
        """Build ActivitiesCache.db with synthetic activity rows."""
        if now is None:
            raise ValueError("_build_activities_db: 'now' must be provided (use sched_now(ctx))")
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(_CDP_SCHEMA)
            start = now - timedelta(days=timeline_days)

            record_count = rng.randint(
                min(200, timeline_days * 2),
                min(2000, timeline_days * 8),
            )
            for i in range(record_count):
                activity_ts = start + timedelta(
                    seconds=rng.randint(0, int((now - start).total_seconds()))
                )
                ft_start = _dt_to_filetime(activity_ts)
                ft_end = ft_start + rng.randint(60 * 10**7, 3600 * 10**7)
                ft_mod = ft_end + rng.randint(0, 60 * 10**7)
                ft_exp = ft_mod + 30 * 86400 * 10**7  # 30 days expiry

                app_id = rng.choice(_APP_IDS)
                activity_type = rng.choice([5, 6, 11, 12, 16])  # common CDP types
                activity_id = f"{rng.randint(0, 2**32):08X}-{rng.randint(0, 2**16):04X}-" \
                              f"{rng.randint(0, 2**16):04X}-{rng.randint(0, 2**16):04X}-" \
                              f"{rng.randint(0, 2**48):012X}"

                conn.execute(
                    "INSERT OR IGNORE INTO Activity "
                    "(Id, AppId, PackageName, ActivityType, ActivityStatus, "
                    "LastModifiedTime, ExpirationTime, StartTime, EndTime, IsLocalOnly) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        activity_id, app_id, app_id, activity_type, 0,
                        ft_mod, ft_exp, ft_start, ft_end, 1,
                    ),
                )

            conn.commit()
        finally:
            conn.close()

        return record_count

    @staticmethod
    def _gen_device_list(rng: Random, now: datetime | None = None) -> list:
        """Generate a plausible connected-devices JSON structure."""
        device_types = ["Phone", "Laptop", "Desktop", "Tablet"]
        device_count = rng.randint(1, 3)
        devices = []
        for i in range(device_count):
            mac = ":".join(f"{rng.randint(0, 255):02X}" for _ in range(6))
            devices.append({
                "deviceId": f"CDP_{rng.randint(10**15, 10**16 - 1)}",
                "deviceName": f"{rng.choice(device_types)}-{rng.randint(1000, 9999)}",
                "platform": rng.choice(["Windows", "Android", "iOS"]),
                "mac": mac,
                "lastSeen": (
                    now - timedelta(hours=rng.randint(0, 720))
                ).isoformat(),
            })
        return devices
