# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Secure credential storage using keyring/libsecret."""

import logging
import threading

log = logging.getLogger(__name__)

SERVICE_NAME = "edith-sftp"

# Bulk export/import hit the keyring from a worker thread while the main thread
# may still be looking up a password to connect with. The active backend is
# keyring's chainer, which falls through to libsecret — a GObject library —
# whenever SecretService returns nothing, so two threads can end up inside
# unrelated D-Bus and GI stacks at once. Serialising every call is cheap next
# to the ~100ms each one already costs, and removes the interleaving entirely.
_lock = threading.RLock()


def _report(action: str, exc: Exception):
    """Log a keyring failure somewhere that survives the desktop launcher.

    Nothing started from a .desktop file has a stderr anyone will ever read, so
    a warning alone means a failed credential operation is invisible.
    """
    log.warning("Failed to %s password: %s", action, exc)
    try:
        from edith.services.freeze_watchdog import record
        record(f"keyring {action} failed: {type(exc).__name__}: {exc}")
    except Exception:
        pass


def _get_keyring():
    try:
        import keyring
        return keyring
    except ImportError:
        log.warning("keyring not available, credentials will not be stored")
        return None


def store_password(server_id: str, password: str):
    kr = _get_keyring()
    if kr:
        try:
            with _lock:
                kr.set_password(SERVICE_NAME, server_id, password)
        except Exception as e:
            _report("store", e)


def get_password(server_id: str) -> str | None:
    kr = _get_keyring()
    if kr:
        try:
            with _lock:
                return kr.get_password(SERVICE_NAME, server_id)
        except Exception as e:
            _report("retrieve", e)
    return None


def delete_password(server_id: str):
    kr = _get_keyring()
    if kr:
        try:
            with _lock:
                kr.delete_password(SERVICE_NAME, server_id)
        except Exception as e:
            _report("delete", e)
