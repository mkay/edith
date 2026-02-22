"""Paramiko SFTP wrapper with thread-safe operations."""

import os
import stat
import threading
from pathlib import Path

import paramiko


class SftpClient:
    """Thread-safe SFTP client wrapping paramiko."""

    def __init__(self):
        self._transport = None
        self._sftp = None
        self._lock = threading.Lock()

    def connect(
        self,
        host: str,
        port: int = 22,
        username: str = "",
        password: str | None = None,
        key_file: str | None = None,
        passphrase: str | None = None,
    ):
        """Connect to an SFTP server. Blocks until connected."""
        transport = paramiko.Transport((host, port))

        pkey = None
        if key_file and os.path.isfile(key_file):
            pkey = paramiko.RSAKey.from_private_key_file(
                key_file, password=passphrase
            )

        if pkey:
            transport.connect(username=username, pkey=pkey)
        elif password:
            transport.connect(username=username, password=password)
        else:
            raise ValueError("No authentication method provided")

        sftp = paramiko.SFTPClient.from_transport(transport)

        self._transport = transport
        self._sftp = sftp

    def close(self):
        with self._lock:
            if self._sftp:
                try:
                    self._sftp.close()
                except Exception:
                    pass
                self._sftp = None
            if self._transport:
                try:
                    self._transport.close()
                except Exception:
                    pass
                self._transport = None

    @property
    def is_connected(self) -> bool:
        return (
            self._transport is not None
            and self._transport.is_active()
            and self._sftp is not None
        )

    def listdir_attr(self, path: str) -> list:
        """List directory contents with attributes."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            return self._sftp.listdir_attr(path)

    def download(self, remote_path: str, local_path: str, progress_cb=None):
        """Download a remote file to a local path."""
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            if progress_cb:
                self._sftp.get(remote_path, local_path, callback=progress_cb)
            else:
                self._sftp.get(remote_path, local_path)

    def upload(self, local_path: str, remote_path: str, progress_cb=None, overwrite=False):
        """Upload a local file to a remote path."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            if not overwrite and self._exists_unlocked(remote_path):
                name = remote_path.rsplit("/", 1)[-1]
                raise FileExistsError(f"'{name}' already exists on the server")
            if progress_cb:
                self._sftp.put(local_path, remote_path, callback=progress_cb)
            else:
                self._sftp.put(local_path, remote_path)

    def stat(self, path: str):
        """Stat a remote path."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            return self._sftp.stat(path)

    def mkdir(self, path: str):
        """Create a remote directory."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            self._sftp.mkdir(path)

    def rename(self, old_path: str, new_path: str):
        """Rename a remote file or directory."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            if self._exists_unlocked(new_path):
                name = new_path.rsplit("/", 1)[-1]
                raise FileExistsError(f"'{name}' already exists at the destination")
            self._sftp.rename(old_path, new_path)

    def chmod(self, path: str, mode: int):
        """Change permissions of a remote file or directory."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            self._sftp.chmod(path, mode)

    def remove(self, path: str):
        """Remove a remote file."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            self._sftp.remove(path)

    def rmdir(self, path: str):
        """Remove a remote directory (must be empty)."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            self._sftp.rmdir(path)

    def rmdir_recursive(self, path: str):
        """Recursively remove a remote directory and all contents."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            self._rmdir_recursive_unlocked(path)

    def _rmdir_recursive_unlocked(self, path: str):
        """Internal recursive delete without re-acquiring the lock."""
        for attr in self._sftp.listdir_attr(path):
            child = f"{path.rstrip('/')}/{attr.filename}"
            if stat.S_ISDIR(attr.st_mode):
                self._rmdir_recursive_unlocked(child)
            else:
                self._sftp.remove(child)
        self._sftp.rmdir(path)

    def copy_remote(self, src: str, dst: str):
        """Copy a remote file by reading and writing via the SFTP channel."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            if self._exists_unlocked(dst):
                name = dst.rsplit("/", 1)[-1]
                raise FileExistsError(f"'{name}' already exists at the destination")
            self._copy_file_unlocked(src, dst)

    def copy_remote_recursive(self, src: str, dst: str):
        """Recursively copy a remote file or directory."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            if self._exists_unlocked(dst):
                name = dst.rsplit("/", 1)[-1]
                raise FileExistsError(f"'{name}' already exists at the destination")
            self._copy_recursive_unlocked(src, dst)

    def _copy_file_unlocked(self, src: str, dst: str):
        with self._sftp.open(src, "rb") as fin:
            with self._sftp.open(dst, "wb") as fout:
                while True:
                    chunk = fin.read(65536)
                    if not chunk:
                        break
                    fout.write(chunk)

    def _copy_recursive_unlocked(self, src: str, dst: str):
        src_stat = self._sftp.stat(src)
        if stat.S_ISDIR(src_stat.st_mode):
            try:
                self._sftp.mkdir(dst)
            except OSError:
                pass  # directory may already exist
            for attr in self._sftp.listdir_attr(src):
                child_src = f"{src.rstrip('/')}/{attr.filename}"
                child_dst = f"{dst.rstrip('/')}/{attr.filename}"
                self._copy_recursive_unlocked(child_src, child_dst)
        else:
            self._copy_file_unlocked(src, dst)

    def create_file(self, path: str):
        """Create an empty remote file."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            f = self._sftp.open(path, "w")
            f.close()

    def upload_directory(self, local_dir: str, remote_dir: str):
        """Recursively upload a local directory to a remote path."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            if self._exists_unlocked(remote_dir):
                name = remote_dir.rsplit("/", 1)[-1]
                raise FileExistsError(f"'{name}' already exists on the server")
            self._upload_directory_unlocked(local_dir, remote_dir)

    def _upload_directory_unlocked(self, local_dir: str, remote_dir: str):
        try:
            self._sftp.mkdir(remote_dir)
        except OSError:
            pass  # directory may already exist
        for entry in os.listdir(local_dir):
            local_path = os.path.join(local_dir, entry)
            remote_path = f"{remote_dir.rstrip('/')}/{entry}"
            if os.path.isdir(local_path):
                self._upload_directory_unlocked(local_path, remote_path)
            else:
                self._sftp.put(local_path, remote_path)

    def _exists_unlocked(self, path: str) -> bool:
        """Check if a remote path exists (must be called with lock held)."""
        try:
            self._sftp.stat(path)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def is_dir(self, path: str) -> bool:
        """Check if remote path is a directory."""
        try:
            st = self.stat(path)
            return stat.S_ISDIR(st.st_mode)
        except Exception:
            return False
