# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Read PuTTY private keys (.ppk), which paramiko can't.

PuTTY, and FileZilla with it, store keys in their own format, so a FileZilla
user's key is often only a .ppk. Versions 2 and 3 are supported, unencrypted
or encrypted with aes256-cbc, holding Ed25519, RSA or ECDSA keys (DSA is
gone from paramiko and OpenSSH alike). Version 3 derives its key with Argon2; only
Argon2id, PuTTYgen's default, is available here (the cryptography package
has no Argon2i or Argon2d).

The key is rebuilt with the cryptography package and handed to paramiko in
OpenSSH format, so every key type paramiko supports works the same way.
"""

import base64
import hashlib
import hmac
import io
import struct

import paramiko

MAGIC = "PuTTY-User-Key-File-"


def is_ppk(path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC.encode("ascii")
    except OSError:
        return False


def load(path, passphrase: str | None = None) -> paramiko.PKey:
    """Load a .ppk as a paramiko key.

    Raises paramiko.PasswordRequiredException for an encrypted key without
    a passphrase, and paramiko.SSHException for a wrong passphrase or a file
    this can't read, as paramiko does for its own formats.
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    try:
        return _load(text, passphrase)
    except (paramiko.PasswordRequiredException, paramiko.SSHException):
        raise
    except (ValueError, KeyError, IndexError, struct.error) as e:
        raise paramiko.SSHException(f"Not a readable PuTTY key file: {e}")


# --- File format -----------------------------------------------------------


def _parse(text: str):
    """Return (version, headers, public blob, private blob)."""
    lines = text.splitlines()
    first, _sep, algorithm = lines[0].partition(":")
    if not first.startswith(MAGIC):
        raise ValueError("missing PuTTY-User-Key-File header")
    version = int(first[len(MAGIC):])
    headers = {"Algorithm": algorithm.strip()}
    blobs = {}
    i = 1
    while i < len(lines):
        key, sep, value = lines[i].partition(": ")
        i += 1
        if not sep:
            continue
        if key in ("Public-Lines", "Private-Lines"):
            count = int(value)
            blobs[key] = "".join(lines[i:i + count])
            i += count
        else:
            headers[key] = value.strip()
    public = base64.b64decode(blobs["Public-Lines"])
    private = base64.b64decode(blobs["Private-Lines"])
    return version, headers, public, private


def _string(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def string(self) -> bytes:
        (n,) = struct.unpack(">I", self.data[self.pos:self.pos + 4])
        start = self.pos + 4
        if start + n > len(self.data):
            raise ValueError("truncated key data")
        self.pos = start + n
        return self.data[start:start + n]

    def mpint(self) -> int:
        return int.from_bytes(self.string(), "big", signed=True)


def _keys_v2(passphrase: bytes):
    """(cipher key, iv, mac key) for a version 2 file."""
    cipher_key = (
        hashlib.sha1(b"\0\0\0\0" + passphrase).digest()
        + hashlib.sha1(b"\0\0\0\1" + passphrase).digest()
    )[:32]
    mac_key = hashlib.sha1(b"putty-private-key-file-mac-key" + passphrase).digest()
    return cipher_key, b"\0" * 16, mac_key


def _keys_v3(headers: dict, passphrase: bytes):
    """(cipher key, iv, mac key) for an encrypted version 3 file."""
    kdf = headers.get("Key-Derivation", "")
    if kdf != "Argon2id":
        raise paramiko.SSHException(
            f"This PuTTY key uses {kdf or 'an unknown key derivation'}, which "
            "Edith can't read. Re-save it in PuTTYgen with the default "
            "(Argon2id), or convert it to OpenSSH format."
        )
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

    derived = Argon2id(
        salt=bytes.fromhex(headers["Argon2-Salt"]),
        length=80,
        iterations=int(headers["Argon2-Passes"]),
        lanes=int(headers["Argon2-Parallelism"]),
        memory_cost=int(headers["Argon2-Memory"]),
    ).derive(passphrase)
    return derived[:32], derived[32:48], derived[48:]


def _load(text: str, passphrase: str | None) -> paramiko.PKey:
    version, headers, public, private = _parse(text)
    if version not in (2, 3):
        raise paramiko.SSHException(f"PuTTY key file version {version} isn't supported")

    encryption = headers.get("Encryption", "none")
    if encryption not in ("none", "aes256-cbc"):
        raise paramiko.SSHException(f"Unsupported PuTTY key encryption: {encryption}")

    if encryption == "none":
        secret = b""
        if version == 2:
            _ck, _iv, mac_key = _keys_v2(secret)
        else:
            mac_key = b""
    else:
        if not passphrase:
            raise paramiko.PasswordRequiredException("Private key file is encrypted")
        secret = passphrase.encode("utf-8")
        if version == 2:
            cipher_key, iv, mac_key = _keys_v2(secret)
        else:
            cipher_key, iv, mac_key = _keys_v3(headers, secret)
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        decryptor = Cipher(algorithms.AES(cipher_key), modes.CBC(iv)).decryptor()
        private = decryptor.update(private) + decryptor.finalize()

    mac_data = (
        _string(headers["Algorithm"].encode())
        + _string(encryption.encode())
        + _string(headers.get("Comment", "").encode("utf-8"))
        + _string(public)
        + _string(private)
    )
    digest = hashlib.sha1 if version == 2 else hashlib.sha256
    expected = hmac.new(mac_key, mac_data, digest).hexdigest()
    if not hmac.compare_digest(expected, headers.get("Private-MAC", "").lower()):
        if encryption != "none":
            raise paramiko.SSHException("Wrong passphrase for the PuTTY key")
        raise paramiko.SSHException("The PuTTY key file is damaged")

    key = _to_paramiko(_build_key(headers["Algorithm"], public, private))
    if key.asbytes() != public:
        raise paramiko.SSHException("The PuTTY key file's private and public keys don't match")
    return key


# --- Key material ----------------------------------------------------------


def _build_key(algorithm: str, public: bytes, private: bytes):
    """A cryptography private key from PuTTY's public and private blobs."""
    pub = _Reader(public)
    priv = _Reader(private)
    if pub.string().decode() != algorithm:
        raise ValueError("public key doesn't match the file's algorithm")

    if algorithm == "ssh-ed25519":
        from cryptography.hazmat.primitives.asymmetric import ed25519

        return ed25519.Ed25519PrivateKey.from_private_bytes(priv.string())

    if algorithm == "ssh-rsa":
        from cryptography.hazmat.primitives.asymmetric import rsa

        e, n = pub.mpint(), pub.mpint()
        d, p, q, iqmp = priv.mpint(), priv.mpint(), priv.mpint(), priv.mpint()
        return rsa.RSAPrivateNumbers(
            p=p, q=q, d=d, dmp1=d % (p - 1), dmq1=d % (q - 1), iqmp=iqmp,
            public_numbers=rsa.RSAPublicNumbers(e, n),
        ).private_key()

    if algorithm.startswith("ecdsa-sha2-"):
        from cryptography.hazmat.primitives.asymmetric import ec

        curves = {
            "nistp256": ec.SECP256R1(), "nistp384": ec.SECP384R1(),
            "nistp521": ec.SECP521R1(),
        }
        curve = curves[pub.string().decode()]
        return ec.derive_private_key(priv.mpint(), curve)

    if algorithm == "ssh-dss":
        # paramiko 4 dropped DSA, as OpenSSH 9.8 did.
        raise paramiko.SSHException(
            "DSA keys are no longer supported by SSH. Create a new key "
            "(Ed25519 or RSA) in PuTTYgen."
        )

    raise paramiko.SSHException(f"Unsupported PuTTY key type: {algorithm}")


def _to_paramiko(key) -> paramiko.PKey:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode("ascii")
    if isinstance(key, ed25519.Ed25519PrivateKey):
        cls = paramiko.Ed25519Key
    elif isinstance(key, rsa.RSAPrivateKey):
        cls = paramiko.RSAKey
    elif isinstance(key, ec.EllipticCurvePrivateKey):
        cls = paramiko.ECDSAKey
    else:
        raise paramiko.SSHException("paramiko can't use this key type")
    return cls.from_private_key(io.StringIO(pem))
