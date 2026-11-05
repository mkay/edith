# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Local file writes that never leave half a file behind, and text decoding
that round-trips the bytes it was given."""

import contextlib
import os
import secrets

# --- Atomic writes ---------------------------------------------------------


@contextlib.contextmanager
def atomic_write(path, buffering=-1):
    """Open a binary file that replaces `path` only once fully written.

    Data goes to a hidden sibling first and is renamed over `path` when the
    block exits cleanly. Any exception — a dropped connection, a cancelled
    transfer, a full disk — deletes the sibling and leaves `path` as it was,
    so a partial download can never be mistaken for a complete one, and a
    failed re-download can't truncate a file that was fine before.

    The sibling is created with mode 0666 and the process umask applies, the
    same as a plain open(path, "wb") on a new file.
    """
    path = os.fspath(path)
    directory, name = os.path.split(path)
    tmp = os.path.join(directory, f".{name}.{secrets.token_hex(4)}.part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "wb", buffering=buffering) as f:
            yield f
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def atomic_write_bytes(path, data: bytes):
    with atomic_write(path) as f:
        f.write(data)


# --- Text encoding ---------------------------------------------------------

UTF8 = "utf-8"
UTF8_BOM = "utf-8-sig"
LATIN1 = "latin-1"

_BOM = b"\xef\xbb\xbf"


def decode_text(data: bytes) -> tuple[str, str]:
    """Decode file bytes, returning (text, encoding) for encode_text().

    UTF-8 (with or without BOM) when the bytes are valid UTF-8, Latin-1
    otherwise. Latin-1 maps every byte to a character, so it never fails and
    encodes back to exactly the bytes it came from — a Windows-1252 file shows
    its few extra characters (€, curly quotes) wrongly, but saving it changes
    nothing that wasn't edited.

    Line endings are left alone: CRLF stays CRLF.
    """
    if data.startswith(_BOM):
        try:
            return data[len(_BOM):].decode(UTF8), UTF8_BOM
        except UnicodeDecodeError:
            pass
    try:
        return data.decode(UTF8), UTF8
    except UnicodeDecodeError:
        return data.decode(LATIN1), LATIN1


def encode_text(text: str, encoding: str) -> bytes:
    """Encode text in the encoding decode_text() reported.

    Raises UnencodableError when the text has characters the encoding can't
    hold (a € typed into a Latin-1 file), rather than writing something else.
    """
    try:
        return text.encode(encoding)  # utf-8-sig writes the BOM back
    except UnicodeEncodeError as e:
        raise UnencodableError(encoding, text[e.start:e.end]) from e


class UnencodableError(ValueError):
    def __init__(self, encoding: str, chars: str):
        self.encoding = encoding
        self.chars = chars
        super().__init__(f"{chars!r} can't be written as {encoding}")


def read_text(path) -> tuple[str, str]:
    with open(path, "rb") as f:
        return decode_text(f.read())
