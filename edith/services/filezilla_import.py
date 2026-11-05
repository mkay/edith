# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

import base64
import binascii
import os

import defusedxml.ElementTree as ET
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List

from edith.models.server import ServerInfo, FolderInfo


# FileZilla's ServerProtocol enum → (Edith protocol, FTP encryption).
# FTP=0 is "use explicit TLS if available", FileZilla's default; plain FTP is
# INSECURE_FTP=6. HTTP (2) and HTTPS (5) have no Edith equivalent.
_PROTOCOL_MAP = {
    "0": ("ftp", "explicit_optional"),
    "1": ("sftp", "none"),
    "3": ("ftp", "implicit"),
    "4": ("ftp", "explicit_required"),
    "6": ("ftp", "none"),
}

# FileZilla's LogonType enum: 0 anonymous, 1 normal, 2 ask, 3 interactive,
# 4 account, 5 key file, 6 profile.
_LOGON_ANONYMOUS = "0"
_LOGON_KEY = "5"


@dataclass
class SitemanagerImport:
    servers: List[ServerInfo] = field(default_factory=list)
    folders: List[FolderInfo] = field(default_factory=list)
    # {server.id: password} for every password FileZilla stored readably.
    passwords: Dict[str, str] = field(default_factory=dict)
    # Passwords FileZilla encrypted with its master password; unrecoverable
    # without it.
    encrypted_passwords: int = 0


def _read_password(elem: ET.Element):
    """Return (password or None, True if it was master-password encrypted).

    FileZilla 3.26+ writes <Pass encoding="base64">; older versions wrote the
    password in plain text with no attribute. With a master password set it
    writes encoding="crypt", which needs that password to decode.
    """
    pass_elem = elem.find("Pass")
    if pass_elem is None or not pass_elem.text:
        return None, False
    encoding = pass_elem.get("encoding", "")
    if encoding == "crypt":
        return None, True
    if encoding == "base64":
        try:
            return base64.b64decode(pass_elem.text, validate=True).decode("utf-8"), False
        except (binascii.Error, UnicodeDecodeError):
            return None, False
    if encoding:
        return None, False
    return pass_elem.text, False


def _key_needs_passphrase(key_file: str) -> bool:
    """Best-effort check whether a private key file is encrypted.

    Only reads the header: PEM keys say "ENCRYPTED", OpenSSH keys name their
    cipher, PuTTY keys have an "Encryption:" line. Anything unreadable counts
    as unencrypted; the connect dialog's error will say otherwise.
    """
    try:
        with open(os.path.expanduser(key_file), "rb") as f:
            head = f.read(8192)
    except OSError:
        return False
    text = head.decode("ascii", errors="replace")
    if "-----BEGIN OPENSSH PRIVATE KEY-----" in text:
        body = text.split("-----BEGIN OPENSSH PRIVATE KEY-----", 1)[1]
        body = "".join(body.split("-----", 1)[0].split())
        try:
            raw = base64.b64decode(body[:64] + "=" * (-len(body[:64]) % 4))
        except (binascii.Error, ValueError):
            return False
        # "openssh-key-v1\0", then a length-prefixed cipher name.
        magic = b"openssh-key-v1\x00"
        if not raw.startswith(magic) or len(raw) < len(magic) + 4:
            return False
        n = int.from_bytes(raw[len(magic):len(magic) + 4], "big")
        cipher = raw[len(magic) + 4:len(magic) + 4 + n]
        return cipher != b"none"
    if text.startswith("PuTTY-User-Key-File"):
        for line in text.splitlines():
            if line.startswith("Encryption:"):
                return line.split(":", 1)[1].strip() != "none"
        return False
    return "ENCRYPTED" in text


def _parse_server(elem: ET.Element, result: SitemanagerImport) -> ServerInfo:
    protocol, ftp_encryption = _PROTOCOL_MAP.get(
        (elem.findtext("Protocol") or "0"), ("ftp", "explicit_optional")
    )
    logon = (elem.findtext("Logontype") or "1").strip()
    username = elem.findtext("User") or ""
    if logon == _LOGON_ANONYMOUS:
        username = "anonymous"

    key_file = ""
    auth_method = "password"
    if protocol == "sftp" and logon == _LOGON_KEY:
        key_file = (elem.findtext("Keyfile") or "").strip()
        if key_file:
            auth_method = (
                "key+passphrase" if _key_needs_passphrase(key_file) else "key"
            )

    server = ServerInfo(
        name=elem.findtext("Name") or "",
        host=elem.findtext("Host") or "",
        port=int(elem.findtext("Port") or (22 if protocol == "sftp" else 21)),
        username=username,
        key_file=key_file,
        protocol=protocol,
        ftp_encryption=ftp_encryption,
        auth_method=auth_method,
        initial_directory=_decode_remote_dir(elem.findtext("RemoteDir") or ""),
    )

    # A key login keeps no password in FileZilla, and an anonymous one has
    # nothing worth storing.
    if auth_method == "password" and logon != _LOGON_ANONYMOUS:
        password, encrypted = _read_password(elem)
        if password:
            result.passwords[server.id] = password
        elif encrypted:
            result.encrypted_passwords += 1
    return server


def _decode_remote_dir(raw: str) -> str:
    """Decode FileZilla's RemoteDir format to a path.

    The format is "type prefixLength [prefix] (length segment)…", e.g.
    "1 0 3 var 3 www" for /var/www. The prefix is only used by VMS and is
    empty (length 0) for Unix paths. Lengths count characters, so a segment
    may contain spaces and can't be split on them.
    """
    raw = raw.strip()
    if not raw:
        return "/"

    pos = 0

    def read_int():
        nonlocal pos
        end = raw.find(" ", pos)
        if end == -1:
            end = len(raw)
        value = int(raw[pos:end])
        pos = end + 1
        return value

    def read_chars(n):
        nonlocal pos
        if n < 0 or pos + n > len(raw):
            raise ValueError("segment runs past the end")
        value = raw[pos:pos + n]
        pos += n + 1  # and the separating space
        return value

    try:
        read_int()                # path type
        prefix_length = read_int()
        if prefix_length:         # an empty prefix is just "0", no field
            read_chars(prefix_length)
        segments = []
        while pos < len(raw):
            segments.append(read_chars(read_int()))
    except ValueError:
        return "/"
    return "/" + "/".join(segments) if segments else "/"


def _folder_name(elem: ET.Element) -> str:
    """Extract folder name from a FileZilla <Folder> element.

    The folder name is the text content before the first child element.
    """
    return (elem.text or "").strip()


def _parse_container(container: ET.Element, result: SitemanagerImport, folder_id: str = ""):
    """Recursively parse <Servers> or <Folder> elements into result."""
    for child in container:
        if child.tag == "Server":
            server = _parse_server(child, result)
            server.folder_id = folder_id
            result.servers.append(server)
        elif child.tag == "Folder":
            folder = FolderInfo(name=_folder_name(child))
            result.folders.append(folder)
            _parse_container(child, result, folder.id)


def parse_sitemanager(path: Path) -> SitemanagerImport:
    """Parse a FileZilla sitemanager.xml."""
    tree = ET.parse(path)
    result = SitemanagerImport()
    servers_elem = tree.getroot().find("Servers")
    if servers_elem is not None:
        _parse_container(servers_elem, result)
    return result
