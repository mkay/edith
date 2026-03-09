"""Paramiko SFTP wrapper with thread-safe operations."""

import os
import stat
import threading
import time
from pathlib import Path

import paramiko


class SftpClient:
    """Thread-safe SFTP client wrapping paramiko."""

    def __init__(self):
        self._transport = None
        self._sftp = None
        self._lock = threading.Lock()
        self.can_exec = False

    def connect(
        self,
        host: str,
        port: int = 22,
        username: str = "",
        password: str | None = None,
        key_file: str | None = None,
        passphrase: str | None = None,
        timeout: float = 10,
    ):
        """Connect to an SFTP server. Blocks until connected."""
        import socket

        sock = socket.create_connection((host, port), timeout=timeout)
        transport = paramiko.Transport(sock)
        transport.start_client()

        pkey = None
        if key_file and os.path.isfile(key_file):
            pkey = paramiko.PKey.from_path(key_file, passphrase=passphrase)

        if pkey:
            transport.auth_publickey(username, pkey)
        elif password:
            transport.auth_password(username, password)
        else:
            # Try SSH agent
            agent = paramiko.Agent()
            agent_keys = agent.get_keys()
            if not agent_keys:
                agent.close()
                raise ValueError("No authentication method provided (no password, key file, or SSH agent keys)")
            authenticated = False
            for agent_key in agent_keys:
                try:
                    transport.auth_publickey(username, agent_key)
                    authenticated = True
                    break
                except paramiko.AuthenticationException:
                    continue
            agent.close()
            if not authenticated:
                raise paramiko.AuthenticationException("All SSH agent keys were rejected")

        transport.set_keepalive(30)
        sftp = paramiko.SFTPClient.from_transport(transport)

        with self._lock:
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

    def normalize(self, path: str) -> str:
        """Resolve a remote path to its absolute form (calls server realpath)."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            return self._sftp.normalize(path)

    def listdir_attr(self, path: str) -> list:
        """List directory contents with attributes."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            return self._sftp.listdir_attr(path)

    def download(self, remote_path: str, local_path: str, progress_cb=None):
        """Download a remote file to a local path.

        Uses a dedicated SFTP channel so the main channel stays free for
        concurrent operations (directory listings etc.).  The manual read loop
        avoids paramiko's prefetch buffering, so cancellation via a
        TransferAborted exception from progress_cb stops the transfer
        immediately.
        """
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            transport = self._transport

        dl_sftp = paramiko.SFTPClient.from_transport(transport)
        try:
            try:
                file_size = dl_sftp.stat(remote_path).st_size
            except OSError:
                file_size = 0

            with dl_sftp.open(remote_path, "rb") as fr:
                with open(local_path, "wb") as fl:
                    received = 0
                    while True:
                        chunk = fr.read(32768)
                        if not chunk:
                            break
                        fl.write(chunk)
                        received += len(chunk)
                        if progress_cb:
                            progress_cb(received, file_size)
        finally:
            dl_sftp.close()

    def download_recursive(self, remote_path: str, local_path: str, progress_cb=None):
        """Download a remote file or directory tree to a local path."""
        with self._lock:
            if not self._sftp:
                raise RuntimeError("Not connected")
            transport = self._transport
            remote_stat = self._sftp.stat(remote_path)

        if stat.S_ISDIR(remote_stat.st_mode):
            Path(local_path).mkdir(parents=True, exist_ok=True)
            dl_sftp = paramiko.SFTPClient.from_transport(transport)
            try:
                self._download_dir_unlocked(dl_sftp, remote_path, local_path, progress_cb)
            finally:
                dl_sftp.close()
        else:
            self.download(remote_path, local_path, progress_cb=progress_cb)

    def _download_dir_unlocked(self, dl_sftp, remote_path: str, local_path: str, progress_cb=None):
        Path(local_path).mkdir(parents=True, exist_ok=True)
        for attr in dl_sftp.listdir_attr(remote_path):
            child_remote = f"{remote_path.rstrip('/')}/{attr.filename}"
            child_local = os.path.join(local_path, attr.filename)
            if stat.S_ISDIR(attr.st_mode):
                self._download_dir_unlocked(dl_sftp, child_remote, child_local, progress_cb)
            else:
                file_size = attr.st_size or 0
                with dl_sftp.open(child_remote, "rb") as fr:
                    with open(child_local, "wb") as fl:
                        received = 0
                        while True:
                            chunk = fr.read(32768)
                            if not chunk:
                                break
                            fl.write(chunk)
                            received += len(chunk)
                            if progress_cb:
                                progress_cb(received, file_size)

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

    def exec_command(self, command: str, timeout: float = 60) -> tuple[int, str, str]:
        """Execute a command on the remote server via SSH.

        Returns (exit_status, stdout, stderr).
        """
        with self._lock:
            if not self._transport or not self._transport.is_active():
                raise RuntimeError("Not connected")
            transport = self._transport

        channel = transport.open_session()
        try:
            channel.settimeout(timeout)
            channel.exec_command(command)

            # Read all output then wait for exit
            stdout_chunks = []
            stderr_chunks = []

            while not channel.exit_status_ready():
                if channel.recv_ready():
                    data = channel.recv(65536)
                    if data:
                        stdout_chunks.append(data.decode("utf-8", errors="replace"))
                if channel.recv_stderr_ready():
                    data = channel.recv_stderr(65536)
                    if data:
                        stderr_chunks.append(data.decode("utf-8", errors="replace"))
                time.sleep(0.05)

            # Drain remaining data after exit
            while True:
                data = channel.recv(65536)
                if not data:
                    break
                stdout_chunks.append(data.decode("utf-8", errors="replace"))
            while True:
                data = channel.recv_stderr(65536)
                if not data:
                    break
                stderr_chunks.append(data.decode("utf-8", errors="replace"))

            exit_status = channel.recv_exit_status()
            return exit_status, "".join(stdout_chunks), "".join(stderr_chunks)
        finally:
            channel.close()

    def can_write_dir(self, path: str) -> bool:
        """Check if the current user can write to a remote directory."""
        try:
            st = self.stat(path)
            mode = st.st_mode
            if not stat.S_ISDIR(mode):
                return False
            # Owner write bit is a reasonable heuristic; the server may also
            # grant access via group or other bits, but checking uid/gid
            # reliably is complex.  Use a practical test instead.
            return bool(mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        except Exception:
            return False

    def can_read(self, path: str) -> bool:
        """Check if the current user can read a remote path."""
        try:
            st = self.stat(path)
            mode = st.st_mode
            return bool(mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH))
        except Exception:
            return False

    def is_dir(self, path: str) -> bool:
        """Check if remote path is a directory."""
        try:
            st = self.stat(path)
            return stat.S_ISDIR(st.st_mode)
        except Exception:
            return False
