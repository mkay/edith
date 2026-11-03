# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Which SSH host keys and TLS certificates the user trusts.

SSH host keys come from ~/.ssh/known_hosts (read only; it belongs to
OpenSSH) and Edith's own known_hosts, which is where keys the user accepts
in Edith are written. A key marked @revoked in either file is refused.

TLS certificates are only consulted when a certificate fails normal
verification: the user can then trust that one certificate, by SHA-256
fingerprint, for that host and port. Shared hosts often present the
provider's certificate rather than one for the customer's domain, which
would otherwise make FTPS unusable there.
"""

import base64
import hashlib
import json
import logging
import os
import threading
from pathlib import Path

import paramiko
from paramiko.hostkeys import HostKeyEntry, InvalidHostKey

from edith.services.config import CONFIG_DIR
from edith.services.local_files import atomic_write_bytes

log = logging.getLogger(__name__)

USER_KNOWN_HOSTS = Path.home() / ".ssh" / "known_hosts"
KNOWN_HOSTS = CONFIG_DIR / "known_hosts"
TRUSTED_CERTS = CONFIG_DIR / "trusted_certificates.json"

_lock = threading.Lock()


# --- Errors the connect flow turns into questions --------------------------


class HostKeyError(Exception):
    """Base for host keys that need the user's attention."""

    def __init__(self, host, port, key, message):
        super().__init__(message)
        self.host = host
        self.port = port
        self.key = key

    @property
    def key_type(self) -> str:
        return key_type_label(self.key)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.key)


class HostKeyUnknown(HostKeyError):
    """No key is known for this host: ask before trusting it."""

    def __init__(self, host, port, key, other_types=()):
        super().__init__(
            host, port, key, f"The host key for {host_id(host, port)} is not known"
        )
        # Key types already known for the host, if the server offered a
        # different one.
        self.other_types = tuple(other_types)


class HostKeyChanged(HostKeyError):
    """The server's key differs from the known one: refuse."""

    def __init__(self, host, port, key, source):
        super().__init__(
            host, port, key, f"The host key for {host_id(host, port)} has changed"
        )
        self.source = source  # the known_hosts file holding the old key


class HostKeyRevoked(HostKeyError):
    """The server's key is marked @revoked: refuse."""

    def __init__(self, host, port, key, source):
        super().__init__(
            host, port, key, f"The host key for {host_id(host, port)} is revoked"
        )
        self.source = source


class UntrustedCertificate(Exception):
    """A TLS certificate failed verification and isn't trusted by the user."""

    def __init__(self, host, port, der: bytes, reason: str):
        super().__init__(f"The certificate of {host}:{port} is not trusted: {reason}")
        self.host = host
        self.port = port
        self.der = der
        self.reason = reason

    @property
    def fingerprint(self) -> str:
        return certificate_fingerprint(self.der)


# --- SSH host keys ---------------------------------------------------------


def host_id(host: str, port: int) -> str:
    """The known_hosts name for host:port, as OpenSSH writes it."""
    return host if port == 22 else f"[{host}]:{port}"


def fingerprint(key: paramiko.PKey) -> str:
    """OpenSSH's SHA256:… fingerprint."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def key_type_label(key: paramiko.PKey) -> str:
    name = key.get_name()
    return {
        "ssh-ed25519": "ED25519",
        "ssh-rsa": "RSA",
        "ssh-dss": "DSA",
    }.get(name, "ECDSA" if name.startswith("ecdsa-") else name)


class _KnownHostsFile:
    def __init__(self, path: Path):
        self.path = path
        self.keys = paramiko.HostKeys()
        self.revoked = []  # HostKeyEntry
        self._load()

    def _load(self):
        try:
            text = self.path.read_text(errors="replace")
        except OSError:
            return
        for lineno, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            marker = None
            if line.startswith("@"):
                marker, _sep, line = line.partition(" ")
                line = line.lstrip()
            try:
                entry = HostKeyEntry.from_line(line, lineno)
            except (InvalidHostKey, ValueError, TypeError) as e:
                log.info("Skipping %s:%d: %s", self.path, lineno, e)
                continue
            if entry is None:
                continue  # unsupported key type
            if marker == "@revoked":
                self.revoked.append(entry)
            elif marker is None:
                for name in entry.hostnames:
                    self.keys.add(name, entry.key.get_name(), entry.key)
            # @cert-authority: host certificates aren't supported; skip.

    def is_revoked(self, key) -> bool:
        # A revoked key is refused for every host, whatever names the line
        # lists (OpenSSH matches @revoked lines on the key alone).
        return any(entry.key == key for entry in self.revoked)

    def lookup(self, name):
        return self.keys.lookup(name) or {}


def _known_hosts_files():
    return [_KnownHostsFile(USER_KNOWN_HOSTS), _KnownHostsFile(KNOWN_HOSTS)]


def known_key_types(host: str, port: int) -> list[str]:
    """Key types already known for host:port, to prefer in the handshake.

    Without this a server offering ED25519 first gets asked about even when
    its RSA key is known, the way OpenSSH avoids it.
    """
    name = host_id(host, port)
    with _lock:
        files = _known_hosts_files()
    types = []
    for f in files:
        for key_type in f.lookup(name):
            if key_type not in types:
                types.append(key_type)
    return types


def check_host_key(host: str, port: int, key: paramiko.PKey):
    """Raise unless the server's key is a known, unrevoked key for host:port."""
    name = host_id(host, port)
    with _lock:
        files = _known_hosts_files()

    for f in files:
        if f.is_revoked(key):
            raise HostKeyRevoked(host, port, key, f.path)

    other_types = []
    for f in files:
        known = f.lookup(name)
        if key.get_name() in known:
            if known[key.get_name()] == key:
                return
            raise HostKeyChanged(host, port, key, f.path)
        other_types.extend(t for t in known if t not in other_types)

    raise HostKeyUnknown(host, port, key, other_types)


def trust_host_key(host: str, port: int, key: paramiko.PKey):
    """Add host:port's key to Edith's known_hosts (never ~/.ssh's)."""
    line = f"{host_id(host, port)} {key.get_name()} {key.get_base64()}\n"
    with _lock:
        KNOWN_HOSTS.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = KNOWN_HOSTS.read_bytes()
        except FileNotFoundError:
            existing = b""
        if existing and not existing.endswith(b"\n"):
            existing += b"\n"
        atomic_write_bytes(KNOWN_HOSTS, existing + line.encode("ascii"))


# --- TLS certificates ------------------------------------------------------


def certificate_fingerprint(der: bytes) -> str:
    """SHA-256 fingerprint as colon-separated hex, as browsers show it."""
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def _cert_key(host: str, port: int) -> str:
    return f"{host.lower()}:{port}"


def _load_certs() -> dict:
    try:
        data = json.loads(TRUSTED_CERTS.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_trusted_certificate(host: str, port: int, der: bytes) -> bool:
    with _lock:
        trusted = _load_certs().get(_cert_key(host, port))
    return trusted == hashlib.sha256(der).hexdigest()


def trust_certificate(host: str, port: int, der: bytes):
    """Trust this certificate for host:port, replacing any trusted before.

    One per host: a renewed certificate is asked about again rather than
    piling up every one the server has ever shown.
    """
    with _lock:
        data = _load_certs()
        data[_cert_key(host, port)] = hashlib.sha256(der).hexdigest()
        TRUSTED_CERTS.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(
            TRUSTED_CERTS, (json.dumps(data, indent=2) + "\n").encode("utf-8")
        )
        try:
            os.chmod(TRUSTED_CERTS, 0o600)
        except OSError:
            pass


def describe_certificate(der: bytes) -> dict:
    """Subject, issuer and validity for the trust dialog; empty on failure."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID

        cert = x509.load_der_x509_certificate(der)

        def name(n):
            cn = n.get_attributes_for_oid(NameOID.COMMON_NAME)
            return cn[0].value if cn else n.rfc4514_string()

        try:
            sans = cert.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value.get_values_for_type(x509.DNSName)
        except x509.ExtensionNotFound:
            sans = []
        return {
            "subject": name(cert.subject),
            "issuer": name(cert.issuer),
            "names": sans,
            "not_before": cert.not_valid_before_utc,
            "not_after": cert.not_valid_after_utc,
        }
    except Exception as e:
        log.info("Can't describe certificate: %s", e)
        return {}
