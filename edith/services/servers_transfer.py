# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Export and import the server list as a portable file.

Copying a file into the config directory used to be the way to move servers
between machines or restore a backup. Under Flatpak that route is gone: the
config lives in ~/.var/app/de.singular.edith/config/edith/, the automatic
backups go with it, and the sandbox cannot read arbitrary host paths. A file
chooser is the sandbox-native answer — the portal grants access to exactly the
file the user picked — so import/export is not a convenience here, it is the
only way data crosses the boundary.

Passwords are deliberately not part of an export. They live in the system
keyring under (SERVICE_NAME, server.id), and *ids are preserved on import*, so
re-importing on the same machine silently reconnects each server to the
credential it already had. Writing secrets into a plain file would buy nothing
there and would be actively unsafe for the case where the file does travel.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from edith.i18n import _
from edith.models.server import ServerInfo, FolderInfo
from edith.services.config import ConfigService

EXPORT_VERSION = 1


def default_export_name() -> str:
    return f"edith-servers-{time.strftime('%Y%m%d')}.json"


def export_servers(path: Path, servers: List[ServerInfo], folders: List[FolderInfo]):
    """Write servers and folders to [path]. Never includes credentials."""
    data = {
        "edith_export": EXPORT_VERSION,
        "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
        "servers": [s.to_dict() for s in servers],
        "folders": [f.to_dict() for f in folders],
    }
    path.write_text(json.dumps(data, indent=2))


def detect_format(path: Path) -> str:
    """Return "edith" or "filezilla" by sniffing content, not extension.

    The file may well be a raw servers.json lifted out of the backups
    directory, which carries no export marker, so anything JSON-shaped with a
    server list counts as ours.
    """
    try:
        head = path.read_text(errors="replace").lstrip()[:1]
    except OSError as e:
        raise ValueError(str(e))
    if head == "<":
        return "filezilla"
    if head == "{":
        return "edith"
    raise ValueError(_("Unrecognised file format — expected an Edith export or a FileZilla sitemanager.xml."))


def parse_export(path: Path) -> Tuple[List[ServerInfo], List[FolderInfo]]:
    """Read an Edith export, or a plain servers.json from the backups folder."""
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(_("This file is not valid JSON: {error}").format(error=e))

    if not isinstance(data, dict) or "servers" not in data:
        raise ValueError(_("This file does not contain an Edith server list."))

    raw_servers = data.get("servers") or []
    raw_folders = data.get("folders") or []
    if not isinstance(raw_servers, list) or not isinstance(raw_folders, list):
        raise ValueError(_("This file's server list is malformed."))

    # from_dict drops unknown keys, so a file written by a newer version still
    # imports — it just loses whatever this version has no field for.
    servers = [ServerInfo.from_dict(s) for s in raw_servers if isinstance(s, dict)]
    folders = [FolderInfo.from_dict(f) for f in raw_folders if isinstance(f, dict)]
    return servers, folders


@dataclass
class ImportResult:
    added: int = 0
    updated: int = 0
    folders_added: int = 0


def apply_import(
    servers: List[ServerInfo],
    folders: List[FolderInfo],
    replace: bool = False,
) -> ImportResult:
    """Merge [servers]/[folders] into the config, or replace it outright.

    Merging matches on id, so re-importing the same export updates entries in
    place instead of duplicating them — and, critically, keeps each server's id
    intact so its keyring credential still resolves. Minting fresh ids to dodge
    collisions would quietly sever every stored password.

    FileZilla imports mint new ids on every parse, so repeated imports from
    that source genuinely do add duplicates. That is inherent to the format
    having no stable identity to match on.
    """
    result = ImportResult()

    if replace:
        result.added = len(servers)
        result.folders_added = len(folders)
        final_servers, final_folders = list(servers), list(folders)
    else:
        final_folders = ConfigService.load_folders()
        known_folders = {f.id for f in final_folders}
        for folder in folders:
            if folder.id not in known_folders:
                final_folders.append(folder)
                known_folders.add(folder.id)
                result.folders_added += 1

        final_servers = ConfigService.load_servers()
        by_id = {s.id: i for i, s in enumerate(final_servers)}
        for server in servers:
            if server.id in by_id:
                final_servers[by_id[server.id]] = server
                result.updated += 1
            else:
                final_servers.append(server)
                result.added += 1

    # A server pointing at a group that came from neither the file nor the
    # existing config would vanish from the sidebar, so park it in Ungrouped
    # rather than let it become invisible.
    valid = {f.id for f in final_folders}
    for server in final_servers:
        if server.folder_id and server.folder_id not in valid:
            server.folder_id = ""

    ConfigService.save_all(final_servers, final_folders)
    return result
