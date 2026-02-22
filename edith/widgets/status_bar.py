import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk


class StatusBar(Gtk.Box):
    """Status bar showing connection state and transfer progress."""

    def __init__(self):
        super().__init__(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_start=8,
            margin_end=8,
            margin_top=4,
            margin_bottom=4,
        )
        self.add_css_class("toolbar")

        # Connection status icon + label
        self._status_icon = Gtk.Image(icon_name="network-offline-symbolic")
        self.append(self._status_icon)

        self._status_label = Gtk.Label(
            label="Disconnected",
            xalign=0,
            hexpand=True,
            ellipsize=3,
            css_classes=["dim-label", "caption"],
        )
        self.append(self._status_label)

        # Transfer spinner (hidden by default)
        self._spinner = Gtk.Spinner(spinning=False, visible=False)
        self.append(self._spinner)

        self._transfer_label = Gtk.Label(
            label="",
            visible=False,
            css_classes=["dim-label", "caption"],
        )
        self.append(self._transfer_label)

    def set_status(self, state: str, message: str):
        """Update the status display.

        state: "disconnected", "connecting", "connected", "downloading",
               "uploading", "error"
        """
        self._status_label.set_label(message)

        icon_map = {
            "disconnected": "network-offline-symbolic",
            "connecting": "network-transmit-symbolic",
            "connected": "network-idle-symbolic",
            "downloading": "network-receive-symbolic",
            "uploading": "network-transmit-symbolic",
            "error": "dialog-error-symbolic",
        }
        self._status_icon.set_from_icon_name(icon_map.get(state, "network-offline-symbolic"))

        is_transferring = state in ("downloading", "uploading", "connecting")
        self._spinner.set_visible(is_transferring)
        self._spinner.set_spinning(is_transferring)

    def set_transfer_progress(self, text: str):
        """Show transfer progress text."""
        if text:
            self._transfer_label.set_label(text)
            self._transfer_label.set_visible(True)
        else:
            self._transfer_label.set_visible(False)
