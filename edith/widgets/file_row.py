import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk

from edith.models.remote_file import RemoteFileInfo


class FileRow(Gtk.Box):
    """A single row in the file browser."""

    def __init__(self, file_info: RemoteFileInfo, depth: int = 0):
        super().__init__(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            margin_start=8 + depth * 16,
            margin_end=8,
            margin_top=2,
            margin_bottom=2,
        )

        self.file_info = file_info
        self.set_tooltip_text(file_info.path)

        icon = Gtk.Image(icon_name=file_info.icon_name, pixel_size=20)
        self.append(icon)

        name_label = Gtk.Label(
            label=file_info.name,
            xalign=0,
            hexpand=True,
            ellipsize=3,  # PANGO_ELLIPSIZE_END
        )
        self.append(name_label)

        if not file_info.is_dir:
            size_label = Gtk.Label(
                label=file_info.human_size(),
                css_classes=["dim-label", "caption"],
            )
            self.append(size_label)
