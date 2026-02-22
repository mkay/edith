"""Manages temporary files for remote file editing."""

import atexit
import shutil
import tempfile
import uuid
from pathlib import Path


class TempManager:
    """Lifecycle manager for temp files used during editing."""

    _base_dir: Path | None = None

    @classmethod
    def _ensure_base(cls) -> Path:
        if cls._base_dir is None or not cls._base_dir.exists():
            cls._base_dir = Path(tempfile.mkdtemp(prefix="edith-"))
            atexit.register(cls.cleanup)
        return cls._base_dir

    @classmethod
    def get_temp_path(cls, remote_path: str) -> Path:
        """Get a unique local temp path for a remote file."""
        base = cls._ensure_base()
        # Use uuid subdirectory to avoid name collisions
        unique = uuid.uuid4().hex[:8]
        filename = remote_path.rsplit("/", 1)[-1]
        subdir = base / unique
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / filename

    @classmethod
    def cleanup(cls):
        """Remove all temp files."""
        if cls._base_dir and cls._base_dir.exists():
            shutil.rmtree(cls._base_dir, ignore_errors=True)
            cls._base_dir = None
