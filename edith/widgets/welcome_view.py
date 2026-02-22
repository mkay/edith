import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk


class WelcomeView(Adw.Bin):
    """Empty state shown when no file is open."""

    def __init__(self):
        super().__init__()

        status = Adw.StatusPage(
            icon_name="network-server-symbolic",
            title="Welcome to Edith",
            description="Connect to a server and open a file to start editing",
            vexpand=True,
            hexpand=True,
        )

        self.set_child(status)
