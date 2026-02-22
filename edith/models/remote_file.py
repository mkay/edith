import stat
from dataclasses import dataclass


@dataclass
class RemoteFileInfo:
    """Represents a remote file or directory entry."""

    name: str
    path: str
    size: int = 0
    is_dir: bool = False
    permissions: int = 0

    @classmethod
    def from_sftp_attr(cls, attr, parent_path: str) -> "RemoteFileInfo":
        name = attr.filename
        if parent_path == "/":
            path = f"/{name}"
        else:
            path = f"{parent_path}/{name}"

        is_dir = stat.S_ISDIR(attr.st_mode) if attr.st_mode else False

        return cls(
            name=name,
            path=path,
            size=attr.st_size or 0,
            is_dir=is_dir,
            permissions=attr.st_mode or 0,
        )

    @property
    def icon_name(self) -> str:
        if self.is_dir:
            return "folder-symbolic"

        ext = self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""

        ext_icons = {
            "py": "text-x-python-symbolic",
            "js": "text-x-javascript-symbolic",
            "ts": "text-x-javascript-symbolic",
            "html": "text-html-symbolic",
            "css": "text-css-symbolic",
            "json": "text-x-generic-symbolic",
            "xml": "text-xml-symbolic",
            "md": "text-x-generic-symbolic",
            "sh": "text-x-script-symbolic",
            "c": "text-x-csrc-symbolic",
            "h": "text-x-chdr-symbolic",
            "rs": "text-x-generic-symbolic",
            "go": "text-x-generic-symbolic",
        }
        return ext_icons.get(ext, "text-x-generic-symbolic")

    def human_size(self) -> str:
        if self.is_dir:
            return ""
        for unit in ("B", "KB", "MB", "GB"):
            if self.size < 1024:
                return f"{self.size:.0f} {unit}" if unit == "B" else f"{self.size:.1f} {unit}"
            self.size /= 1024
        return f"{self.size:.1f} TB"
