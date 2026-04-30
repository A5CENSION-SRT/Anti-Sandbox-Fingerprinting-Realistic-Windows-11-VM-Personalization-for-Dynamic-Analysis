"""ctypes-based hivex wrapper.

Drop-in replacement for the python3-hivex C extension when it is not
available for the active Python version.  Calls libhivex.so.0 directly
via ctypes — no ABI compatibility issue with Python version.

API is intentionally identical to hivex.Hivex so that existing code
(HivexHandle, HiveWriter) requires zero changes.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from typing import Optional

# ---------------------------------------------------------------------------
# Load the shared library
# ---------------------------------------------------------------------------

_LIB_NAME = "libhivex.so.0"

try:
    _lib = ctypes.CDLL(_LIB_NAME)
except OSError as _exc:
    raise ImportError(
        f"Cannot load {_LIB_NAME}: {_exc}. "
        "Install with: sudo apt install libhivex-bin"
    ) from _exc

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HIVEX_OPEN_WRITE     = 2
HIVEX_OPEN_VERBOSE   = 4
HIVEX_OPEN_DEBUG     = 8
HIVEX_OPEN_MSGLVL    = 0x30

# hive_type enum values (winnt.h REG_*)
REG_NONE        = 0
REG_SZ          = 1
REG_EXPAND_SZ   = 2
REG_BINARY      = 3
REG_DWORD       = 4
REG_DWORD_BIG_ENDIAN = 5
REG_LINK        = 6
REG_MULTI_SZ    = 7
REG_QWORD       = 11


# ---------------------------------------------------------------------------
# Struct: hive_set_value
#
# typedef struct {
#   char      *key;   // 8 bytes (pointer, x86-64)
#   hive_type  t;     // 4 bytes (int enum)
#   // [4 bytes implicit padding to align size_t]
#   size_t     len;   // 8 bytes
#   char      *value; // 8 bytes (pointer)
# } hive_set_value;
# ---------------------------------------------------------------------------

class _HiveSetValue(ctypes.Structure):
    _fields_ = [
        ("key",   ctypes.c_char_p),
        ("t",     ctypes.c_uint32),
        ("_pad",  ctypes.c_uint32),   # explicit alignment padding
        ("len",   ctypes.c_size_t),
        ("value", ctypes.c_void_p),   # c_void_p so binary data is not truncated at \0
    ]


# ---------------------------------------------------------------------------
# Function signatures
# ---------------------------------------------------------------------------

_lib.hivex_open.restype  = ctypes.c_void_p
_lib.hivex_open.argtypes = [ctypes.c_char_p, ctypes.c_int]

_lib.hivex_close.restype  = ctypes.c_int
_lib.hivex_close.argtypes = [ctypes.c_void_p]

_lib.hivex_commit.restype  = ctypes.c_int
_lib.hivex_commit.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

_lib.hivex_root.restype  = ctypes.c_int64
_lib.hivex_root.argtypes = [ctypes.c_void_p]

_lib.hivex_node_name.restype  = ctypes.c_char_p
_lib.hivex_node_name.argtypes = [ctypes.c_void_p, ctypes.c_int64]

_lib.hivex_node_get_child.restype  = ctypes.c_int64
_lib.hivex_node_get_child.argtypes = [ctypes.c_void_p, ctypes.c_int64, ctypes.c_char_p]

_lib.hivex_node_add_child.restype  = ctypes.c_int64
_lib.hivex_node_add_child.argtypes = [ctypes.c_void_p, ctypes.c_int64, ctypes.c_char_p]

_lib.hivex_node_delete_child.restype  = ctypes.c_int
_lib.hivex_node_delete_child.argtypes = [ctypes.c_void_p, ctypes.c_int64]

_lib.hivex_node_set_value.restype  = ctypes.c_int
_lib.hivex_node_set_value.argtypes = [
    ctypes.c_void_p, ctypes.c_int64,
    ctypes.POINTER(_HiveSetValue), ctypes.c_int,
]

_lib.hivex_node_get_value.restype  = ctypes.c_int64
_lib.hivex_node_get_value.argtypes = [ctypes.c_void_p, ctypes.c_int64, ctypes.c_char_p]

_lib.hivex_value_type.restype  = ctypes.c_int
_lib.hivex_value_type.argtypes = [
    ctypes.c_void_p, ctypes.c_int64,
    ctypes.POINTER(ctypes.c_size_t),
]

_lib.hivex_value_value.restype  = ctypes.c_void_p
_lib.hivex_value_value.argtypes = [
    ctypes.c_void_p, ctypes.c_int64,
    ctypes.POINTER(ctypes.c_size_t),
]


# ---------------------------------------------------------------------------
# Python wrapper class — API matches hivex.Hivex from python3-hivex
# ---------------------------------------------------------------------------

def _enc(s: object) -> bytes:
    """Encode str → bytes; pass bytes through."""
    if isinstance(s, str):
        return s.encode("utf-8")
    return bytes(s) if s is not None else b""


class Hivex:
    """ctypes wrapper around libhivex.so.0.

    Provides the same API as ``hivex.Hivex`` from python3-hivex so that
    HivexHandle and HiveWriter require no changes.

    Args:
        filename: Path to the hive file (str or bytes).
        write:    Open in write mode (default False).
    """

    def __init__(self, filename: object, write: bool = False) -> None:
        flags = HIVEX_OPEN_WRITE if write else 0
        fname = _enc(filename)
        self._h = _lib.hivex_open(fname, flags)
        if not self._h:
            raise OSError(f"hivex_open failed on: {filename}")

    def close(self) -> None:
        if self._h:
            _lib.hivex_close(self._h)
            self._h = None

    def commit(self, filename: Optional[object]) -> None:
        """Commit changes.  Pass None (or omit) to write back to the same file."""
        fname = _enc(filename) if filename is not None else None
        ret = _lib.hivex_commit(self._h, fname)
        if ret != 0:
            raise OSError("hivex_commit failed")

    # -- node navigation --

    def root(self) -> int:
        return _lib.hivex_root(self._h)

    def node_get_child(self, node: int, name: object) -> int:
        """Return child node handle, or 0 if not found."""
        return _lib.hivex_node_get_child(self._h, node, _enc(name))

    def node_add_child(self, parent: int, name: object) -> int:
        result = _lib.hivex_node_add_child(self._h, parent, _enc(name))
        if result == 0:
            raise OSError(f"hivex_node_add_child failed for: {name}")
        return result

    def node_delete_child(self, node: int) -> None:
        ret = _lib.hivex_node_delete_child(self._h, node)
        if ret != 0:
            raise OSError("hivex_node_delete_child failed")

    # -- values --

    def node_get_value(self, node: int, key: object) -> int:
        """Return value handle, or 0 if not found."""
        return _lib.hivex_node_get_value(self._h, node, _enc(key))

    def node_set_value(self, node: int, val: dict, flags: int = 0) -> None:
        """Set a value on a node.

        Args:
            node:  Node handle.
            val:   Dict with keys ``"key"`` (str), ``"t"`` (int, REG_* constant),
                   and ``"value"`` (bytes).
            flags: Unused; kept for API compatibility.
        """
        key_b   = _enc(val["key"])
        value_b = bytes(val.get("value", b""))
        t       = int(val["t"])

        # Keep a reference to the buffer so the GC doesn't free it before the C call.
        buf = ctypes.create_string_buffer(value_b, len(value_b))

        sv = _HiveSetValue(
            key   = key_b,
            t     = t,
            _pad  = 0,
            len   = len(value_b),
            value = ctypes.cast(buf, ctypes.c_void_p),
        )
        ret = _lib.hivex_node_set_value(
            self._h, node, ctypes.byref(sv), 0
        )
        if ret != 0:
            raise OSError(f"hivex_node_set_value failed for key: {val.get('key')}")

    def value_type(self, val_h: int) -> tuple:
        """Return (hive_type, length) for a value handle."""
        length = ctypes.c_size_t(0)
        t = _lib.hivex_value_type(self._h, val_h, ctypes.byref(length))
        return t, length.value

    def value_value(self, val_h: int) -> bytes:
        """Return raw bytes of a value."""
        length = ctypes.c_size_t(0)
        ptr = _lib.hivex_value_value(self._h, val_h, ctypes.byref(length))
        if not ptr:
            return b""
        return ctypes.string_at(ptr, length.value)
