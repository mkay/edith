# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

"""FTP/FTPS client with the same interface as SftpClient."""

import functools
import os
import socket
import ssl
import stat as stat_module
import tempfile
import threading
from ftplib import FTP, FTP_TLS, error_perm, error_reply, error_temp  # nosec B402
from io import BytesIO
from pathlib import Path

from edith.services.local_files import atomic_write
from edith.services.transfer_queue import TransferAborted


class _FTP_TLS(FTP_TLS):
    """FTP_TLS that resumes the control connection's TLS session on data
    connections.

    ftplib wraps each data socket as a fresh TLS session. vsftpd refuses that
    by default (require_ssl_reuse=YES), as do some ProFTPD and Pure-FTPd
    setups, so listings and transfers fail there. Resuming the control
    session is what FileZilla and every other client does.
    """

    def ntransfercmd(self, cmd, rest=None):
        conn, size = FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(
                conn, server_hostname=self.host, session=self.sock.session
            )
        return conn, size


class _ImplicitFTP_TLS(_FTP_TLS):
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


def _unverified_context():
    """A context that accepts any certificate, to look at one that failed.

    Only used to compare the certificate against one the user trusted.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # nosec B501
    return context


class _DataChannel:
    """The handle an FTP transfer registers with the transfer queue.

    Cancelling closes what was registered. For FTP that must be the data
    connection alone: the control connection carries the session, and
    closing it would cost the whole connection just to stop one file.
    """

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        # socket.socket.shutdown, not SSLSocket's: that one also drops the
        # SSL object the worker thread may be reading through.
        #
        # SHUT_RD, not SHUT_RDWR: it wakes the blocked recv() as a plain end
        # of file. SHUT_RDWR makes it fail with EPIPE instead, and on a TLS
        # socket with a timeout that error resurfaces on the next read of
        # the *control* connection, which then looks dead. Only downloads
        # register a channel; an upload is cancelled between chunks.
        try:
            socket.socket.shutdown(self._conn, socket.SHUT_RD)
        except OSError:
            pass  # already closed


class FtpFileAttr:
    """Mimics paramiko SFTPAttributes for FTP directory entries."""

    def __init__(self, filename, facts, owner="", group="", is_link=False):
        self.filename = filename
        self.st_size = int(facts.get("size", 0))
        self.st_mode = 0
        # The caller resolves a link's type and passes it in facts["type"].
        self.is_link = is_link

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

        # Textual names: explicit (parsed from LIST) or MLSD *name facts.
        self.ownername = owner or facts.get("unix.ownername", "") or facts.get("unix.owner", "")
        self.groupname = group or facts.get("unix.groupname", "") or facts.get("unix.group", "")
        # Numeric ids only when the server actually sent them; otherwise leave
        # unset (None) so ownership renders as "unknown" rather than root (0).
        self.st_uid = int(facts["unix.uid"]) if "unix.uid" in facts else None
        self.st_gid = int(facts["unix.gid"]) if "unix.gid" in facts else None
        self.st_atime = self.st_mtime
        # MLSD/MLST's id for the file itself (device and inode on Unix), the
        # same for every path that reaches it.
        self.unique = facts.get("unique", "")


class FtpClient:
    """Thread-safe FTP client matching the SftpClient interface."""

    # Seconds between keep-alive NOOPs on an idle control connection.
    _KEEPALIVE_INTERVAL = 30

    def __init__(self):
        self._ftp = None
        self._lock = threading.Lock()
        self._use_tls = False
        self._keepalive_thread = None
        self._keepalive_stop = None
        # Set when the control connection is found dead; see connection_lost.
        self._lost = False

    @property
    def connection_lost(self) -> bool:
        """True once the control connection is known to be dead.

        A flag rather than a NOOP, so the main loop can poll it without
        waiting on the lock behind a transfer. It is set by the keep-alive
        thread when its NOOP fails, and by any operation that fails on the
        connection itself (see _marks_lost).
        """
        return self._lost

    def _is_connection_error(self, error) -> bool:
        if isinstance(error, (EOFError, ConnectionError,
                              ssl.SSLEOFError, ssl.SSLZeroReturnError)):
            return True
        # 421: the server is closing the control connection (idle timeout).
        if isinstance(error, error_temp) and str(error).startswith("421"):
            return True
        # ftplib.close() leaves the object with no socket; every later
        # command fails on that.
        return self._ftp is not None and self._ftp.sock is None

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

        TLS certificates are verified against the system trust store. One
        that fails is accepted only if the user trusted that exact
        certificate for this host and port before; otherwise this raises
        trust_store.UntrustedCertificate, before any credentials are sent.
        """
        from edith.services import trust_store

        try:
            ftp, tls = self._open(host, port, encryption, timeout,
                                  ssl.create_default_context())
        except ssl.SSLCertVerificationError as e:
            reason = e.verify_message or str(e)
            # Connect again without verification to see the certificate,
            # and keep this connection only if it is the trusted one: the
            # check is on the certificate this session actually uses.
            ftp, tls = self._open(host, port, encryption, timeout,
                                  _unverified_context())
            if not tls:
                # It offered TLS a moment ago: refusing it now is a downgrade,
                # not "TLS unavailable".
                ftp.close()
                raise
            der = ftp.sock.getpeercert(binary_form=True)
            if not trust_store.is_trusted_certificate(host, port, der):
                ftp.close()
                raise trust_store.UntrustedCertificate(host, port, der, reason)

        # FTP_TLS.login() would send AUTH again on a plain socket.
        if tls:
            ftp.login(username or "anonymous", password or "")
            ftp.prot_p()
        else:
            FTP.login(ftp, username or "anonymous", password or "")
        self._use_tls = tls

        # Check for MLSD support
        self._has_mlsd = self._check_mlsd(ftp)

        with self._lock:
            self._ftp = ftp

        self._start_keepalive()

    def _start_keepalive(self):
        """Start a background thread that sends NOOP on an idle connection.

        Unlike paramiko's transport (which has set_keepalive), ftplib has no
        built-in keep-alive, so an idle control connection gets dropped by the
        server's idle timeout. This thread sends a periodic NOOP to keep it
        alive, mirroring SftpClient's transport.set_keepalive(30).
        """
        self._keepalive_stop = threading.Event()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop,
            args=(self._keepalive_stop,),
            daemon=True,
        )
        self._keepalive_thread.start()

    def _keepalive_loop(self, stop: threading.Event):
        while not stop.wait(self._KEEPALIVE_INTERVAL):
            # Try the lock without blocking: if a transfer or command is in
            # flight, traffic is already flowing and no NOOP is needed.
            if not self._lock.acquire(blocking=False):
                continue
            try:
                if self._ftp is None:
                    return
                if not self._ping():
                    # Connection is gone or unhealthy; stop pinging it.
                    self._lost = True
                    return
            finally:
                self._lock.release()

    def _ping(self) -> bool:
        """NOOP the control connection; True if the server answered at all.

        Some servers (HostEurope) refuse NOOP with "550 Operation not
        permitted". That is still a reply, so the connection is alive: only
        a transport failure or a 421 means it is gone. Caller holds the lock.
        """
        try:
            self._ftp.voidcmd("NOOP")
            return True
        except (error_perm, error_temp) as e:
            return not self._is_connection_error(e)
        except Exception:
            return False

    def _stop_keepalive(self):
        if self._keepalive_stop:
            self._keepalive_stop.set()
        self._keepalive_thread = None
        self._keepalive_stop = None

    @staticmethod
    def _open(host, port, encryption, timeout, context):
        """Connect and set up TLS as `encryption` asks, short of logging in.

        Returns (ftp, tls) where tls tells whether the control connection is
        encrypted. A failed TLS handshake raises (and closes the socket).
        """
        if encryption == "implicit":
            ftp = _ImplicitFTP_TLS(context=context, timeout=timeout)
        elif encryption in ("explicit_required", "explicit_optional"):
            ftp = _FTP_TLS(context=context, timeout=timeout)
        else:
            ftp = FTP(timeout=timeout)  # nosec B321

        try:
            ftp.connect(host, port)

            tls = encryption == "implicit"
            if encryption == "explicit_required":
                ftp.auth()
                tls = True
            elif encryption == "explicit_optional":
                try:
                    ftp.auth()
                    tls = True
                except (error_perm, error_reply, error_temp):
                    # The server refused AUTH (500/502/504/534…): carry on
                    # in plain text, as "if available" promises. A failed
                    # handshake (OSError, including a certificate that
                    # doesn't verify) is not a refusal and still aborts.
                    pass
        except BaseException:
            ftp.close()
            raise
        return ftp, tls

    @staticmethod
    def _check_mlsd(ftp):
        try:
            feat = ftp.sendcmd("FEAT")
            return "MLST" in feat.upper()
        except Exception:
            return False

    def close(self):
        self._stop_keepalive()
        with self._lock:
            if self._ftp:
                try:
                    self._ftp.quit()
                except OSError:
                    try:
                        self._ftp.close()
                    except OSError:
                        pass
                self._ftp = None

    @property
    def is_connected(self) -> bool:
        with self._lock:
            if not self._ftp:
                return False
            return self._ping()

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
            is_link = _is_link_type(facts)
            if is_link:
                facts = self._resolve_link_unlocked(path, name, facts)
            results.append(FtpFileAttr(name, facts, is_link=is_link))
        return results

    def _listdir_list(self, path: str) -> list:
        """Fallback parser for servers without MLSD (Unix-style LIST)."""
        results = []
        for name, facts, owner, group in self._list_unlocked(path):
            is_link = _is_link_type(facts)
            if is_link:
                facts = self._resolve_link_unlocked(path, name, facts)
            results.append(FtpFileAttr(name, facts, owner=owner, group=group,
                                       is_link=is_link))
        return results

    def _list_unlocked(self, path: str) -> list:
        """LIST path, parsed into (name, facts, owner, group) tuples.

        Links come back with facts["type"] == "OS.unix=symlink"; their type
        is unknown until resolved.
        """
        lines = []
        self._ftp.retrlines(f"LIST {path}", lines.append)
        results = []
        for line in lines:
            parsed = self._parse_list_line(line)
            if parsed and parsed[0] not in (".", ".."):
                results.append(parsed)
        return results

    @classmethod
    def _parse_list_line(cls, line: str):
        parts = line.split(None, 8)
        if len(parts) < 9:
            return None
        perms, owner, group, name = parts[0], parts[2], parts[3], parts[8]
        facts = {"size": parts[4] if parts[4].isdigit() else "0"}
        if perms.startswith("l"):
            # "name -> target". A name containing " -> " is ambiguous; the
            # first one is the likelier split.
            name = name.split(" -> ", 1)[0]
            facts["type"] = "OS.unix=symlink"
        elif perms.startswith("d"):
            facts["type"] = "dir"
        else:
            facts["type"] = "file"
        mode = cls._parse_permission_string(perms[1:10]) if len(perms) >= 10 else 0
        if mode:
            facts["unix.mode"] = oct(mode)[2:]
        return name, facts, owner, group

    def _resolve_link_unlocked(self, parent: str, name: str, facts: dict) -> dict:
        """Return facts with a link's type replaced by its target's.

        FTP can't stat through a link, but CWD follows it: if that works the
        target is a folder, otherwise a file (or the link dangles). LIST gives
        the link's own size, so a file's comes from SIZE.
        """
        child = f"{parent.rstrip('/')}/{name}"
        facts = dict(facts)
        if self._is_dir_unlocked(child):
            facts["type"] = "dir"
        else:
            facts["type"] = "file"
            try:
                self._ftp.voidcmd("TYPE I")
                size = self._ftp.size(child)
                if size is not None:
                    facts["size"] = str(size)
            except (OSError, error_perm):
                pass  # dangling: keep the link's own
        return facts

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

    def download(self, remote_path: str, local_path: str, progress_cb=None,
                 cancel_event=None, set_channel=None):
        """Download a remote file to a local path.

        cancel_event/set_channel mirror SftpClient.download(): the cancel
        event is checked between chunks and the connection is registered for
        force-close so a stalled transfer can be interrupted.
        """
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            self._retr_unlocked(remote_path, local_path,
                                self._size_unlocked(remote_path),
                                progress_cb, cancel_event, set_channel)

    def download_recursive(self, remote_path: str, local_path: str, progress_cb=None,
                           cancel_event=None, set_channel=None):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            if self._is_dir_unlocked(remote_path):
                self._download_dir_unlocked(remote_path, local_path, progress_cb,
                                            cancel_event, set_channel)
            else:
                Path(local_path).parent.mkdir(parents=True, exist_ok=True)
                self._retr_unlocked(remote_path, local_path,
                                    self._size_unlocked(remote_path),
                                    progress_cb, cancel_event, set_channel)

    def download_many(self, items, progress_cb=None, cancel_event=None, set_channel=None):
        """Download several remote paths.

        Mirrors SftpClient.download_many() so callers need no protocol
        branch.  FTP has no channels to conserve, so this is a plain loop
        over the single control connection.
        """
        for remote_path, local_path in items:
            if cancel_event is not None and cancel_event.is_set():
                raise TransferAborted()
            self.download_recursive(remote_path, local_path, progress_cb=progress_cb,
                                    cancel_event=cancel_event, set_channel=set_channel)

    def _download_dir_unlocked(self, remote_path: str, local_path: str, progress_cb=None,
                               cancel_event=None, set_channel=None):
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
                self._download_dir_unlocked(child_remote, child_local, progress_cb,
                                            cancel_event, set_channel)
            else:
                self._retr_unlocked(child_remote, child_local,
                                    int(facts.get("size", 0)),
                                    progress_cb, cancel_event, set_channel)

    _BLOCKSIZE = 1 << 16

    def _retr_unlocked(self, remote_path, local_path, file_size, progress_cb,
                       cancel_event, set_channel=None):
        """RETR one file into local_path, which appears only once complete."""
        received = 0
        with atomic_write(local_path) as f:
            def on_data(chunk):
                nonlocal received
                f.write(chunk)
                received += len(chunk)
                if progress_cb:
                    progress_cb(received, file_size)
            self._transfer_unlocked(f"RETR {remote_path}", on_data, None,
                                    cancel_event, set_channel)

    def _transfer_unlocked(self, cmd, on_data, source, cancel_event, set_channel):
        """Run one RETR (on_data gets each chunk) or STOR (source is read).

        ftplib's retrbinary()/storbinary() let a cancel or a local error
        escape without reading the server's reply to the transfer, so the
        next command would get that stale 426 or 226 instead of its own.
        Doing the data connection here keeps the control connection in step
        whatever happens, and lets the queue cancel the data connection
        alone (see _DataChannel).
        """
        self._ftp.voidcmd("TYPE I")
        conn = self._ftp.transfercmd(cmd)
        if set_channel:
            set_channel(_DataChannel(conn))

        def cancelled():
            return cancel_event is not None and cancel_event.is_set()

        complete = False
        try:
            try:
                if source is None:
                    while True:
                        if cancelled():
                            raise TransferAborted()
                        chunk = conn.recv(self._BLOCKSIZE)
                        if not chunk:
                            break
                        on_data(chunk)
                else:
                    while True:
                        if cancelled():
                            raise TransferAborted()
                        chunk = source.read(self._BLOCKSIZE)
                        if not chunk:
                            break
                        conn.sendall(chunk)
                        if on_data:
                            on_data(chunk)
            except OSError:
                # The queue shut the socket down under a blocked recv/send.
                if cancelled():
                    raise TransferAborted()
                raise
            # A cancel that only woke recv() reads as a clean end of file.
            if cancelled():
                raise TransferAborted()
            if isinstance(conn, ssl.SSLSocket):
                conn.unwrap()
            complete = True
        finally:
            conn.close()
            if set_channel:
                set_channel(None)
            if complete:
                self._ftp.voidresp()
            else:
                self._discard_transfer_reply()

    def _discard_transfer_reply(self):
        """Read the reply to a transfer cut short (426, or a late 226).

        If none comes the connection is no longer in step, so it is marked
        lost rather than left to answer the next command with this reply.
        """
        try:
            self._ftp.getresp()
        except (error_perm, error_reply, error_temp):
            pass  # 426/451: the server saw the abort, which is the point
        except Exception:
            self._lost = True

    def _listdir_list_raw(self, path: str) -> list:
        """LIST fallback returning (name, facts) tuples like mlsd.

        Links keep their unresolved link type, so recursive operations treat
        them as files: a delete removes the link, not what it points to.
        """
        return [(name, facts) for name, facts, _o, _g in self._list_unlocked(path)]

    def upload(self, local_path: str, remote_path: str, progress_cb=None, overwrite=False):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            if not overwrite and self._exists_unlocked(remote_path):
                name = remote_path.rsplit("/", 1)[-1]
                raise FileExistsError(f"'{name}' already exists on the server")
            file_size = os.path.getsize(local_path)
            sent = 0

            def on_sent(chunk):
                nonlocal sent
                sent += len(chunk)
                if progress_cb:
                    progress_cb(sent, file_size)

            with open(local_path, "rb") as f:
                self._transfer_unlocked(f"STOR {remote_path}", on_sent, f,
                                        None, None)

    def stat(self, path: str):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            if self._has_mlsd:
                return self._mlst_unlocked(path)
            # Fallback: try SIZE + MDTM + check if directory
            found = False
            facts = {"size": "0", "type": "file"}
            try:
                self._ftp.voidcmd("TYPE I")
                size = self._ftp.size(path)
                if size is not None:
                    facts["size"] = str(size)
                found = True
            except (OSError, error_perm):
                pass
            try:
                mdtm_resp = self._ftp.sendcmd(f"MDTM {path}")
                # Response: "213 YYYYMMDDHHMMSS"
                if mdtm_resp.startswith("213 "):
                    facts["modify"] = mdtm_resp[4:].strip()
                    found = True
            except (OSError, error_perm):
                pass
            if self._is_dir_unlocked(path):
                facts["type"] = "dir"
                found = True
            if not found:
                raise FileNotFoundError(f"No such file or directory: '{path}'")
            return FtpFileAttr(path.rsplit("/", 1)[-1], facts)

    def _mlst_unlocked(self, path: str):
        """Stat one path with MLST.

        RFC 3659 ties MLST and MLSD together (the FEAT line is "MLST"), so a
        server listing with MLSD answers this too, without the full listing
        of the parent the window would otherwise fetch per open file per
        poll. Like SFTP's stat(), this follows a link.
        """
        name = path.rstrip("/").rsplit("/", 1)[-1] or "/"
        try:
            resp = self._ftp.sendcmd(f"MLST {path}")
        except error_perm as e:
            if str(e).startswith("550"):
                raise FileNotFoundError(f"No such file or directory: '{path}'")
            raise
        # 250-Start of list
        #  type=file;size=12;modify=20260101120000; /path/name
        # 250 End
        for line in resp.splitlines()[1:]:
            if not line.startswith(" "):
                continue
            facts_text, _sep, _name = line[1:].partition(" ")
            facts = {}
            for fact in facts_text.rstrip(";").split(";"):
                key, eq, value = fact.partition("=")
                if eq:
                    facts[key.lower()] = value
            if _is_link_type(facts):
                parent = path.rstrip("/").rsplit("/", 1)[0] or "/"
                return FtpFileAttr(
                    name, self._resolve_link_unlocked(parent, name, facts),
                    is_link=True,
                )
            return FtpFileAttr(name, facts)
        raise FileNotFoundError(f"No such file or directory: '{path}'")

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
        """Copy by downloading and re-uploading.

        One control connection can't RETR and STOR at once, so the file goes
        through an unnamed temp file on disk rather than memory, which a
        large file would exhaust.
        """
        with tempfile.TemporaryFile(prefix="edith-copy-") as buf:
            self._ftp.retrbinary(f"RETR {src}", buf.write)
            buf.seek(0)
            self._ftp.storbinary(f"STOR {dst}", buf)

    def _copy_recursive_unlocked(self, src: str, dst: str):
        if not self._is_dir_unlocked(src):
            self._copy_file_unlocked(src, dst)
            return
        top = ""
        if self._has_mlsd:
            try:
                top = self._mlst_unlocked(src).unique
            except (OSError, error_perm):
                pass
        skipped = []
        self._copy_dir_unlocked(src, dst, {top} if top else set(), skipped)
        if skipped:
            names = ", ".join(p.rsplit("/", 1)[-1] for p in skipped[:5])
            more = f" and {len(skipped) - 5} more" if len(skipped) > 5 else ""
            raise OSError(
                f"Copied, except {len(skipped)} linked folder(s), which FTP "
                f"can't copy as links: {names}{more}"
            )

    def _copy_dir_unlocked(self, src, dst, ancestors, skipped):
        """Copy folder src's contents to dst.

        FTP can't create links, and a link to a folder that is followed can
        lead back to a parent and never end. So links to folders are skipped
        (and reported), and on servers whose MLSD follows links silently, a
        folder whose unique id matches a folder above it is skipped too.
        """
        try:
            self._ftp.mkd(dst)
        except (OSError, error_perm):
            pass  # directory may already exist
        if self._has_mlsd:
            entries = list(self._ftp.mlsd(src))
        else:
            entries = self._listdir_list_raw(src)
        for name, facts in entries:
            if name in (".", ".."):
                continue
            child_src = f"{src.rstrip('/')}/{name}"
            child_dst = f"{dst.rstrip('/')}/{name}"
            if _is_link_type(facts):
                facts = self._resolve_link_unlocked(src, name, facts)
                if facts["type"] == "dir":
                    skipped.append(child_src)
                    continue
            entry_type = facts.get("type", "file").lower()
            if entry_type in ("cdir", "pdir"):
                continue
            if entry_type == "dir":
                unique = facts.get("unique", "")
                if unique and unique in ancestors:
                    skipped.append(child_src)
                    continue
                self._copy_dir_unlocked(
                    child_src, child_dst,
                    ancestors | {unique} if unique else ancestors, skipped,
                )
            else:
                self._copy_file_unlocked(child_src, child_dst)

    def create_file(self, path: str):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            self._ftp.storbinary(f"STOR {path}", BytesIO(b""))

    def upload_directory(self, local_dir: str, remote_dir: str, overwrite=False):
        with self._lock:
            if not self._ftp:
                raise RuntimeError("Not connected")
            if not overwrite and self._exists_unlocked(remote_dir):
                name = remote_dir.rsplit("/", 1)[-1]
                raise FileExistsError(f"'{name}' already exists on the server")
            self._upload_directory_unlocked(local_dir, remote_dir)

    def _upload_directory_unlocked(self, local_dir: str, remote_dir: str):
        try:
            self._ftp.mkd(remote_dir)
        except OSError:
            pass  # directory may already exist
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
            self._ftp.voidcmd("TYPE I")
            self._ftp.size(path)
            return True
        except (OSError, error_perm):
            pass
        return self._is_dir_unlocked(path)

    def _size_unlocked(self, path: str) -> int:
        try:
            self._ftp.voidcmd("TYPE I")
            size = self._ftp.size(path)
            return size if size is not None else 0
        except Exception:
            return 0


def _is_link_type(facts: dict) -> bool:
    """True for a symlink entry: our LIST parser's "OS.unix=symlink", or the
    MLSD types ProFTPD sends ("OS.unix=symlink", "OS.unix=slink:/target")."""
    entry_type = facts.get("type", "").lower()
    return entry_type.startswith(("os.unix=symlink", "os.unix=slink"))


def _marks_lost(method):
    """Flag the connection lost when an operation fails on the connection
    itself, not on the request (a missing file, a refused rename)."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as e:
            if self._is_connection_error(e):
                self._lost = True
            raise
    return wrapper


for _name in (
    "normalize", "listdir_attr", "download", "download_recursive", "upload",
    "stat", "mkdir", "rename", "chmod", "remove", "rmdir", "rmdir_recursive",
    "copy_remote", "copy_remote_recursive", "create_file", "upload_directory",
):
    setattr(FtpClient, _name, _marks_lost(getattr(FtpClient, _name)))
del _name
