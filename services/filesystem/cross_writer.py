"""Recursive directory/file writing service for mounted drives.

CrossWriter is a pure executor: it creates directories and files from a tree
specification, applies timestamps and file attributes, and logs all operations.
It never generates content, timestamps, or identity data.
"""

import os
import platform
from pathlib import Path
from tempfile import NamedTemporaryFile

from services.base_service import BaseService

# Windows file attributes — available only on Windows via pywin32
try:
    import win32api
    import win32con
    import win32file
    import pywintypes

    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False

# Attribute name → win32con flag mapping
_ATTRIBUTE_MAP = {
    "hidden": getattr(win32con, "FILE_ATTRIBUTE_HIDDEN", 0x2) if _HAS_WIN32 else 0x2,
    "system": getattr(win32con, "FILE_ATTRIBUTE_SYSTEM", 0x4) if _HAS_WIN32 else 0x4,
    "archive": getattr(win32con, "FILE_ATTRIBUTE_ARCHIVE", 0x20) if _HAS_WIN32 else 0x20,
}

_VALID_FILE_FIELDS = {"type", "content", "binary_content", "attributes", "timestamp_event"}
_VALID_ATTRIBUTES = set(_ATTRIBUTE_MAP.keys())


class CrossWriterError(Exception):
    """Raised on schema validation, path escape, or write failures."""


class CrossWriter(BaseService):
    """Recursively creates directories and files on a mounted drive.

    Dependencies (injected):
        mount_manager: Resolves all paths relative to mount root.
        timestamp_service: Provides timestamps for filesystem entries.
        audit_logger: Records all operations for audit trail.
    """

    def __init__(self, mount_manager, timestamp_service, audit_logger):
        self._mount_manager = mount_manager
        self._timestamp_service = timestamp_service
        self._audit_logger = audit_logger

    @property
    def service_name(self) -> str:
        return "CrossWriter"

    def apply(self, context: dict) -> None:
        """Execute from orchestrator context.

        Expects context keys:
            tree_spec: dict — the tree specification
            base_path: str — relative path from mount root (default "")
        """
        tree_spec = context.get("tree_spec", {})
        base_path = context.get("base_path", "")
        self.apply_tree(tree_spec, base_path)

    def apply_tree(self, tree_spec: dict, base_path: str = "") -> None:
        """Create directories and files described by tree_spec.

        Args:
            tree_spec: Nested dict describing the directory/file tree.
            base_path: Relative path from mount root.

        Raises:
            CrossWriterError: On schema validation failure or path escape.
        """
        self._validate_tree(tree_spec)
        root = self._mount_manager.resolve(base_path)
        self._assert_within_mount(root)
        self._process_node(root, tree_spec)

    # ------------------------------------------------------------------
    # Schema validation
    # ------------------------------------------------------------------

    def _validate_tree(self, node: dict, path: str = "<root>") -> None:
        """Recursively validate the tree spec before any writes."""
        if not isinstance(node, dict):
            raise CrossWriterError(f"Expected dict at {path}, got {type(node).__name__}")

        for key, value in node.items():
            child_path = f"{path}/{key}"
            if not isinstance(value, dict):
                raise CrossWriterError(
                    f"Expected dict for '{child_path}', got {type(value).__name__}"
                )
            if value.get("type") == "file":
                self._validate_file_node(value, child_path)
            else:
                # It's a directory — recurse
                if "type" in value:
                    raise CrossWriterError(
                        f"Unknown type '{value['type']}' at {child_path}"
                    )
                self._validate_tree(value, child_path)

    def _validate_file_node(self, node: dict, path: str) -> None:
        """Validate a file node's fields."""
        unknown = set(node.keys()) - _VALID_FILE_FIELDS
        if unknown:
            raise CrossWriterError(
                f"Unknown fields {unknown} in file node at {path}"
            )
        if "timestamp_event" not in node:
            raise CrossWriterError(
                f"Missing required 'timestamp_event' in file node at {path}"
            )
        if "content" in node and "binary_content" in node:
            raise CrossWriterError(
                f"Cannot specify both 'content' and 'binary_content' at {path}"
            )
        attrs = node.get("attributes", [])
        if not isinstance(attrs, list):
            raise CrossWriterError(
                f"'attributes' must be a list at {path}"
            )
        invalid_attrs = set(attrs) - _VALID_ATTRIBUTES
        if invalid_attrs:
            raise CrossWriterError(
                f"Invalid attributes {invalid_attrs} at {path}"
            )

    # ------------------------------------------------------------------
    # Recursive tree processing
    # ------------------------------------------------------------------

    def _process_node(self, current_path: Path, node: dict) -> None:
        """Recursively process a tree node, creating dirs and files."""
        for name, value in node.items():
            child_path = current_path / name
            self._assert_within_mount(child_path)

            if value.get("type") == "file":
                self._create_file(child_path, value)
            else:
                self._create_directory(child_path)
                self._process_node(child_path, value)

    # ------------------------------------------------------------------
    # Directory creation
    # ------------------------------------------------------------------

    def _create_directory(self, path: Path) -> None:
        """Create a directory and log the operation."""
        path.mkdir(parents=True, exist_ok=True)
        self._audit_logger.log({
            "service": self.service_name,
            "operation": "create_directory",
            "path": str(path),
        })

    # ------------------------------------------------------------------
    # File creation
    # ------------------------------------------------------------------

    def _create_file(self, target_path: Path, node: dict) -> None:
        """Atomically write a file, apply timestamps and attributes."""
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Determine content
        if "binary_content" in node:
            data = node["binary_content"]
            if isinstance(data, str):
                data = data.encode("utf-8")
            mode = "wb"
        else:
            data = (node.get("content") or "").encode("utf-8")
            mode = "wb"

        # Atomic write via temp file
        try:
            with NamedTemporaryFile(
                delete=False, dir=str(target_path.parent), mode=mode
            ) as tmp:
                tmp.write(data)
                tmp_path = Path(tmp.name)
            tmp_path.replace(target_path)
        except Exception as exc:
            # Clean up temp file on failure
            if "tmp_path" in locals() and tmp_path.exists():
                tmp_path.unlink()
            self._audit_logger.log({
                "service": self.service_name,
                "operation": "create_file_failed",
                "path": str(target_path),
                "error": str(exc),
            })
            raise CrossWriterError(
                f"Failed to write file {target_path}: {exc}"
            ) from exc

        # Apply timestamps
        event_type = node["timestamp_event"]
        self._apply_timestamps(target_path, event_type)

        # Apply attributes
        attributes = node.get("attributes", [])
        if attributes:
            self._apply_attributes(target_path, attributes)

        self._audit_logger.log({
            "service": self.service_name,
            "operation": "create_file",
            "path": str(target_path),
            "attributes": attributes,
            "timestamp_event": event_type,
        })

    # ------------------------------------------------------------------
    # Timestamps
    # ------------------------------------------------------------------

    def _apply_timestamps(self, path: Path, event_type: str) -> None:
        """Apply created/modified/accessed timestamps from the timestamp service."""
        timestamps = self._timestamp_service.get_timestamp(event_type)

        accessed = timestamps["accessed"].timestamp()
        modified = timestamps["modified"].timestamp()
        os.utime(str(path), (accessed, modified))

        # Creation time requires pywin32 on Windows
        if _HAS_WIN32 and platform.system() == "Windows":
            created = pywintypes.Time(timestamps["created"])
            handle = win32file.CreateFile(
                str(path),
                win32con.GENERIC_WRITE,
                win32con.FILE_SHARE_WRITE,
                None,
                win32con.OPEN_EXISTING,
                win32con.FILE_ATTRIBUTE_NORMAL,
                None,
            )
            try:
                win32file.SetFileTime(handle, created, None, None)
            finally:
                handle.Close()

    # ------------------------------------------------------------------
    # File attributes
    # ------------------------------------------------------------------

    def _apply_attributes(self, path: Path, attributes: list) -> None:
        """Apply hidden/system/archive file attributes."""
        if not _HAS_WIN32 or platform.system() != "Windows":
            return

        flags = 0
        for attr in attributes:
            flags |= _ATTRIBUTE_MAP[attr]

        win32api.SetFileAttributes(str(path), flags)

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    def _assert_within_mount(self, path: Path) -> None:
        """Ensure resolved path is within the mount root."""
        mount_root = self._mount_manager.root
        resolved = path.resolve()
        try:
            resolved.relative_to(mount_root)
        except ValueError:
            raise CrossWriterError(
                f"Path escape detected: {path} resolves outside mount root {mount_root}"
            )
