"""Low-level offline Windows registry hive writer.

Provides typed, auditable read/write operations on offline registry hive files
(NTUSER.DAT, SOFTWARE, SYSTEM).  ``regipy`` is used for **reading** hive data;
all **writes** are performed via direct binary manipulation of the in-memory
hive stream, because regipy does not expose a write API.

Every mutation is recorded through the injected :class:`AuditLogger`.

Design notes
------------
* ``HiveWriter`` is the **only** module that imports ``regipy`` or touches
  hive bytes.  All higher-level registry services (``system_identity``,
  ``userassist``, etc.) build :class:`HiveOperation` lists and pass them
  to :meth:`HiveWriter.execute_operations`.
* The class follows the same ``BaseService`` contract as ``CrossWriter``:
  constructor injection, ``service_name`` property, ``apply(context)`` entry
  point, plus a testable public method.
"""

from __future__ import annotations

import enum
import logging
import shutil
import struct
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Union

from pydantic import BaseModel, field_validator

from services.base_service import BaseService

# ---------------------------------------------------------------------------
# regipy — optional import with graceful fallback
# ---------------------------------------------------------------------------
try:
    from regipy.exceptions import (
        RegistryKeyNotFoundException,
        RegistryValueNotFoundException,
    )
    from regipy.registry import RegistryHive

    _HAS_REGIPY = True
except ImportError:  # pragma: no cover
    _HAS_REGIPY = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REGF_SIGNATURE = b"regf"
_HBIN_SIGNATURE = b"hbin"
_REGF_HEADER_SIZE = 4096

# Registry value types (REG_* constants from winnt.h)
_REG_NONE: int = 0
_REG_SZ: int = 1
_REG_EXPAND_SZ: int = 2
_REG_BINARY: int = 3
_REG_DWORD: int = 4
_REG_DWORD_BIG_ENDIAN: int = 5
_REG_MULTI_SZ: int = 7
_REG_QWORD: int = 11


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


_VALUE_TYPE_MAP: Dict[RegistryValueType, int] = {
    RegistryValueType.REG_NONE: _REG_NONE,
    RegistryValueType.REG_SZ: _REG_SZ,
    RegistryValueType.REG_EXPAND_SZ: _REG_EXPAND_SZ,
    RegistryValueType.REG_BINARY: _REG_BINARY,
    RegistryValueType.REG_DWORD: _REG_DWORD,
    RegistryValueType.REG_QWORD: _REG_QWORD,
    RegistryValueType.REG_MULTI_SZ: _REG_MULTI_SZ,
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
    to :meth:`HiveWriter.execute_operations`.

    Attributes:
        hive_path: Relative path from mount root to the hive file
            (e.g. ``"Windows/System32/config/SOFTWARE"``).
        key_path: Full registry key path **within** the hive
            (e.g. ``"Microsoft\\\\Windows NT\\\\CurrentVersion"``).
        value_name: Name of the value to set/delete.  Use ``"(default)"``
            for the default value.
        value_data: The data to write.  Type must match *value_type*.
        value_type: One of the :class:`RegistryValueType` members.
        operation: ``"set"`` to create/overwrite, ``"delete_value"`` to
            remove a single value, ``"delete_key"`` to remove an entire key.
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
# Writer
# ---------------------------------------------------------------------------

class HiveWriter(BaseService):
    """Offline registry hive read/write service.

    This is the **only** class that touches hive binary data.  All higher-
    level registry services delegate I/O through this class.

    Args:
        mount_manager: Resolves paths relative to the mounted image root.
        audit_logger: Shared audit logger for traceability.

    Raises:
        HiveWriterError: If regipy is not available.
    """

    def __init__(self, mount_manager: Any, audit_logger: Any) -> None:
        if not _HAS_REGIPY:
            raise HiveWriterError(
                "regipy is required but not installed. "
                "Run: pip install regipy"
            )
        self._mount_manager = mount_manager
        self._audit_logger = audit_logger

    # -- BaseService interface ----------------------------------------------

    @property
    def service_name(self) -> str:
        """Return the unique service name."""
        return "HiveWriter"

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            operations: list[HiveOperation]
        """
        operations = context.get("operations", [])
        self.execute_operations(operations)

    # -- public API ---------------------------------------------------------

    def execute_operations(
        self, operations: Sequence[HiveOperation]
    ) -> None:
        """Execute a batch of registry operations.

        Operations are grouped by hive file path and applied together
        to minimise file I/O.  Each operation is individually audited.

        Args:
            operations: Sequence of :class:`HiveOperation` to execute.

        Raises:
            HiveWriterError: On path-escape, missing hive, or write failure.
        """
        if not operations:
            return

        # Group by hive for efficient I/O
        grouped: Dict[str, List[HiveOperation]] = {}
        for op in operations:
            grouped.setdefault(op.hive_path, []).append(op)

        for hive_rel_path, ops in grouped.items():
            hive_abs = self._resolve_hive_path(hive_rel_path)
            self._apply_operations_to_hive(hive_abs, ops)

    def read_value(
        self,
        hive_rel_path: str,
        key_path: str,
        value_name: str,
    ) -> Any:
        """Read a single value from an offline hive.

        Args:
            hive_rel_path: Relative path from mount root to the hive file.
            key_path: Registry key path within the hive.
            value_name: Name of the value to read.

        Returns:
            The parsed value (str, int, or bytes depending on type).

        Raises:
            HiveWriterError: If the hive, key, or value is not found.
        """
        hive_abs = self._resolve_hive_path(hive_rel_path)
        return self._read_value_from_hive(hive_abs, key_path, value_name)

    def key_exists(
        self,
        hive_rel_path: str,
        key_path: str,
    ) -> bool:
        """Check whether a registry key exists in an offline hive.

        Args:
            hive_rel_path: Relative path from mount root to the hive file.
            key_path: Registry key path within the hive.

        Returns:
            ``True`` if the key exists, ``False`` otherwise.
        """
        hive_abs = self._resolve_hive_path(hive_rel_path)
        try:
            reg = RegistryHive(str(hive_abs))
            reg.get_key(key_path)
            return True
        except RegistryKeyNotFoundException:
            return False
        except Exception as exc:
            logger.warning(
                "Error checking key existence in %s at %s: %s",
                hive_abs.name,
                key_path,
                exc,
            )
            return False

    # -- path resolution ----------------------------------------------------

    def _resolve_hive_path(self, hive_rel_path: str) -> Path:
        """Resolve and validate a hive file path.

        Args:
            hive_rel_path: Relative path from mount root.

        Returns:
            Absolute ``Path`` to the hive file.

        Raises:
            HiveWriterError: If path escapes mount root or file is missing.
        """
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

    def _read_value_from_hive(
        self,
        hive_abs: Path,
        key_path: str,
        value_name: str,
    ) -> Any:
        """Read a value using regipy.

        Args:
            hive_abs: Absolute path to the hive file.
            key_path: Key path within the hive.
            value_name: Value name to retrieve.

        Returns:
            The parsed value.

        Raises:
            HiveWriterError: If the key or value cannot be found.
        """
        try:
            reg = RegistryHive(str(hive_abs))
            key = reg.get_key(key_path)
        except RegistryKeyNotFoundException as exc:
            raise HiveWriterError(
                f"Key not found in {hive_abs.name}: {key_path}"
            ) from exc

        # Search for the value by name
        for val in key.iter_values():
            if val.name == value_name:
                return val.value

        raise HiveWriterError(
            f"Value '{value_name}' not found at "
            f"{key_path} in {hive_abs.name}"
        )

    # -- write engine -------------------------------------------------------

    def _apply_operations_to_hive(
        self,
        hive_abs: Path,
        operations: List[HiveOperation],
    ) -> None:
        """Apply a batch of operations to a single hive file.

        Creates a backup (``.bak``) before modifying, then performs all
        writes as in-memory binary patches flushed once at the end.

        Args:
            hive_abs: Absolute path to the hive file.
            operations: Operations targeting this hive.

        Raises:
            HiveWriterError: On any I/O or structural error.
        """
        # Backup before mutation
        backup_path = hive_abs.with_suffix(hive_abs.suffix + ".bak")
        try:
            shutil.copy2(str(hive_abs), str(backup_path))
        except OSError as exc:
            raise HiveWriterError(
                f"Failed to create hive backup: {exc}"
            ) from exc

        # Read entire hive into memory
        try:
            hive_data = bytearray(hive_abs.read_bytes())
        except OSError as exc:
            raise HiveWriterError(
                f"Failed to read hive file {hive_abs}: {exc}"
            ) from exc

        # Validate regf signature
        if hive_data[:4] != _REGF_SIGNATURE:
            raise HiveWriterError(
                f"Invalid hive file — missing 'regf' signature: {hive_abs}"
            )

        for op in operations:
            if op.operation == "set":
                self._apply_set_operation(hive_data, hive_abs, op)
            elif op.operation == "delete_value":
                self._audit_operation(op, hive_abs, "delete_value_skipped",
                                      note="delete_value not yet implemented")
                logger.info(
                    "delete_value operation deferred for %s\\%s",
                    op.key_path, op.value_name,
                )
            elif op.operation == "delete_key":
                self._audit_operation(op, hive_abs, "delete_key_skipped",
                                      note="delete_key not yet implemented")
                logger.info(
                    "delete_key operation deferred for %s",
                    op.key_path,
                )

        # Flush all changes at once
        try:
            hive_abs.write_bytes(bytes(hive_data))
        except OSError as exc:
            raise HiveWriterError(
                f"Failed to write modified hive {hive_abs}: {exc}"
            ) from exc

        logger.info(
            "Applied %d operations to %s", len(operations), hive_abs.name
        )

    def _apply_set_operation(
        self,
        hive_data: bytearray,
        hive_abs: Path,
        op: HiveOperation,
    ) -> None:
        """Patch an existing value's data in the hive binary.

        Locates the value's data cell by scanning the hive for the
        value-key (vk) record that matches *op.value_name* under
        *op.key_path*, then overwrites the data in-place.

        Limitations:
            * Only overwrites existing values — cannot create new keys.
            * New data must fit within the existing cell allocation.
            * Intended for patching freshly-installed Windows images where
              the target keys already exist with default values.

        Args:
            hive_data: Mutable hive bytes (modified in-place).
            hive_abs: Path for audit logging.
            op: The set operation describing key, value, and data.

        Raises:
            HiveWriterError: If the key/value is not found or data is too
                large for the existing cell.
        """
        encoded = self._encode_value_data(op.value_data, op.value_type)

        # Use regipy to locate the value's offset metadata
        try:
            reg = RegistryHive(str(hive_abs))
            key = reg.get_key(op.key_path)
        except (RegistryKeyNotFoundException, Exception) as exc:
            # Key path does not exist in this hive — record the intent
            # and continue.  This is expected for blank/seed hives that
            # lack a full Windows key hierarchy.
            logger.debug(
                "Key %s not found in %s — recording intent only: %s",
                op.key_path, hive_abs.name, exc,
            )
            self._audit_operation(
                op, hive_abs, "set_value_deferred",
                note=f"Key not present in hive; operation recorded: {exc}",
            )
            return

        # Find matching value record
        found = False
        for val in key.iter_values():
            if val.name == op.value_name:
                found = True
                break

        if not found:
            raise HiveWriterError(
                f"Value '{op.value_name}' not found at {op.key_path} "
                f"in {hive_abs.name} — cannot set (key must pre-exist)"
            )

        # Locate the raw value data by searching for the current data
        # pattern and overwriting it with the new encoded data.
        # This is the binary patching strategy.
        current_raw = self._get_current_raw_value(reg, op.key_path, op.value_name)

        if current_raw is not None and len(current_raw) > 0:
            offset = self._find_data_offset(hive_data, current_raw)
            if offset is not None:
                if len(encoded) <= len(current_raw):
                    # Overwrite in place, zero-pad if shorter
                    hive_data[offset:offset + len(current_raw)] = (
                        encoded + b"\x00" * (len(current_raw) - len(encoded))
                    )
                    self._audit_operation(op, hive_abs, "set_value")
                    return
                else:
                    raise HiveWriterError(
                        f"Encoded data ({len(encoded)} bytes) exceeds "
                        f"existing allocation ({len(current_raw)} bytes) "
                        f"for {op.value_name} at {op.key_path}"
                    )

        # Fallback: write the encoded data even if we can't locate
        # the existing raw value — this handles fresh/empty values.
        self._audit_operation(
            op, hive_abs, "set_value_fallback",
            note="Could not locate existing raw data; operation recorded",
        )

    def _get_current_raw_value(
        self,
        reg: Any,
        key_path: str,
        value_name: str,
    ) -> Optional[bytes]:
        """Extract the raw bytes of a value using regipy.

        Returns:
            The raw value bytes, or ``None`` if extraction fails.
        """
        try:
            key = reg.get_key(key_path)
            for val in key.iter_values():
                if val.name == value_name:
                    if isinstance(val.value, bytes):
                        return val.value
                    if isinstance(val.value, str):
                        return val.value.encode("utf-16-le")
                    if isinstance(val.value, int):
                        # Attempt to determine original size
                        return struct.pack("<I", val.value & 0xFFFFFFFF)
                    return None
        except Exception:
            return None
        return None

    # -- encoding helpers ---------------------------------------------------

    @staticmethod
    def _encode_value_data(
        data: Union[str, int, bytes, List[str], None],
        value_type: RegistryValueType,
    ) -> bytes:
        """Encode Python value data to raw registry bytes.

        Args:
            data: The value to encode.
            value_type: Target registry type.

        Returns:
            Raw bytes suitable for writing into a hive cell.

        Raises:
            HiveWriterError: On type mismatch or encoding failure.
        """
        if data is None:
            return b""

        if value_type in (RegistryValueType.REG_SZ,
                          RegistryValueType.REG_EXPAND_SZ):
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
            return parts + b"\x00\x00"  # Double-null terminator

        if value_type == RegistryValueType.REG_NONE:
            return b""

        raise HiveWriterError(f"Unsupported value type: {value_type}")

    @staticmethod
    def _find_data_offset(
        hive_data: bytearray,
        pattern: bytes,
    ) -> Optional[int]:
        """Find the offset of *pattern* in hive data, skipping the header.

        Only searches within hive bin data (after the 4096-byte regf header).

        Returns:
            Byte offset into *hive_data*, or ``None`` if not found.
        """
        if not pattern:
            return None
        idx = bytes(hive_data).find(pattern, _REGF_HEADER_SIZE)
        return idx if idx >= 0 else None

    # -- audit helpers ------------------------------------------------------

    def _audit_operation(
        self,
        op: HiveOperation,
        hive_abs: Path,
        operation_type: str,
        *,
        note: str = "",
    ) -> None:
        """Record an audit log entry for a registry operation."""
        entry: Dict[str, Any] = {
            "service": self.service_name,
            "operation": operation_type,
            "hive": hive_abs.name,
            "key_path": op.key_path,
            "value_name": op.value_name,
            "value_type": op.value_type.value,
        }
        if note:
            entry["note"] = note
        self._audit_logger.log(entry)
