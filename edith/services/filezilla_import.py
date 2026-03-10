import defusedxml.ElementTree as ET
from pathlib import Path
from typing import List, Tuple

from edith.models.server import ServerInfo, FolderInfo


# FileZilla protocol codes → Edith protocol strings
_PROTOCOL_MAP = {
    "0": ("ftp", "none"),
    "1": ("sftp", "none"),
    "3": ("ftp", "explicit_required"),
    "4": ("ftp", "explicit_optional"),
    "6": ("ftp", "implicit"),
}


def _parse_server(elem: ET.Element) -> ServerInfo:
    protocol, ftp_encryption = _PROTOCOL_MAP.get(
        (elem.findtext("Protocol") or "0"), ("ftp", "none")
    )
    return ServerInfo(
        name=elem.findtext("Name") or "",
        host=elem.findtext("Host") or "",
        port=int(elem.findtext("Port") or (22 if protocol == "sftp" else 21)),
        username=elem.findtext("User") or "",
        protocol=protocol,
        ftp_encryption=ftp_encryption,
        auth_method="password",
        initial_directory=_decode_remote_dir(elem.findtext("RemoteDir") or ""),
    )


def _decode_remote_dir(raw: str) -> str:
    """Decode FileZilla's RemoteDir format (e.g. '1 0 4 home 3 foo') to a path."""
    if not raw or not raw.strip():
        return "/"
    parts = raw.strip().split(" ")
    # Format: type count [len name]...  — type is first token, count is second
    try:
        count = int(parts[1])
    except (IndexError, ValueError):
        return "/"
    if count == 0:
        return "/"
    segments = []
    i = 2
    for _ in range(count):
        if i >= len(parts):
            break
        int(parts[i])  # skip length field
        i += 1
        name = parts[i] if i < len(parts) else ""
        segments.append(name)
        i += 1
    return "/" + "/".join(segments) if segments else "/"


def _folder_name(elem: ET.Element) -> str:
    """Extract folder name from a FileZilla <Folder> element.

    The folder name is the text content before the first child element.
    """
    return (elem.text or "").strip()


def _parse_container(container: ET.Element, folder_id: str = "") -> Tuple[List[ServerInfo], List[FolderInfo]]:
    """Recursively parse <Servers> or <Folder> elements."""
    servers = []
    folders = []
    for child in container:
        if child.tag == "Server":
            server = _parse_server(child)
            server.folder_id = folder_id
            servers.append(server)
        elif child.tag == "Folder":
            folder = FolderInfo(name=_folder_name(child))
            folders.append(folder)
            sub_servers, sub_folders = _parse_container(child, folder.id)
            servers.extend(sub_servers)
            folders.extend(sub_folders)
    return servers, folders


def parse_sitemanager(path: Path) -> Tuple[List[ServerInfo], List[FolderInfo]]:
    """Parse a FileZilla sitemanager.xml and return (servers, folders)."""
    tree = ET.parse(path)
    root = tree.getroot()
    servers_elem = root.find("Servers")
    if servers_elem is None:
        return [], []
    return _parse_container(servers_elem)
