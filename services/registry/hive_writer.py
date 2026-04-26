"""Offline Windows registry hive writer.

Provides typed, auditable read/write operations on offline registry hive files
(NTUSER.DAT, SOFTWARE, SYSTEM) using the hivex library.

Every mutation is recorded through the injected AuditLogger.

Design
------
* ``HiveWriter`` is the **only** module that imports ``hivex`` or touches hive
  binary data.  All higher-level registry services build ``HiveOperation``
  lists and pass them to ``HiveWriter.execute_operations()``.
* ``HiveOperation`` and ``RegistryValueType`` are the stable public API —
  callers should never import hivex directly.
"""

from __future__ import annotations

import enum
import logging
import struct
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Union

from pydantic import BaseModel, field_validator

from services.base_service import BaseService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# hivex — primary backend
# ---------------------------------------------------------------------------

try:
    import hivex as _hivex
    _HAS_HIVEX = True
except ImportError:
    _HAS_HIVEX = False

# ---------------------------------------------------------------------------
# regipy — kept for read-only operations (key_exists, read_value)
# ---------------------------------------------------------------------------

try:
    from regipy.exceptions import (
        RegistryKeyNotFoundException,
        RegistryValueNotFoundException,
    )
    from regipy.registry import RegistryHive
    _HAS_REGIPY = True
except ImportError:
    _HAS_REGIPY = False


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RegistryValueType(str, enum.Enum):
    """Supported registry value types for write operations."""

    REG_SZ = "REG_SZ"
    REG_EXPAND_SZ = "REG_EXPAND_SZ"
    REG_BINARY = "REG_BINARY"
    REG_DWORD = "REG_DWORD"
    REG_QWORD = "REG_QWORD"
    REG_MULTI_SZ = "REG_MULTI_SZ"
    REG_NONE = "REG_NONE"


# Maps RegistryValueType → hivex integer constant
_TYPE_TO_HIVEX: Dict[RegistryValueType, int] = {
    RegistryValueType.REG_NONE:       0,
    RegistryValueType.REG_SZ:         1,
    RegistryValueType.REG_EXPAND_SZ:  2,
    RegistryValueType.REG_BINARY:     3,
    RegistryValueType.REG_DWORD:      4,
    RegistryValueType.REG_QWORD:     11,
    RegistryValueType.REG_MULTI_SZ:   7,
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class HiveWriterError(Exception):
    """Raised on hive I/O, validation, or structural errors."""


# ---------------------------------------------------------------------------
# Pydantic operation model
# ---------------------------------------------------------------------------

class HiveOperation(BaseModel):
    """Single atomic registry operation.

    Every higher-level service builds a list of these and passes them
    to HiveWriter.execute_operations().

    Attributes:
        hive_path: Relative path from mount root to the hive file.
        key_path: Full registry key path within the hive.
        value_name: Name of the value to set/delete.  Use ``"(default)"``
            for the default value.
        value_data: Data to write.  Type must match *value_type*.
        value_type: One of the RegistryValueType members.
        operation: ``"set"`` to create/overwrite; ``"delete_value"`` to
            remove a single value; ``"delete_key"`` to remove an entire key.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    hive_path: str
    key_path: str
    value_name: str = "(default)"
    value_data: Union[str, int, bytes, List[str], None] = None
    value_type: RegistryValueType = RegistryValueType.REG_SZ
    operation: Literal["set", "delete_value", "delete_key"] = "set"

    @field_validator("hive_path")
    @classmethod
    def _hive_path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("hive_path must not be empty")
        return v

    @field_validator("key_path")
    @classmethod
    def _key_path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("key_path must not be empty")
        return v


# ---------------------------------------------------------------------------
# HiveWriter
# ---------------------------------------------------------------------------

class HiveWriter(BaseService):
    """Offline registry hive read/write service backed by hivex.

    Args:
        mount_manager: Resolves paths relative to the mounted image root.
        audit_logger: Shared audit logger for traceability.
    """

    def __init__(self, mount_manager: Any, audit_logger: Any) -> None:
        if not _HAS_HIVEX:
            raise HiveWriterError(
                "hivex is required but not installed. "
                "Run: apt install libhivex-bin python3-hivex"
            )
        self._mount_manager = mount_manager
        self._audit_logger = audit_logger

    @property
    def service_name(self) -> str:
        return "HiveWriter"

    def apply(self, ctx: "ServiceContext") -> None:
        pass  # operations are submitted via execute_operations()

    # -- public API ---------------------------------------------------------

    def preflight_hive_logs(self, hive_rel_paths: Sequence[str]) -> None:
        """Pre-flight check: verify .LOG1 / .LOG2 companion files are deletable.

        Windows replays the transaction log on next mount if the log files
        survive, rolling back all hivex writes (R28, ADR-010).  This check
        must be called before any hive is opened for writing.

        Args:
            hive_rel_paths: Relative paths to the hives that will be written.

        Raises:
            HiveWriterError: If any log file exists but cannot be deleted,
                indicating a permissions problem that would cause a boot loop.
        """
        for rel in hive_rel_paths:
            try:
                hive_abs = self._resolve_hive_path(rel)
            except HiveWriterError:
                # Hive may not exist yet (creation path) — that's fine.
                continue
            for suffix in (".LOG1", ".LOG2"):
                log_path = hive_abs.parent / (hive_abs.name + suffix)
                if not log_path.exists():
                    continue
                # Probe: can we delete it? Try rename-then-rename-back.
                probe = log_path.with_suffix(".LOG_probe")
                try:
                    log_path.rename(probe)
                    probe.rename(log_path)
                except OSError as exc:
                    self._audit_logger.log({
                        "service": self.service_name,
                        "operation": "preflight_log_check_failed",
                        "log_path": str(log_path),
                        "error": str(exc),
                    })
                    raise HiveWriterError(
                        f"Cannot delete hive log {log_path}: {exc}. "
                        "This will cause Windows to replay the log on next mount, "
                        "rolling back all hive writes (R28). Aborting."
                    ) from exc
        logger.debug("Preflight hive-log check passed for %d hive(s)", len(list(hive_rel_paths)))

    def execute_operations(self, operations: Sequence[HiveOperation]) -> None:
        """Execute a batch of registry operations grouped by hive file.

        Args:
            operations: Sequence of HiveOperation to execute.

        Raises:
            HiveWriterError: On path-escape, missing hive, or write failure.
        """
        if not operations:
            return
        grouped: Dict[str, List[HiveOperation]] = {}
        for op in operations:
            grouped.setdefault(op.hive_path, []).append(op)
        for hive_rel_path, ops in grouped.items():
            hive_abs = self._resolve_hive_path(hive_rel_path)
            self._apply_operations_to_hive(hive_abs, ops)

    def read_value(self, hive_rel_path: str, key_path: str, value_name: str) -> Any:
        """Read a single value from an offline hive.

        Falls back to regipy for reads (hivex read API is more verbose).

        Raises:
            HiveWriterError: If the hive, key, or value is not found.
        """
        hive_abs = self._resolve_hive_path(hive_rel_path)
        return self._read_value_hivex(hive_abs, key_path, value_name)

    def key_exists(self, hive_rel_path: str, key_path: str) -> bool:
        """Return True if *key_path* exists in the hive."""
        hive_abs = self._resolve_hive_path(hive_rel_path)
        try:
            h = _hivex.Hivex(str(hive_abs))
            self._navigate_to_node(h, key_path)
            return True
        except Exception:
            return False

    # -- path resolution ----------------------------------------------------

    def _resolve_hive_path(self, hive_rel_path: str) -> Path:
        try:
            hive_abs = self._mount_manager.resolve(hive_rel_path)
        except ValueError as exc:
            raise HiveWriterError(
                f"Path escape detected for hive: {hive_rel_path}"
            ) from exc
        if not hive_abs.is_file():
            raise HiveWriterError(f"Hive file not found: {hive_abs}")
        return hive_abs

    # -- read helpers -------------------------------------------------------

    def _read_value_hivex(
        self, hive_abs: Path, key_path: str, value_name: str
    ) -> Any:
        try:
            h = _hivex.Hivex(str(hive_abs))
            node = self._navigate_to_node(h, key_path)
        except Exception as exc:
            raise HiveWriterError(
                f"Key not found in {hive_abs.name}: {key_path}"
            ) from exc
        for val in h.node_values(node):
            if h.value_key(val) == value_name:
                typ, _ = h.value_type(val)
                raw = h.value_value(val)
                return self._decode_raw(typ, raw)
        raise HiveWriterError(
            f"Value '{value_name}' not found at {key_path} in {hive_abs.name}"
        )

    @staticmethod
    def _decode_raw(typ: int, raw: bytes) -> Any:
        if typ in (1, 2):  # REG_SZ, REG_EXPAND_SZ
            return raw.rstrip(b"\x00").decode("utf-16-le", errors="replace")
        if typ == 4:  # REG_DWORD
            return struct.unpack_from("<I", raw)[0] if len(raw) >= 4 else 0
        if typ == 11:  # REG_QWORD
            return struct.unpack_from("<Q", raw)[0] if len(raw) >= 8 else 0
        if typ == 7:  # REG_MULTI_SZ
            text = raw.rstrip(b"\x00").decode("utf-16-le", errors="replace")
            return [s for s in text.split("\x00") if s]
        return raw  # REG_BINARY, REG_NONE, etc.

    # -- write engine -------------------------------------------------------

    def _apply_operations_to_hive(
        self, hive_abs: Path, operations: List[HiveOperation]
    ) -> None:
        h = _hivex.Hivex(str(hive_abs), write=True)
        try:
            for op in operations:
                try:
                    if op.operation == "set":
                        self._do_set(h, op)
                        self._audit(op, hive_abs, "set_value")
                    elif op.operation == "delete_value":
                        self._do_delete_value(h, op)
                        self._audit(op, hive_abs, "delete_value")
                    elif op.operation == "delete_key":
                        self._do_delete_key(h, op)
                        self._audit(op, hive_abs, "delete_key")
                except HiveWriterError:
                    raise
                except Exception as exc:
                    raise HiveWriterError(
                        f"Operation failed [{op.operation}] "
                        f"{op.key_path}\\{op.value_name}: {exc}"
                    ) from exc
            h.commit(None)
        except Exception:
            raise
        finally:
            try:
                h.close()
            except Exception:
                pass

        # ADR-010: delete .LOG1 / .LOG2 to prevent Windows replay (A19)
        for suffix in (".LOG1", ".LOG2"):
            log_path = hive_abs.parent / (hive_abs.name + suffix)
            if log_path.exists():
                try:
                    log_path.unlink()
                    logger.debug("Deleted hive log: %s", log_path)
                except OSError as exc:
                    logger.warning("Could not delete hive log %s: %s", log_path, exc)
                    self._audit_logger.log({
                        "service": self.service_name,
                        "operation": "hive_log_deletion_failed",
                        "log_path": str(log_path),
                        "error": str(exc),
                        "risk": "R28: Windows may replay log on next mount, rolling back hive writes",
                    })

        logger.info("Applied %d operation(s) to %s", len(operations), hive_abs.name)

    def _do_set(self, h: Any, op: HiveOperation) -> None:
        node = self._get_or_create_node(h, op.key_path)
        encoded = self._encode(op.value_data, op.value_type)
        typ = _TYPE_TO_HIVEX[op.value_type]
        h.node_set_value(node, {
            "key": op.value_name,
            "t": typ,
            "value": encoded,
        })

    def _do_delete_value(self, h: Any, op: HiveOperation) -> None:
        try:
            node = self._navigate_to_node(h, op.key_path)
        except Exception as exc:
            raise HiveWriterError(
                f"Key not found for delete_value: {op.key_path}"
            ) from exc
        for val in h.node_values(node):
            if h.value_key(val) == op.value_name:
                h.node_delete_child(node, val)
                return
        logger.debug(
            "delete_value: value '%s' not found at %s (no-op)",
            op.value_name, op.key_path,
        )

    def _do_delete_key(self, h: Any, op: HiveOperation) -> None:
        parts = [p for p in op.key_path.replace("/", "\\").split("\\") if p]
        if not parts:
            raise HiveWriterError("Cannot delete root key")
        parent_path = "\\".join(parts[:-1])
        child_name = parts[-1]
        try:
            parent = self._navigate_to_node(h, parent_path) if parent_path else h.root()
        except Exception as exc:
            raise HiveWriterError(
                f"Parent key not found for delete_key: {parent_path}"
            ) from exc
        child = h.node_get_child(parent, child_name)
        if child is None:
            logger.debug(
                "delete_key: key '%s' not found under %s (no-op)",
                child_name, parent_path,
            )
            return
        h.node_delete_child(parent, child)

    # -- navigation helpers -------------------------------------------------

    @staticmethod
    def _navigate_to_node(h: Any, key_path: str) -> Any:
        """Walk key_path from root, returning the leaf node handle.

        Raises:
            Exception: If any segment is not found (caller converts to HiveWriterError).
        """
        parts = [p for p in key_path.replace("/", "\\").split("\\") if p]
        node = h.root()
        for part in parts:
            child = h.node_get_child(node, part)
            if child is None:
                raise KeyError(f"Key segment not found: {part!r} in {key_path!r}")
            node = child
        return node

    @staticmethod
    def _get_or_create_node(h: Any, key_path: str) -> Any:
        """Walk or create every segment of key_path, returning the leaf node."""
        parts = [p for p in key_path.replace("/", "\\").split("\\") if p]
        node = h.root()
        for part in parts:
            child = h.node_get_child(node, part)
            if child is None:
                child = h.node_add_child(node, part)
            node = child
        return node

    # -- encoding helpers ---------------------------------------------------

    @staticmethod
    def _encode_value_data(
        data: Union[str, int, bytes, List[str], None],
        value_type: RegistryValueType,
    ) -> bytes:
        """Alias for _encode — kept for test compatibility."""
        return HiveWriter._encode(data, value_type)

    @staticmethod
    def _encode(
        data: Union[str, int, bytes, List[str], None],
        value_type: RegistryValueType,
    ) -> bytes:
        if data is None:
            return b""
        if value_type in (RegistryValueType.REG_SZ, RegistryValueType.REG_EXPAND_SZ):
            if not isinstance(data, str):
                raise HiveWriterError(
                    f"REG_SZ/REG_EXPAND_SZ requires str, got {type(data).__name__}"
                )
            return data.encode("utf-16-le") + b"\x00\x00"
        if value_type == RegistryValueType.REG_DWORD:
            if not isinstance(data, int):
                raise HiveWriterError(
                    f"REG_DWORD requires int, got {type(data).__name__}"
                )
            return struct.pack("<I", data & 0xFFFFFFFF)
        if value_type == RegistryValueType.REG_QWORD:
            if not isinstance(data, int):
                raise HiveWriterError(
                    f"REG_QWORD requires int, got {type(data).__name__}"
                )
            return struct.pack("<Q", data & 0xFFFFFFFFFFFFFFFF)
        if value_type == RegistryValueType.REG_BINARY:
            if not isinstance(data, bytes):
                raise HiveWriterError(
                    f"REG_BINARY requires bytes, got {type(data).__name__}"
                )
            return data
        if value_type == RegistryValueType.REG_MULTI_SZ:
            if not isinstance(data, list):
                raise HiveWriterError(
                    f"REG_MULTI_SZ requires list[str], got {type(data).__name__}"
                )
            parts = b"".join(
                s.encode("utf-16-le") + b"\x00\x00" for s in data
            )
            return parts + b"\x00\x00"
        return b""

    # -- audit helpers ------------------------------------------------------

    def _audit(
        self,
        op: HiveOperation,
        hive_abs: Path,
        operation_type: str,
    ) -> None:
        self._audit_logger.log({
            "service": self.service_name,
            "operation": operation_type,
            "hive": hive_abs.name,
            "key_path": op.key_path,
            "value_name": op.value_name,
            "value_type": op.value_type.value,
        })
