# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
from pathlib import Path
from typing import List

from edith.models.server import ServerInfo, FolderInfo

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "edith"
SERVERS_FILE = CONFIG_DIR / "servers.json"


class ConfigService:
    """Load and save server/folder configurations to ~/.config/edith/servers.json."""

    _servers_file_override = None  # set via set_servers_file()

    @classmethod
    def set_servers_file(cls, path: str):
        """Use a custom servers file instead of the default."""
        cls._servers_file_override = Path(path)

    @classmethod
    def _servers_file(cls) -> Path:
        return cls._servers_file_override or SERVERS_FILE

    # --- Internal helpers ---

    @classmethod
    def _load_raw(cls) -> dict:
        path = cls._servers_file()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, KeyError):
            return {}

    @classmethod
    def _write_raw(cls, data: dict, deliberate: bool = False):
        """Write the whole config back, refusing to silently drop every server.

        Everything the app knows lives in this one file, so a writer that meant
        to touch a single preference can take the server list with it if the
        read half of its read-modify-write went wrong. That happened once — the
        readers followed the servers-file override while the writers went to the
        real path, so a preference write landed an empty dict on top of 218
        servers. The class of bug is silent and total, which is exactly what a
        cheap assertion is for.

        [deliberate] is for the writers whose actual job is the server list;
        only those may take it to zero, which is what deleting your last server
        legitimately does.
        """
        path = cls._servers_file()

        if not deliberate:
            current = cls._load_raw()
            if current.get("servers") and not data.get("servers"):
                # Skip the write rather than raise: the caller is saving a
                # preference or a recent path, and losing that is trivial next
                # to losing the servers. Say so loudly in the diagnostic log,
                # since nothing else about this failure is visible.
                from edith.services.freeze_watchdog import record
                record(
                    f"REFUSED config write that would drop "
                    f"{len(current['servers'])} servers from {path} "
                    f"(payload keys: {sorted(data)})"
                )
                return

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def _save(cls, servers: List[ServerInfo], folders: List[FolderInfo]):
        data = cls._load_raw()
        data["servers"] = [s.to_dict() for s in servers]
        data["folders"] = [f.to_dict() for f in folders]
        cls._write_raw(data, deliberate=True)

    # --- Backups ---

    BACKUP_KEEP = 10

    @classmethod
    def backup_now(cls):
        """Snapshot the config beside itself, keeping the last BACKUP_KEEP.

        Called once at startup. The whole config is one small file, so a plain
        copy is cheaper than reasoning about what is worth saving. Never raises:
        a failed backup must not stop the app from starting.
        """
        path = cls._servers_file()
        try:
            if not path.exists():
                return
            content = path.read_bytes()
            # An empty or serverless config is not worth preserving, and would
            # push a good backup out of the rotation.
            if not cls._load_raw().get("servers"):
                return

            backup_dir = path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            existing = sorted(backup_dir.glob("servers-*.json"))

            # Unchanged since the last run is the common case; rewriting it
            # would rotate a week of history out over a week of idle launches.
            if existing and existing[-1].read_bytes() == content:
                return

            import time
            stamp = time.strftime("%Y%m%d-%H%M%S")
            (backup_dir / f"servers-{stamp}.json").write_bytes(content)

            for stale in sorted(backup_dir.glob("servers-*.json"))[:-cls.BACKUP_KEEP]:
                stale.unlink()
        except OSError:
            pass

    # --- Server operations ---

    @staticmethod
    def load_servers() -> List[ServerInfo]:
        data = ConfigService._load_raw()
        return [ServerInfo.from_dict(s) for s in data.get("servers", [])]

    @staticmethod
    def save_servers(servers: List[ServerInfo]):
        folders = ConfigService.load_folders()
        ConfigService._save(servers, folders)

    @staticmethod
    def add_server(server: ServerInfo) -> List[ServerInfo]:
        servers = ConfigService.load_servers()
        servers.append(server)
        ConfigService.save_servers(servers)
        return servers

    @staticmethod
    def update_server(server: ServerInfo) -> List[ServerInfo]:
        servers = ConfigService.load_servers()
        for i, s in enumerate(servers):
            if s.id == server.id:
                servers[i] = server
                break
        ConfigService.save_servers(servers)
        return servers

    @staticmethod
    def delete_server(server_id: str) -> List[ServerInfo]:
        servers = ConfigService.load_servers()
        servers = [s for s in servers if s.id != server_id]
        ConfigService.save_servers(servers)
        return servers

    # --- Folder operations ---

    @staticmethod
    def load_folders() -> List[FolderInfo]:
        data = ConfigService._load_raw()
        return [FolderInfo.from_dict(f) for f in data.get("folders", [])]

    @staticmethod
    def save_folders(folders: List[FolderInfo]):
        servers = ConfigService.load_servers()
        ConfigService._save(servers, folders)

    @staticmethod
    def save_all(servers: List[ServerInfo], folders: List[FolderInfo]):
        ConfigService._save(servers, folders)

    @staticmethod
    def add_folder(folder: FolderInfo) -> List[FolderInfo]:
        folders = ConfigService.load_folders()
        folders.append(folder)
        ConfigService.save_folders(folders)
        return folders

    @staticmethod
    def update_folder(folder: FolderInfo) -> List[FolderInfo]:
        folders = ConfigService.load_folders()
        for i, f in enumerate(folders):
            if f.id == folder.id:
                folders[i] = folder
                break
        ConfigService.save_folders(folders)
        return folders

    @staticmethod
    def delete_folder(folder_id: str) -> List[FolderInfo]:
        folders = ConfigService.load_folders()
        folders = [f for f in folders if f.id != folder_id]
        ConfigService.save_folders(folders)
        return folders

    @staticmethod
    def reorder_folders(ordered_ids: list):
        """Reorder the folders list to match the given ID order and save."""
        folders = ConfigService.load_folders()
        id_to_folder = {f.id: f for f in folders}
        reordered = [id_to_folder[fid] for fid in ordered_ids if fid in id_to_folder]
        # Append any folders not in the list (shouldn't happen, but be safe)
        seen = set(ordered_ids)
        for f in folders:
            if f.id not in seen:
                reordered.append(f)
        ConfigService.save_folders(reordered)
        return reordered

    @staticmethod
    def move_server_to_folder(server_id: str, folder_id: str):
        servers = ConfigService.load_servers()
        for s in servers:
            if s.id == server_id:
                s.folder_id = folder_id
                break
        ConfigService.save_servers(servers)

    # --- Recents ---

    RECENTS_MAX = 5

    @staticmethod
    def get_recents(server_id: str) -> list:
        data = ConfigService._load_raw()
        return data.get("recents", {}).get(server_id, [])

    @staticmethod
    def push_recent(server_id: str, path: str):
        data = ConfigService._load_raw()
        recents = data.get("recents", {})
        paths = [p for p in recents.get(server_id, []) if p != path]
        paths.insert(0, path)
        recents[server_id] = paths[:ConfigService.RECENTS_MAX]
        data["recents"] = recents
        ConfigService._write_raw(data)

    @staticmethod
    def delete_recent(server_id: str, path: str):
        data = ConfigService._load_raw()
        recents = data.get("recents", {})
        recents[server_id] = [p for p in recents.get(server_id, []) if p != path]
        data["recents"] = recents
        ConfigService._write_raw(data)

    # --- Pins ---

    @staticmethod
    def get_pins(server_id: str) -> list:
        data = ConfigService._load_raw()
        return data.get("pins", {}).get(server_id, [])

    @staticmethod
    def add_pin(server_id: str, path: str, is_dir: bool):
        data = ConfigService._load_raw()
        pins = data.get("pins", {})
        entries = pins.get(server_id, [])
        if not any(e["path"] == path for e in entries):
            entries.append({"path": path, "is_dir": is_dir})
        pins[server_id] = entries
        data["pins"] = pins
        ConfigService._write_raw(data)

    @staticmethod
    def delete_pin(server_id: str, path: str):
        data = ConfigService._load_raw()
        pins = data.get("pins", {})
        pins[server_id] = [e for e in pins.get(server_id, []) if e["path"] != path]
        data["pins"] = pins
        ConfigService._write_raw(data)

    # --- Pinned servers ---

    @staticmethod
    def get_pinned_servers() -> list:
        data = ConfigService._load_raw()
        return data.get("pinned_servers", [])

    @staticmethod
    def is_server_pinned(server_id: str) -> bool:
        return server_id in ConfigService.get_pinned_servers()

    @staticmethod
    def toggle_server_pin(server_id: str):
        data = ConfigService._load_raw()
        pins = data.get("pinned_servers", [])
        if server_id in pins:
            pins.remove(server_id)
        else:
            pins.append(server_id)
        data["pinned_servers"] = pins
        ConfigService._write_raw(data)

    # --- Preferences ---

    @staticmethod
    def has_config() -> bool:
        """True when a config file already exists, i.e. this is not a first run.

        The closest thing Linux offers to Android's firstInstallTime check, and
        packaging-agnostic: pacman, Flatpak and a source checkout all leave the
        config alone. Call it before anything writes config this session.
        """
        return ConfigService._servers_file().exists()

    @staticmethod
    def get_preference(key: str, default=None):
        data = ConfigService._load_raw()
        return data.get("preferences", {}).get(key, default)

    @staticmethod
    def set_preference(key: str, value):
        data = ConfigService._load_raw()
        prefs = data.get("preferences", {})
        prefs[key] = value
        data["preferences"] = prefs
        ConfigService._write_raw(data)
