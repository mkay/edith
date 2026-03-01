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
            return "edith-folder-symbolic"

        # Bare filenames with no extension (Dockerfile, .env, etc.)
        bare_icons = {
            "dockerfile": "edith-file-dockerfile",
            ".env":       "edith-file-env",
        }
        if self.name.lower() in bare_icons:
            return bare_icons[self.name.lower()]

        ext = self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""

        ext_icons = {
            # Python
            "py": "edith-file-python", "pyw": "edith-file-python", "pyi": "edith-file-python",
            # JavaScript
            "js": "edith-file-javascript", "mjs": "edith-file-javascript",
            "cjs": "edith-file-javascript", "jsx": "edith-file-javascript",
            # TypeScript
            "ts": "edith-file-typescript", "tsx": "edith-file-typescript",
            "mts": "edith-file-typescript", "cts": "edith-file-typescript",
            # HTML
            "html": "edith-file-html", "htm": "edith-file-html", "xhtml": "edith-file-html",
            # CSS
            "css": "edith-file-css",
            # JSON
            "json": "edith-file-json", "jsonc": "edith-file-json",
            # YAML
            "yml": "edith-file-yaml", "yaml": "edith-file-yaml",
            # Markdown
            "md": "edith-file-markdown", "mkd": "edith-file-markdown",
            "markdown": "edith-file-markdown", "mdx": "edith-file-markdown",
            # Shell
            "sh": "edith-file-shell", "bash": "edith-file-shell",
            "zsh": "edith-file-shell", "fish": "edith-file-shell",
            # SQL
            "sql": "edith-file-sql",
            # XML
            "xml": "edith-file-xml", "xsl": "edith-file-xml",
            "xslt": "edith-file-xml", "plist": "edith-file-xml",
            # Plain text / logs
            "txt": "edith-file-text", "text": "edith-file-text", "log": "edith-file-text",
            # PHP
            "php": "edith-file-php", "phtml": "edith-file-php",
            # Ruby
            "rb": "edith-file-ruby", "erb": "edith-file-ruby",
            # Go
            "go": "edith-file-go",
            # Rust
            "rs": "edith-file-rust",
            # Java
            "java": "edith-file-java",
            # C
            "c": "edith-file-c", "h": "edith-file-c",
            # C++
            "cpp": "edith-file-cpp", "cc": "edith-file-cpp", "cxx": "edith-file-cpp",
            "hpp": "edith-file-cpp", "hh": "edith-file-cpp", "hxx": "edith-file-cpp",
            # SCSS
            "scss": "edith-file-scss",
            # Less
            "less": "edith-file-less",
            # Config / INI
            "ini": "edith-file-ini", "cfg": "edith-file-ini",
            "conf": "edith-file-ini", "properties": "edith-file-ini",
            # Terraform / HCL
            "tf": "edith-file-terraform", "tfvars": "edith-file-terraform",
            "hcl": "edith-file-terraform",
            # GraphQL
            "graphql": "edith-file-graphql", "gql": "edith-file-graphql",
            # TOML
            "toml": "edith-file-toml",
            # ENV
            "env": "edith-file-env",
            # Documents
            "doc": "edith-file-doc", "docx": "edith-file-doc",
            "xls": "edith-file-xls", "xlsx": "edith-file-xls",
            "pdf": "edith-file-pdf",
            # Images
            "png": "edith-file-image-png",
            "jpg": "edith-file-image-jpg", "jpeg": "edith-file-image-jpg",
            "gif": "edith-file-image-gif",
            "webp": "edith-file-image-webp",
            "bmp": "edith-file-image",
            "ico": "edith-file-image", "tiff": "edith-file-image",
            "tif": "edith-file-image", "avif": "edith-file-image",
            "svg": "edith-file-svg",
            # Archives
            "zip": "edith-file-archive", "tar": "edith-file-archive",
            "gz": "edith-file-archive",  "tgz": "edith-file-archive",
            "bz2": "edith-file-archive", "tbz": "edith-file-archive",
            "xz": "edith-file-archive",  "txz": "edith-file-archive",
            "7z": "edith-file-archive",  "rar": "edith-file-archive",
            "zst": "edith-file-archive",
        }
        if ext in ext_icons:
            return ext_icons[ext]

        # Dotfiles with no recognised extension (.htaccess, .gitignore, etc.)
        if self.name.startswith("."):
            return "edith-file-dotfile"

        return "edith-file-symbolic"

    def human_size(self) -> str:
        if self.is_dir:
            return ""
        for unit in ("B", "KB", "MB", "GB"):
            if self.size < 1024:
                return f"{self.size:.0f} {unit}" if unit == "B" else f"{self.size:.1f} {unit}"
            self.size /= 1024
        return f"{self.size:.1f} TB"
