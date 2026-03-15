import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from edith import VERSION


class WelcomeView(Adw.Bin):
    """Empty state shown when no file is open."""

    def __init__(self, on_add_server=None):
        super().__init__()
        self._on_add_server = on_add_server

        self._status = Adw.StatusPage(
            icon_name="de.singular.edith-symbolic",
            title="Welcome to Edith",
            vexpand=True,
            hexpand=True,
        )

        child_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            halign=Gtk.Align.CENTER,
        )

        self._add_server_btn = Gtk.Button(
            label="Add Your First Server",
            halign=Gtk.Align.CENTER,
            css_classes=["pill", "suggested-action"],
        )
        self._add_server_btn.connect("clicked", self._on_add_btn_clicked)
        child_box.append(self._add_server_btn)

        self._hint_label = Gtk.Label(
            use_markup=True,
            halign=Gtk.Align.CENTER,
        )
        self._hint_label.set_markup(
            'Connect to a server from the sidebar or <a href="create">click here to create one</a>.'
        )
        self._hint_label.connect("activate-link", self._on_link_activated)
        child_box.append(self._hint_label)

        self._status.set_child(child_box)
        self.set_child(self._status)
        self._update_state()

    def _has_servers(self):
        from edith.services.config import ConfigService
        return len(ConfigService.load_servers()) > 0

    def _update_state(self):
        has = self._has_servers()
        if has:
            self._status.set_description(
                f"A code editor that talks (S)FTP\nVersion {VERSION}"
            )
            self._add_server_btn.set_visible(False)
            self._hint_label.set_visible(True)
        else:
            self._status.set_description(
                f"A code editor that talks (S)FTP\nVersion {VERSION}\n\n"
                "No servers configured yet.\n"
                "Add a server to get started."
            )
            self._add_server_btn.set_visible(True)
            self._hint_label.set_visible(False)

    def refresh(self):
        """Re-evaluate state and update the view."""
        self._update_state()

    def _on_add_btn_clicked(self, _btn):
        if self._on_add_server:
            self._on_add_server()

    def _on_link_activated(self, _label, uri):
        if uri == "create" and self._on_add_server:
            self._on_add_server()
            return True
        return False
