import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, GObject

from edith.models.server import FolderInfo


class FolderRow(Gtk.Box):
    """A collapsible folder row in the server list."""

    __gsignals__ = {
        "toggled": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self, folder_info: FolderInfo):
        super().__init__(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_start=8,
            margin_end=8,
            margin_top=4,
            margin_bottom=4,
        )
        self.folder_info = folder_info

        # Expand/collapse toggle
        icon_name = "pan-down-symbolic" if folder_info.expanded else "pan-end-symbolic"
        self._toggle_btn = Gtk.Button(
            icon_name=icon_name,
            css_classes=["flat", "circular"],
            valign=Gtk.Align.CENTER,
        )
        self._toggle_btn.connect("clicked", self._on_toggle_clicked)
        self.append(self._toggle_btn)

        # Folder icon
        self._folder_icon = Gtk.Image(icon_name="folder-symbolic")
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

    def _on_toggle_clicked(self, btn):
        self.folder_info.expanded = not self.folder_info.expanded
        icon = "pan-down-symbolic" if self.folder_info.expanded else "pan-end-symbolic"
        self._toggle_btn.set_icon_name(icon)
        self.emit("toggled", self.folder_info.expanded)

    def set_count(self, n: int):
        self._count_label.set_label(str(n))

    def update_name(self, name: str):
        self.folder_info.name = name
        self._name_label.set_label(name)
