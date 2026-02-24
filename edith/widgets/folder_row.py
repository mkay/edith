import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk

from edith.models.server import FolderInfo


class FolderRow(Gtk.Box):
    """A folder/group nav row in the server sidebar."""

    def __init__(self, folder_info: FolderInfo):
        super().__init__(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_start=8,
            margin_end=8,
            margin_top=8,
            margin_bottom=8,
        )
        self.folder_info = folder_info

        # Folder icon
        self._folder_icon = Gtk.Image(icon_name="edith-folder-symbolic")
        self.append(self._folder_icon)

        # Name label
        self._name_label = Gtk.Label(
            label=folder_info.name,
            xalign=0,
            hexpand=True,
            css_classes=["heading"],
        )
        self.append(self._name_label)

        # Server count label
        self._count_label = Gtk.Label(
            label="0",
            css_classes=["dim-label", "caption"],
            valign=Gtk.Align.CENTER,
        )
        self.append(self._count_label)

    def set_count(self, n: int):
        self._count_label.set_label(str(n))

    def update_name(self, name: str):
        self.folder_info.name = name
        self._name_label.set_label(name)
