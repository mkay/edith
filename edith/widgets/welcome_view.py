import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from edith import VERSION


class WelcomeView(Adw.Bin):
    """Empty state shown when no file is open."""

    def __init__(self):
        super().__init__()

        status = Adw.StatusPage(
            icon_name="de.singular.edith-symbolic",
            title="Welcome to Edith",
            description=f"Version {VERSION}\n\nEdith is alpha software.\nIt's guaranteed to surprise you — sometimes even in a good way.\n\nConnect to a server and open a file to start editing",
            vexpand=True,
            hexpand=True,
        )

        self.set_child(status)
