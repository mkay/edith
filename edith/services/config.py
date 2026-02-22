import json
import os
from pathlib import Path
from typing import List

from edith.models.server import ServerInfo, FolderInfo

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "edith"
SERVERS_FILE = CONFIG_DIR / "servers.json"


class ConfigService:
    """Load and save server/folder configurations to ~/.config/edith/servers.json."""

    # --- Internal helpers ---

    @staticmethod
    def _load_raw() -> dict:
        if not SERVERS_FILE.exists():
            return {}
        try:
            return json.loads(SERVERS_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            return {}

    @staticmethod
    def _save(servers: List[ServerInfo], folders: List[FolderInfo]):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        existing = ConfigService._load_raw()
        data = {
            "servers": [s.to_dict() for s in servers],
            "folders": [f.to_dict() for f in folders],
        }
        if "preferences" in existing:
            data["preferences"] = existing["preferences"]
        SERVERS_FILE.write_text(json.dumps(data, indent=2))

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
    def move_server_to_folder(server_id: str, folder_id: str):
        servers = ConfigService.load_servers()
        for s in servers:
            if s.id == server_id:
                s.folder_id = folder_id
                break
        ConfigService.save_servers(servers)

    # --- Preferences ---

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
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SERVERS_FILE.write_text(json.dumps(data, indent=2))
