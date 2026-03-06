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
        "unpin-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, server_info: ServerInfo, pinned: bool = False):
        super().__init__(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_start=12,
            margin_end=12,
            margin_top=10,
            margin_bottom=10,
        )
        self.server_info = server_info

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, spacing=2)

        # Top row: name + protocol badge
        name_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name_label = Gtk.Label(
            label=server_info.display_name,
            xalign=0,
            css_classes=["heading"],
        )
        name_box.append(name_label)

        tag, css_class = self._protocol_badge(server_info)
        badge = Gtk.Label(
            label=tag,
            valign=Gtk.Align.CENTER,
            css_classes=["protocol-badge", css_class, "caption"],
        )
        name_box.append(badge)
        labels.append(name_box)

        detail_label = Gtk.Label(
            label=f"{server_info.username}@{server_info.host}:{server_info.port}",
            xalign=0,
            css_classes=["dim-label", "caption"],
        )
        labels.append(detail_label)
        self.append(labels)

        if pinned:
            pin_btn = Gtk.Button(
                icon_name="edith-pin-symbolic",
                valign=Gtk.Align.CENTER,
                css_classes=["flat", "dim-label"],
                tooltip_text="Unpin",
                focusable=False,
            )
            pin_btn.connect("clicked", lambda _: self.emit("unpin-requested"))
            self.append(pin_btn)

        self._name_label = name_label
        self._detail_label = detail_label
        self._badge = badge

    @staticmethod
    def _protocol_badge(server_info: ServerInfo) -> tuple:
        """Return (label, css_class) for the protocol badge."""
        protocol = getattr(server_info, "protocol", "sftp")
        if protocol == "sftp":
            return ("SFTP", "badge-ssh")
        if protocol == "ftps":
            return ("FTPS", "badge-tls")
        enc = getattr(server_info, "ftp_encryption", "none")
        if enc == "implicit":
            return ("FTPS", "badge-tls")
        if enc in ("explicit_required", "explicit_optional"):
            return ("FTP/TLS", "badge-tls")
        return ("FTP", "badge-insecure")

    def update_from(self, server_info: ServerInfo):
        self.server_info = server_info
        self._name_label.set_label(server_info.display_name)
        self._detail_label.set_label(
            f"{server_info.username}@{server_info.host}:{server_info.port}"
        )
        tag, css_class = self._protocol_badge(server_info)
        self._badge.set_label(tag)
        # Swap badge class
        for cls in ("badge-ssh", "badge-tls", "badge-insecure"):
            self._badge.remove_css_class(cls)
        self._badge.add_css_class(css_class)
