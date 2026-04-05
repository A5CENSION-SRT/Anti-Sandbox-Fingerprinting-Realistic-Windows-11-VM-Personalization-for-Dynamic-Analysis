"""Mount point validation and path resolution."""

from pathlib import Path


class MountManager:
    """Manages the mounted drive root and resolves relative paths safely."""

    def __init__(self, mount_root: str):
        self._mount_root = Path(mount_root).resolve()
        if not self._mount_root.exists():
            raise FileNotFoundError(
                f"Mount root does not exist: {self._mount_root}"
            )

    @property
    def root(self) -> Path:
        """Return the resolved mount root path."""
        return self._mount_root

    def resolve(self, relative_path: str = "") -> Path:
        """Resolve a relative path against the mount root.

        Raises ValueError if the resolved path escapes the mount root.
        """
        resolved = (self._mount_root / relative_path).resolve()
        if not str(resolved).startswith(str(self._mount_root)):
            raise ValueError(
                f"Path escape detected: {relative_path} resolves outside mount root"
            )
        return resolved
