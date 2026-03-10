"""FTP/FTPS client with the same interface as SftpClient."""

import os
import socket
import stat as stat_module
import threading
from ftplib import FTP, FTP_TLS
from io import BytesIO
from pathlib import Path


class _ImplicitFTP_TLS(FTP_TLS):
    """FTP_TLS subclass for implicit FTPS (TLS on connect, port 990)."""

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        if host:
            self.host = host
        if port:
            self.port = port
        if timeout != -999:
            self.timeout = timeout
        if source_address is not None:
            self.source_address = source_address

        sock = socket.create_connection(
            (self.host, self.port), self.timeout, self.source_address
        )
        self.af = sock.family
        self.sock = self.context.wrap_socket(sock, server_hostname=self.host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome


class FtpFileAttr:
    """Mimics paramiko SFTPAttributes for FTP directory entries."""

    def __init__(self, filename, facts):
        self.filename = filename
        self.st_size = int(facts.get("size", 0))
        self.st_mode = 0

        entry_type = facts.get("type", "file").lower()
        if entry_type in ("dir", "cdir", "pdir"):
            self.st_mode = stat_module.S_IFDIR | 0o755
        else:
            self.st_mode = stat_module.S_IFREG | 0o644

        modify = facts.get("modify")
        if modify:
            import calendar
            import time
            try:
                t = time.strptime(modify, "%Y%m%d%H%M%S")
                self.st_mtime = calendar.timegm(t)
            except ValueError:
                self.st_mtime = 0
        else:
            self.st_mtime = 0

        unix_mode = facts.get("unix.mode")
        if unix_mode:
            try:
                mode_bits = int(unix_mode, 8)
                if entry_type in ("dir", "cdir", "pdir"):
                    self.st_mode = stat_module.S_IFDIR | mode_bits
                else:
                    self.st_mode = stat_module.S_IFREG | mode_bits
            except ValueError:
                pass

        self.st_uid = int(facts.get("unix.uid", 0))
        self.st_gid = int(facts.get("unix.gid", 0))
        self.st_atime = self.st_mtime


class FtpClient:
    """Thread-safe FTP client matching the SftpClient interface."""

    def __init__(self):
        self._ftp = None
        self._lock = threading.Lock()
        self._use_tls = False

    def connect(
        self,
        host: str,
        port: int = 21,
        username: str = "",
        password: str | None = None,
        encryption: str = "none",
        timeout: float = 10,
        **kwargs,
    ):
        """Connect to an FTP server.

        encryption: "none", "explicit_optional", "explicit_required", "implicit"
        """
        self._use_tls = encryption != "none"

        if encryption == "implicit":
            ftp = _ImplicitFTP_TLS(timeout=timeout)
        elif encryption in ("explicit_required", "explicit_optional"):
            ftp = FTP_TLS(timeout=timeout)
        else:
            ftp = FTP(timeout=timeout)

        ftp.connect(host, port)

        if encryption == "explicit_required":
            ftp.auth()
        elif encryption == "explicit_optional":
            try:
                ftp.auth()
            except OSError:
                pass  # server doesn't support TLS, continue unencrypted

        ftp.login(username or "anonymous", password or "")

        if encryption != "none" and isinstance(ftp, FTP_TLS):
            ftp.prot_p()

        # Check for MLSD support
        self._has_mlsd = self._check_mlsd(ftp)

        with self._lock:
            self._ftp = ftp

    @staticmethod
    def _check_mlsd(ftp):
        try:
            feat = ftp.sendcmd("FEAT")
            return "MLST" in feat.upper()
        except Exception:
            return False

    def close(self):
        with self._lock:
            if self._ftp:
                try:
                    self._ftp.quit()
                except Exception:
                    try:
                        self._ftp.close()
                    except Exception:
                        pass
                self._ftp = None

    @property
    def is_connected(self) -> bool:
        with self._lock:
            if not self._ftp:
                return False
            try:
                self._ftp.voidcmd("NOOP")
                return True
            except Exception:
                return False

    def normalize(self, path: str) -> str:
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            if path == ".":
                return self._ftp.pwd()
            # FTP has no realpath; resolve relative to cwd
            old = self._ftp.pwd()
            try:
                self._ftp.cwd(path)
                resolved = self._ftp.pwd()
            finally:
                self._ftp.cwd(old)
            return resolved

    def listdir_attr(self, path: str) -> list:
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            if self._has_mlsd:
                return self._listdir_mlsd(path)
            return self._listdir_list(path)

    def _listdir_mlsd(self, path: str) -> list:
        results = []
        for name, facts in self._ftp.mlsd(path):
            if name in (".", ".."):
                continue
            results.append(FtpFileAttr(name, facts))
        return results

    def _listdir_list(self, path: str) -> list:
        """Fallback parser for servers without MLSD (Unix-style LIST)."""
        lines = []
        self._ftp.retrlines(f"LIST {path}", lines.append)
        results = []
        for line in lines:
            parts = line.split(None, 8)
            if len(parts) < 9:
                continue
            name = parts[8]
            if name in (".", ".."):
                continue
            perms = parts[0]
            size = int(parts[4]) if parts[4].isdigit() else 0
            facts = {"size": str(size)}
            if perms.startswith("d"):
                facts["type"] = "dir"
            else:
                facts["type"] = "file"
            # Parse permission bits from rwxrwxrwx string
            mode = self._parse_permission_string(perms[1:10]) if len(perms) >= 10 else 0
            if mode:
                facts["unix.mode"] = oct(mode)[2:]
            results.append(FtpFileAttr(name, facts))
        return results

    @staticmethod
    def _parse_permission_string(s: str) -> int:
        """Convert 'rwxrwxrwx' to an octal mode integer."""
        if len(s) != 9:
            return 0
        mode = 0
        mapping = [
            (0o400, 0), (0o200, 1), (0o100, 2),
            (0o040, 3), (0o020, 4), (0o010, 5),
            (0o004, 6), (0o002, 7), (0o001, 8),
        ]
        for bit, idx in mapping:
            if s[idx] != "-":
                mode |= bit
        return mode

    def download(self, remote_path: str, local_path: str, progress_cb=None):
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            file_size = self._size_unlocked(remote_path)
            received = 0
            with open(local_path, "wb") as f:
                def callback(chunk):
                    nonlocal received
                    f.write(chunk)
                    received += len(chunk)
                    if progress_cb:
                        progress_cb(received, file_size)
                self._ftp.retrbinary(f"RETR {remote_path}", callback)

    def download_recursive(self, remote_path: str, local_path: str, progress_cb=None):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            if self._is_dir_unlocked(remote_path):
                self._download_dir_unlocked(remote_path, local_path, progress_cb)
            else:
                file_size = self._size_unlocked(remote_path)
                Path(local_path).parent.mkdir(parents=True, exist_ok=True)
                received = 0
                with open(local_path, "wb") as f:
                    def callback(chunk):
                        nonlocal received
                        f.write(chunk)
                        received += len(chunk)
                        if progress_cb:
                            progress_cb(received, file_size)
                    self._ftp.retrbinary(f"RETR {remote_path}", callback)

    def _download_dir_unlocked(self, remote_path: str, local_path: str, progress_cb=None):
        Path(local_path).mkdir(parents=True, exist_ok=True)
        if self._has_mlsd:
            entries = list(self._ftp.mlsd(remote_path))
        else:
            entries = self._listdir_list_raw(remote_path)

        for name, facts in entries:
            if name in (".", ".."):
                continue
            child_remote = f"{remote_path.rstrip('/')}/{name}"
            child_local = os.path.join(local_path, name)
            entry_type = facts.get("type", "file").lower()
            if entry_type in ("dir", "cdir", "pdir"):
                self._download_dir_unlocked(child_remote, child_local, progress_cb)
            else:
                file_size = int(facts.get("size", 0))
                received = [0]
                with open(child_local, "wb") as f:
                    def make_callback(r, s):
                        def callback(chunk):
                            f.write(chunk)
                            r[0] += len(chunk)
                            if progress_cb:
                                progress_cb(r[0], s)
                        return callback
                    self._ftp.retrbinary(f"RETR {child_remote}", make_callback(received, file_size))

    def _listdir_list_raw(self, path: str) -> list:
        """LIST fallback returning (name, facts) tuples like mlsd."""
        lines = []
        self._ftp.retrlines(f"LIST {path}", lines.append)
        results = []
        for line in lines:
            parts = line.split(None, 8)
            if len(parts) < 9:
                continue
            name = parts[8]
            facts = {"type": "dir" if parts[0].startswith("d") else "file",
                     "size": parts[4] if parts[4].isdigit() else "0"}
            results.append((name, facts))
        return results

    def upload(self, local_path: str, remote_path: str, progress_cb=None, overwrite=False):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            if not overwrite and self._exists_unlocked(remote_path):
                name = remote_path.rsplit("/", 1)[-1]
                raise FileExistsError(f"'{name}' already exists on the server")
            file_size = os.path.getsize(local_path)
            sent = 0
            with open(local_path, "rb") as f:
                if progress_cb:
                    def callback(chunk):
                        nonlocal sent
                        sent += len(chunk)
                        progress_cb(sent, file_size)
                    self._ftp.storbinary(f"STOR {remote_path}", f, callback=callback)
                else:
                    self._ftp.storbinary(f"STOR {remote_path}", f)

    def stat(self, path: str):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            if self._has_mlsd:
                parent = path.rsplit("/", 1)[0] or "/"
                name = path.rsplit("/", 1)[-1]
                for entry_name, facts in self._ftp.mlsd(parent):
                    if entry_name == name:
                        return FtpFileAttr(name, facts)
            # Fallback: try SIZE + check if directory
            facts = {"size": "0", "type": "file"}
            try:
                size = self._ftp.size(path)
                if size is not None:
                    facts["size"] = str(size)
            except Exception:
                pass
            if self._is_dir_unlocked(path):
                facts["type"] = "dir"
            return FtpFileAttr(path.rsplit("/", 1)[-1], facts)

    def mkdir(self, path: str):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            self._ftp.mkd(path)

    def rename(self, old_path: str, new_path: str):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            if self._exists_unlocked(new_path):
                name = new_path.rsplit("/", 1)[-1]
                raise FileExistsError(f"'{name}' already exists at the destination")
            self._ftp.rename(old_path, new_path)

    def chmod(self, path: str, mode: int):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            mode_str = oct(mode)[2:]
            resp = self._ftp.sendcmd(f"SITE CHMOD {mode_str} {path}")
            if not resp.startswith("2"):
                raise OSError(f"SITE CHMOD not supported by this server: {resp}")

    def remove(self, path: str):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            self._ftp.delete(path)

    def rmdir(self, path: str):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            self._ftp.rmd(path)

    def rmdir_recursive(self, path: str):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            self._rmdir_recursive_unlocked(path)

    def _rmdir_recursive_unlocked(self, path: str):
        if self._has_mlsd:
            entries = list(self._ftp.mlsd(path))
        else:
            entries = self._listdir_list_raw(path)
        for name, facts in entries:
            if name in (".", ".."):
                continue
            child = f"{path.rstrip('/')}/{name}"
            entry_type = facts.get("type", "file").lower()
            if entry_type in ("dir", "cdir", "pdir"):
                self._rmdir_recursive_unlocked(child)
            else:
                self._ftp.delete(child)
        self._ftp.rmd(path)

    def copy_remote(self, src: str, dst: str):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            if self._exists_unlocked(dst):
                name = dst.rsplit("/", 1)[-1]
                raise FileExistsError(f"'{name}' already exists at the destination")
            self._copy_file_unlocked(src, dst)

    def copy_remote_recursive(self, src: str, dst: str):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            if self._exists_unlocked(dst):
                name = dst.rsplit("/", 1)[-1]
                raise FileExistsError(f"'{name}' already exists at the destination")
            self._copy_recursive_unlocked(src, dst)

    def _copy_file_unlocked(self, src: str, dst: str):
        """Copy by downloading to memory and re-uploading."""
        buf = BytesIO()
        self._ftp.retrbinary(f"RETR {src}", buf.write)
        buf.seek(0)
        self._ftp.storbinary(f"STOR {dst}", buf)

    def _copy_recursive_unlocked(self, src: str, dst: str):
        if self._is_dir_unlocked(src):
            try:
                self._ftp.mkd(dst)
            except Exception:
                pass
            if self._has_mlsd:
                entries = list(self._ftp.mlsd(src))
            else:
                entries = self._listdir_list_raw(src)
            for name, facts in entries:
                if name in (".", ".."):
                    continue
                child_src = f"{src.rstrip('/')}/{name}"
                child_dst = f"{dst.rstrip('/')}/{name}"
                self._copy_recursive_unlocked(child_src, child_dst)
        else:
            self._copy_file_unlocked(src, dst)

    def create_file(self, path: str):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            self._ftp.storbinary(f"STOR {path}", BytesIO(b""))

    def upload_directory(self, local_dir: str, remote_dir: str):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            if self._exists_unlocked(remote_dir):
                name = remote_dir.rsplit("/", 1)[-1]
                raise FileExistsError(f"'{name}' already exists on the server")
            self._upload_directory_unlocked(local_dir, remote_dir)

    def _upload_directory_unlocked(self, local_dir: str, remote_dir: str):
        try:
            self._ftp.mkd(remote_dir)
        except Exception:
            pass
        for entry in os.listdir(local_dir):
            local_path = os.path.join(local_dir, entry)
            remote_path = f"{remote_dir.rstrip('/')}/{entry}"
            if os.path.isdir(local_path):
                self._upload_directory_unlocked(local_path, remote_path)
            else:
                with open(local_path, "rb") as f:
                    self._ftp.storbinary(f"STOR {remote_path}", f)

    def is_dir(self, path: str) -> bool:
        with self._lock:
            if not self._ftp:
                return False
            return self._is_dir_unlocked(path)

    def _is_dir_unlocked(self, path: str) -> bool:
        old = self._ftp.pwd()
        try:
            self._ftp.cwd(path)
            self._ftp.cwd(old)
            return True
        except Exception:
            return False

    def _exists_unlocked(self, path: str) -> bool:
        try:
            self._ftp.size(path)
            return True
        except Exception:
            pass
        return self._is_dir_unlocked(path)

    def _size_unlocked(self, path: str) -> int:
        try:
            size = self._ftp.size(path)
            return size if size is not None else 0
        except Exception:
            return 0
