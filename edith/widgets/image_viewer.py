import os
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gtk, GdkPixbuf

from edith.models.open_file import OpenFile


IMAGE_EXTENSIONS = frozenset([
    "png", "jpg", "jpeg", "gif", "webp", "bmp",
    "ico", "tiff", "tif", "avif",
])


def is_image_file(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in IMAGE_EXTENSIONS


def _format_size(n_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes} {unit}" if unit == "B" else f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"


class ImageViewer(Gtk.Box):
    """Read-only image viewer for a single editor tab."""

    def __init__(self, open_file: OpenFile):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.open_file = open_file
        self.open_file.is_modified = False

        self._picture = Gtk.Picture.new_for_filename(open_file.local_path)
        self._picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self._picture.set_can_shrink(True)
        self._picture.set_vexpand(True)
        self._picture.set_hexpand(True)
        self.append(self._picture)

        self.append(Gtk.Separator())
        self._info_bar = self._build_info_bar(open_file.local_path)
        self.append(self._info_bar)

    def reload_from_disk(self):
        """Re-read the local file after it changed on disk."""
        local_path = self.open_file.local_path
        # Clearing first forces a reload: GtkPicture ignores set_filename()
        # when the file it already holds compares equal.
        self._picture.set_filename(None)
        self._picture.set_filename(local_path)

        new_bar = self._build_info_bar(local_path)
        self.insert_child_after(new_bar, self._info_bar)
        self.remove(self._info_bar)
        self._info_bar = new_bar

    def _build_info_bar(self, local_path: str) -> Gtk.Box:
        bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=0,
            margin_start=12,
            margin_end=12,
            margin_top=4,
            margin_bottom=4,
        )

        parts = self._collect_info(local_path)
        for i, text in enumerate(parts):
            if i > 0:
                sep = Gtk.Label(label=" · ", css_classes=["dim-label", "caption"])
                bar.append(sep)
            lbl = Gtk.Label(label=text, css_classes=["dim-label", "caption"])
            bar.append(lbl)

        return bar

    def _collect_info(self, local_path: str) -> list[str]:
        parts = []

        # File size
        try:
            parts.append(_format_size(os.path.getsize(local_path)))
        except OSError:
            pass

        # Dimensions and DPI via GdkPixbuf
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(local_path)
            w = pixbuf.get_width()
            h = pixbuf.get_height()
            parts.append(f"{w} × {h} px")

            x_dpi = pixbuf.get_option("x-dpi")
            y_dpi = pixbuf.get_option("y-dpi")
            if x_dpi and y_dpi:
                if x_dpi == y_dpi:
                    parts.append(f"{x_dpi} dpi")
                else:
                    parts.append(f"{x_dpi} × {y_dpi} dpi")
        except (ValueError, AttributeError):
            pass

        return parts
