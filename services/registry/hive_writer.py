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
# Allocation helpers
# ---------------------------------------------------------------------------

def _align8(size: int) -> int:
    """Round *size* up to the next multiple of 8 (cell alignment)."""
    return (size + 7) & ~7


class _HiveAllocator:
    """Sequential cell allocator for hive binary data.

    Tracks the next free byte within hive bins and extends the hive
    with new 4096-byte hbin pages when space runs out.  All offsets
    returned are **hive-relative** (i.e. relative to the start of the
    first hbin, which sits at file offset ``_REGF_HEADER_SIZE``).
    """

    _HBIN_PAGE_SIZE = 4096

    def __init__(self, hive_data: bytearray) -> None:
        self._data = hive_data
        # Walk hbins to find the end of allocated space
        self._next_file_offset = self._find_next_free(hive_data)

    @staticmethod
    def _find_next_free(hive_data: bytearray) -> int:
        """Scan hbin(s) to locate the first free cell."""
        pos = _REGF_HEADER_SIZE
        while pos < len(hive_data):
            sig = hive_data[pos:pos + 4]
            if sig != _HBIN_SIGNATURE:
                break
            hbin_size = struct.unpack_from("<I", hive_data, pos + 8)[0]
            cell_pos = pos + 32  # skip hbin header
            last_free = None
            while cell_pos < pos + hbin_size:
                cell_size_raw = struct.unpack_from("<i", hive_data, cell_pos)[0]
                cell_size = abs(cell_size_raw)
                if cell_size < 8:
                    break
                if cell_size_raw > 0:
                    # Free cell — potential allocation point
                    last_free = cell_pos
                cell_pos += cell_size
            if last_free is not None:
                return last_free
            pos += hbin_size
        # No free space found — allocations will extend the hive
        return len(hive_data)

    def allocate(self, data_size: int) -> int:
        """Allocate a cell of *data_size* bytes.

        Returns the hive-relative offset of the cell (pointing at the
        cell size field).  The caller must write the cell content.
        """
        cell_size = _align8(data_size + 4)  # +4 for size prefix
        file_off = self._next_file_offset

        # Ensure enough space — extend hive if needed
        while file_off + cell_size > len(self._data):
            self._add_hbin_page()

        # If we're allocating inside an existing free cell, split it
        if file_off < len(self._data) - 4:
            existing_raw = struct.unpack_from("<i", self._data, file_off)[0]
            if existing_raw > 0:  # free cell
                remaining = existing_raw - cell_size
                if remaining >= 8:
                    # Create a smaller free cell after our allocation
                    struct.pack_into("<i", self._data, file_off + cell_size, remaining)

        self._next_file_offset = file_off + cell_size
        # Return hive-relative offset
        return file_off - _REGF_HEADER_SIZE

    def _add_hbin_page(self) -> None:
        """Extend the hive with a new 4096-byte hbin page."""
        hbin_offset_from_start = len(self._data) - _REGF_HEADER_SIZE
        page = bytearray(self._HBIN_PAGE_SIZE)
        struct.pack_into("<4s", page, 0, _HBIN_SIGNATURE)
        struct.pack_into("<I", page, 4, hbin_offset_from_start)
        struct.pack_into("<I", page, 8, self._HBIN_PAGE_SIZE)
        # Mark entire data area as one free cell
        free_size = self._HBIN_PAGE_SIZE - 32  # minus hbin header
        struct.pack_into("<i", page, 32, free_size)
        self._data.extend(page)


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
                # Flush to disk before each set so regipy can read
                # the latest structure (needed for key creation path).
                try:
                    hive_abs.write_bytes(bytes(hive_data))
                except OSError as exc:
                    raise HiveWriterError(
                        f"Failed to write hive {hive_abs}: {exc}"
                    ) from exc
                self._apply_set_operation(hive_data, hive_abs, op)
            elif op.operation == "delete_value":
                self._audit_operation(op, hive_abs, "delete_value_skipped",
                                      note="delete_value not yet implemented")
            elif op.operation == "delete_key":
                self._audit_operation(op, hive_abs, "delete_key_skipped",
                                      note="delete_key not yet implemented")

        # Final flush
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
        """Set a registry value, creating keys/values as needed.

        First attempts to patch an existing value in-place.  If the
        key or value does not exist, creates the full key path and
        value in the hive binary.

        Args:
            hive_data: Mutable hive bytes (modified in-place).
            hive_abs: Path for audit logging.
            op: The set operation describing key, value, and data.
        """
        encoded = self._encode_value_data(op.value_data, op.value_type)

        # Try the fast path: patch existing value in-place
        try:
            reg = RegistryHive(str(hive_abs))
            key = reg.get_key(op.key_path)
            # Key exists — try to find and patch the value
            for val in key.iter_values():
                if val.name == op.value_name:
                    current_raw = self._get_current_raw_value(
                        reg, op.key_path, op.value_name
                    )
                    if current_raw and len(current_raw) > 0:
                        offset = self._find_data_offset(hive_data, current_raw)
                        if offset is not None and len(encoded) <= len(current_raw):
                            hive_data[offset:offset + len(current_raw)] = (
                                encoded + b"\x00" * (len(current_raw) - len(encoded))
                            )
                            self._audit_operation(op, hive_abs, "set_value")
                            return
                    break  # value found but can't patch in-place; fall through
        except (RegistryKeyNotFoundException, Exception):
            pass  # key doesn't exist — fall through to creation

        # Slow path: create the key path and value in the hive binary.
        # We must flush hive_data to disk first so that regipy can
        # re-read the updated structure after each creation step.
        self._create_key_and_value(hive_data, hive_abs, op, encoded)

    def _create_key_and_value(
        self,
        hive_data: bytearray,
        hive_abs: Path,
        op: HiveOperation,
        encoded: bytes,
    ) -> None:
        """Create a full key path and value in the hive binary.

        Walks from the root NK cell, creating missing NK (key) cells
        along the path, then creates or replaces the VK (value) cell.
        """
        alloc = _HiveAllocator(hive_data)

        # Find root NK cell offset (relative to hbin data start)
        root_cell_rel = struct.unpack_from("<I", hive_data, 36)[0]
        parts = [p for p in op.key_path.replace("/", "\\").split("\\") if p]

        # Walk existing keys, creating missing ones
        current_nk_rel = root_cell_rel
        for part in parts:
            child_rel = self._find_subkey(hive_data, current_nk_rel, part)
            if child_rel is None:
                child_rel = self._create_nk_cell(
                    hive_data, alloc, part, current_nk_rel
                )
                self._add_subkey_to_parent(
                    hive_data, alloc, current_nk_rel, child_rel, part
                )
            current_nk_rel = child_rel

        # Now current_nk_rel points to the leaf key NK cell.
        # Create the value.
        value_type_int = _VALUE_TYPE_MAP.get(op.value_type, _REG_SZ)
        self._create_or_replace_value(
            hive_data, alloc, current_nk_rel,
            op.value_name, encoded, value_type_int,
        )

        # Update regf header data size
        total_hbins = len(hive_data) - _REGF_HEADER_SIZE
        struct.pack_into("<I", hive_data, 40, total_hbins)

        # Update regf header checksum
        checksum = 0
        for i in range(0, 508, 4):
            checksum ^= struct.unpack_from("<I", hive_data, i)[0]
        struct.pack_into("<I", hive_data, 508, checksum)

        self._audit_operation(op, hive_abs, "set_value_created")

    def _find_subkey(
        self,
        hive_data: bytearray,
        parent_nk_rel: int,
        name: str,
    ) -> Optional[int]:
        """Find a subkey by name under a parent NK cell.

        Returns the subkey's hive-relative offset, or None.
        """
        file_off = _REGF_HEADER_SIZE + parent_nk_rel
        # Read subkey count and subkey list offset from NK cell
        # NK layout: +0 size, +4 "nk", +6 flags, ... +24 subkey_count, +32 subkeys_stable_off
        subkey_count = struct.unpack_from("<I", hive_data, file_off + 4 + 20)[0]
        subkeys_off_rel = struct.unpack_from("<I", hive_data, file_off + 4 + 28)[0]
        if subkey_count == 0 or subkeys_off_rel == 0xFFFFFFFF:
            return None

        # Read LF/LH/RI subkey list
        lf_file_off = _REGF_HEADER_SIZE + subkeys_off_rel
        sig = hive_data[lf_file_off + 4:lf_file_off + 6]
        count = struct.unpack_from("<H", hive_data, lf_file_off + 6)[0]

        name_lower = name.lower()
        for i in range(count):
            entry_off = lf_file_off + 8 + i * 8
            child_rel = struct.unpack_from("<I", hive_data, entry_off)[0]
            # Read child NK name
            child_file_off = _REGF_HEADER_SIZE + child_rel
            child_name_len = struct.unpack_from(
                "<H", hive_data, child_file_off + 4 + 68
            )[0]
            child_name_off = child_file_off + 4 + 72
            raw_name = bytes(hive_data[child_name_off:child_name_off + child_name_len])
            try:
                child_name = raw_name.decode("ascii")
            except UnicodeDecodeError:
                child_name = raw_name.decode("utf-16-le", errors="replace")
            if child_name.lower() == name_lower:
                return child_rel
        return None

    @staticmethod
    def _create_nk_cell(
        hive_data: bytearray,
        alloc: "_HiveAllocator",
        name: str,
        parent_nk_rel: int,
    ) -> int:
        """Create a new NK (key) cell and return its hive-relative offset."""
        name_bytes = name.encode("ascii", errors="replace")
        # NK data: sig(2) + flags(2) + timestamp(8) + access(4) + parent(4) +
        #   subkey_count(4) + subkey_count_volatile(4) + subkeys_stable(4) +
        #   subkeys_volatile(4) + value_count(4) + values_list(4) +
        #   security(4) + class_name(4) + max_subkey_name(4) +
        #   max_class_name(4) + max_value_name(4) + max_value_data(4) +
        #   name_len(2) + class_name_len(2) + name(N) = 72 + N
        data_size = 72 + len(name_bytes)
        cell_rel = alloc.allocate(data_size)
        cell_file = _REGF_HEADER_SIZE + cell_rel

        cell_size = _align8(data_size + 4)
        struct.pack_into("<i", hive_data, cell_file, -cell_size)
        off = cell_file + 4
        struct.pack_into("<2s", hive_data, off, b"nk")       # signature
        struct.pack_into("<H", hive_data, off + 2, 0x0000)   # flags (normal key)
        struct.pack_into("<Q", hive_data, off + 4, 0)        # timestamp
        struct.pack_into("<I", hive_data, off + 12, 0)       # access
        struct.pack_into("<I", hive_data, off + 16, parent_nk_rel)  # parent
        struct.pack_into("<I", hive_data, off + 20, 0)       # subkey_count
        struct.pack_into("<I", hive_data, off + 24, 0)       # subkey_count_volatile
        struct.pack_into("<I", hive_data, off + 28, 0xFFFFFFFF)  # subkeys_stable
        struct.pack_into("<I", hive_data, off + 32, 0xFFFFFFFF)  # subkeys_volatile
        struct.pack_into("<I", hive_data, off + 36, 0)       # value_count
        struct.pack_into("<I", hive_data, off + 40, 0xFFFFFFFF)  # values_list
        struct.pack_into("<I", hive_data, off + 44, 0xFFFFFFFF)  # security
        struct.pack_into("<I", hive_data, off + 48, 0xFFFFFFFF)  # class_name
        struct.pack_into("<I", hive_data, off + 52, 0)       # max_subkey_name
        struct.pack_into("<I", hive_data, off + 56, 0)       # max_class_name
        struct.pack_into("<I", hive_data, off + 60, 0)       # max_value_name
        struct.pack_into("<I", hive_data, off + 64, 0)       # max_value_data
        struct.pack_into("<H", hive_data, off + 68, len(name_bytes))  # name_len
        struct.pack_into("<H", hive_data, off + 70, 0)       # class_name_len
        hive_data[off + 72:off + 72 + len(name_bytes)] = name_bytes
        return cell_rel

    @staticmethod
    def _add_subkey_to_parent(
        hive_data: bytearray,
        alloc: "_HiveAllocator",
        parent_nk_rel: int,
        child_nk_rel: int,
        child_name: str,
    ) -> None:
        """Add a child NK reference to a parent's subkey list."""
        parent_file = _REGF_HEADER_SIZE + parent_nk_rel
        nk_off = parent_file + 4  # skip cell size

        subkey_count = struct.unpack_from("<I", hive_data, nk_off + 20)[0]
        old_lf_rel = struct.unpack_from("<I", hive_data, nk_off + 28)[0]

        # Compute a 4-byte name hint (first 4 bytes of ASCII name, padded)
        hint_bytes = child_name.encode("ascii", errors="replace")[:4].ljust(4, b"\x00")
        hint = struct.unpack("<I", hint_bytes)[0]

        if old_lf_rel == 0xFFFFFFFF or subkey_count == 0:
            # No existing subkey list — create a new LF with one entry
            lf_data_size = 4 + 8  # sig(2)+count(2) + 1 entry(8)
            lf_rel = alloc.allocate(lf_data_size)
            lf_file = _REGF_HEADER_SIZE + lf_rel
            cell_size = _align8(lf_data_size + 4)
            struct.pack_into("<i", hive_data, lf_file, -cell_size)
            struct.pack_into("<2s", hive_data, lf_file + 4, b"lf")
            struct.pack_into("<H", hive_data, lf_file + 6, 1)
            struct.pack_into("<I", hive_data, lf_file + 8, child_nk_rel)
            struct.pack_into("<I", hive_data, lf_file + 12, hint)
        else:
            # Extend existing LF list — create a new one with one more entry
            old_lf_file = _REGF_HEADER_SIZE + old_lf_rel
            old_count = struct.unpack_from("<H", hive_data, old_lf_file + 6)[0]

            new_count = old_count + 1
            lf_data_size = 4 + 8 * new_count
            lf_rel = alloc.allocate(lf_data_size)
            lf_file = _REGF_HEADER_SIZE + lf_rel
            cell_size = _align8(lf_data_size + 4)
            struct.pack_into("<i", hive_data, lf_file, -cell_size)
            struct.pack_into("<2s", hive_data, lf_file + 4, b"lf")
            struct.pack_into("<H", hive_data, lf_file + 6, new_count)

            # Copy old entries
            for i in range(old_count):
                src = old_lf_file + 8 + i * 8
                dst = lf_file + 8 + i * 8
                hive_data[dst:dst + 8] = hive_data[src:src + 8]

            # Append new entry
            dst = lf_file + 8 + old_count * 8
            struct.pack_into("<I", hive_data, dst, child_nk_rel)
            struct.pack_into("<I", hive_data, dst + 4, hint)

            # Mark old LF cell as free (positive size)
            old_cell_size = abs(struct.unpack_from("<i", hive_data, old_lf_file)[0])
            struct.pack_into("<i", hive_data, old_lf_file, old_cell_size)

        # Update parent NK: subkeys_stable offset and subkey_count
        struct.pack_into("<I", hive_data, nk_off + 28, lf_rel)
        struct.pack_into("<I", hive_data, nk_off + 20, subkey_count + 1)

    @staticmethod
    def _create_or_replace_value(
        hive_data: bytearray,
        alloc: "_HiveAllocator",
        key_nk_rel: int,
        value_name: str,
        encoded_data: bytes,
        value_type: int,
    ) -> None:
        """Create a VK cell and add it to the key's value list."""
        key_file = _REGF_HEADER_SIZE + key_nk_rel
        nk_off = key_file + 4

        name_bytes = value_name.encode("ascii", errors="replace")
        data_len = len(encoded_data)

        # Determine if data is inline (≤ 4 bytes)
        inline = data_len <= 4

        # Create VK cell
        # VK data: sig(2) + name_len(2) + data_len(4) + data_off(4) +
        #   data_type(4) + flags(2) + spare(2) + name(N) = 20 + N
        vk_data_size = 20 + len(name_bytes)
        vk_rel = alloc.allocate(vk_data_size)
        vk_file = _REGF_HEADER_SIZE + vk_rel
        vk_cell_size = _align8(vk_data_size + 4)
        struct.pack_into("<i", hive_data, vk_file, -vk_cell_size)
        vk = vk_file + 4
        struct.pack_into("<2s", hive_data, vk, b"vk")
        struct.pack_into("<H", hive_data, vk + 2, len(name_bytes))

        if inline:
            # Inline: set high bit of data_length, store data in offset field
            struct.pack_into("<I", hive_data, vk + 4, data_len | 0x80000000)
            # Pack inline data into the 4-byte offset field
            inline_data = encoded_data.ljust(4, b"\x00")
            hive_data[vk + 8:vk + 12] = inline_data[:4]
        else:
            # External: create a separate data cell
            data_cell_size = data_len
            data_rel = alloc.allocate(data_cell_size)
            data_file = _REGF_HEADER_SIZE + data_rel
            cell_sz = _align8(data_cell_size + 4)
            struct.pack_into("<i", hive_data, data_file, -cell_sz)
            hive_data[data_file + 4:data_file + 4 + data_len] = encoded_data

            struct.pack_into("<I", hive_data, vk + 4, data_len)
            struct.pack_into("<I", hive_data, vk + 8, data_rel)

        struct.pack_into("<I", hive_data, vk + 12, value_type)
        # Flags: 1 = name is ASCII
        struct.pack_into("<H", hive_data, vk + 16, 1 if name_bytes.isascii() else 0)
        struct.pack_into("<H", hive_data, vk + 18, 0)  # spare
        hive_data[vk + 20:vk + 20 + len(name_bytes)] = name_bytes

        # Update key's value list
        old_val_count = struct.unpack_from("<I", hive_data, nk_off + 36)[0]
        old_vl_rel = struct.unpack_from("<I", hive_data, nk_off + 40)[0]
        new_count = old_val_count + 1

        # Create new value list
        vl_data_size = 4 * new_count
        vl_rel = alloc.allocate(vl_data_size)
        vl_file = _REGF_HEADER_SIZE + vl_rel
        vl_cell_size = _align8(vl_data_size + 4)
        struct.pack_into("<i", hive_data, vl_file, -vl_cell_size)

        # Copy old entries if any
        if old_vl_rel != 0xFFFFFFFF and old_val_count > 0:
            old_vl_file = _REGF_HEADER_SIZE + old_vl_rel
            for i in range(old_val_count):
                old_entry = struct.unpack_from("<I", hive_data, old_vl_file + 4 + i * 4)[0]
                struct.pack_into("<I", hive_data, vl_file + 4 + i * 4, old_entry)
            # Free old value list
            old_sz = abs(struct.unpack_from("<i", hive_data, old_vl_file)[0])
            struct.pack_into("<i", hive_data, old_vl_file, old_sz)

        # Append new VK reference
        struct.pack_into("<I", hive_data, vl_file + 4 + old_val_count * 4, vk_rel)

        # Update NK: value_count and values_list offset
        struct.pack_into("<I", hive_data, nk_off + 36, new_count)
        struct.pack_into("<I", hive_data, nk_off + 40, vl_rel)

    def _get_current_raw_value(
        self,
        reg: Any,
        key_path: str,
        value_name: str,
    ) -> Optional[bytes]:
        """Extract the raw bytes of a value using regipy."""
        try:
            key = reg.get_key(key_path)
            for val in key.iter_values():
                if val.name == value_name:
                    if isinstance(val.value, bytes):
                        return val.value
                    if isinstance(val.value, str):
                        return val.value.encode("utf-16-le")
                    if isinstance(val.value, int):
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
