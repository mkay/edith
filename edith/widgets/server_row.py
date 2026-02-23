import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk, GObject

from edith.models.server import ServerInfo


class ServerRow(Gtk.Box):
    """A single row in the server list."""

    __gsignals__ = {
        "connect-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "edit-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "delete-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, server_info: ServerInfo):
        super().__init__(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_start=8,
            margin_end=8,
            margin_top=4,
            margin_bottom=4,
        )
        self.server_info = server_info

        icon = Gtk.Image(icon_name="edith-server-symbolic", valign=Gtk.Align.START, margin_start=1, margin_top=5)
        self.append(icon)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, spacing=2)
        name_label = Gtk.Label(
            label=server_info.display_name,
            xalign=0,
            css_classes=["heading"],
        )
        detail_label = Gtk.Label(
            label=f"{server_info.username}@{server_info.host}:{server_info.port}",
            xalign=0,
            css_classes=["dim-label", "caption"],
        )
        labels.append(name_label)
        labels.append(detail_label)
        self.append(labels)

        self._name_label = name_label
        self._detail_label = detail_label

    def update_from(self, server_info: ServerInfo):
        self.server_info = server_info
        self._name_label.set_label(server_info.display_name)
        self._detail_label.set_label(
            f"{server_info.username}@{server_info.host}:{server_info.port}"
        )
