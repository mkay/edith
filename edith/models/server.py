from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class ServerInfo:
    """Represents a saved SFTP server configuration."""

    name: str = ""
    host: str = ""
    port: int = 22
    username: str = ""
    key_file: str = ""
    auth_method: str = "password"  # "password", "key", "key+passphrase"
    initial_directory: str = "/"
    id: str = field(default_factory=lambda: "")
    folder_id: str = ""  # empty = ungrouped

    def __post_init__(self):
        if not self.id:
            import uuid
            self.id = str(uuid.uuid4())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ServerInfo":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    @property
    def display_name(self) -> str:
        return self.name or f"{self.username}@{self.host}"


@dataclass
class FolderInfo:
    """Represents a folder group in the server list."""

    name: str = "New Folder"
    id: str = field(default_factory=lambda: "")
    expanded: bool = True

    def __post_init__(self):
        if not self.id:
            import uuid
            self.id = str(uuid.uuid4())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "FolderInfo":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)
