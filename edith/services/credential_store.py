"""Secure credential storage using keyring/libsecret."""

import logging

log = logging.getLogger(__name__)

SERVICE_NAME = "edith-sftp"


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
            kr.set_password(SERVICE_NAME, server_id, password)
        except Exception as e:
            log.warning("Failed to store password: %s", e)


def get_password(server_id: str) -> str | None:
    kr = _get_keyring()
    if kr:
        try:
            return kr.get_password(SERVICE_NAME, server_id)
        except Exception as e:
            log.warning("Failed to retrieve password: %s", e)
    return None


def delete_password(server_id: str):
    kr = _get_keyring()
    if kr:
        try:
            kr.delete_password(SERVICE_NAME, server_id)
        except Exception as e:
            log.warning("Failed to delete password: %s", e)
