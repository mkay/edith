import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk, GObject

from edith.models.server import ServerInfo
from edith.services.config import ConfigService


class ServerEditDialog(Adw.Dialog):
    """Dialog for adding or editing a server."""

    __gsignals__ = {
        "saved": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, server_info: ServerInfo | None = None):
        super().__init__(
            title="Add Server" if server_info is None else "Edit Server",
            content_width=420,
            content_height=500,
        )

        self._server = server_info or ServerInfo()
        self._is_edit = server_info is not None

        self._build_ui()

    def _build_ui(self):
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_btn)

        save_btn = Gtk.Button(
            label="Save" if self._is_edit else "Add",
            css_classes=["suggested-action"],
        )
        save_btn.connect("clicked", self._on_save)
        header.pack_end(save_btn)
        self._save_btn = save_btn

        toolbar_view.add_top_bar(header)

        # Form
        clamp = Adw.Clamp(maximum_size=400, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # Name
        name_group = Adw.PreferencesGroup(title="Server")
        self._name_entry = Adw.EntryRow(title="Name (optional)")
        self._name_entry.set_text(self._server.name)
        name_group.add(self._name_entry)
        box.append(name_group)

        # Connection
        conn_group = Adw.PreferencesGroup(title="Connection")

        self._host_entry = Adw.EntryRow(title="Host")
        self._host_entry.set_text(self._server.host)
        conn_group.add(self._host_entry)

        self._port_adj = Gtk.Adjustment(
            value=self._server.port, lower=1, upper=65535, step_increment=1
        )
        self._port_row = Adw.SpinRow(
            title="Port",
            adjustment=self._port_adj,
        )
        conn_group.add(self._port_row)

        self._user_entry = Adw.EntryRow(title="Username")
        self._user_entry.set_text(self._server.username)
        conn_group.add(self._user_entry)

        box.append(conn_group)

        # Auth
        auth_group = Adw.PreferencesGroup(title="Authentication")

        self._auth_combo = Adw.ComboRow(title="Method")
        auth_model = Gtk.StringList.new(["Password", "SSH Key", "SSH Key + Passphrase"])
        self._auth_combo.set_model(auth_model)

        method_map = {"password": 0, "key": 1, "key+passphrase": 2}
        self._auth_combo.set_selected(method_map.get(self._server.auth_method, 0))
        self._auth_combo.connect("notify::selected", self._on_auth_changed)
        auth_group.add(self._auth_combo)

        self._key_entry = Adw.EntryRow(title="Key File Path")
        self._key_entry.set_text(self._server.key_file)
        auth_group.add(self._key_entry)
        self._key_entry.set_visible(self._server.auth_method != "password")

        box.append(auth_group)

        # Options
        opts_group = Adw.PreferencesGroup(title="Options")
        self._dir_entry = Adw.EntryRow(title="Initial Directory")
        self._dir_entry.set_text(self._server.initial_directory)
        opts_group.add(self._dir_entry)

        # Folder / group selector
        self._folders = ConfigService.load_folders()
        folder_names = ["None"] + [f.name for f in self._folders]
        self._folder_combo = Adw.ComboRow(title="Group")
        self._folder_combo.set_model(Gtk.StringList.new(folder_names))

        # Select the current folder
        selected_idx = 0
        for i, f in enumerate(self._folders):
            if f.id == self._server.folder_id:
                selected_idx = i + 1  # offset by 1 for "None"
                break
        self._folder_combo.set_selected(selected_idx)
        opts_group.add(self._folder_combo)

        box.append(opts_group)

        clamp.set_child(box)
        toolbar_view.set_content(clamp)

        self.set_child(toolbar_view)

    def _on_auth_changed(self, combo, pspec):
        sel = combo.get_selected()
        self._key_entry.set_visible(sel != 0)

    def _on_save(self, btn):
        host = self._host_entry.get_text().strip()
        username = self._user_entry.get_text().strip()

        if not host or not username:
            dialog = Adw.AlertDialog(
                heading="Missing Fields",
                body="Host and Username are required.",
            )
            dialog.add_response("ok", "OK")
            dialog.present(self)
            return

        auth_map = {0: "password", 1: "key", 2: "key+passphrase"}

        self._server.name = self._name_entry.get_text().strip()
        self._server.host = host
        self._server.port = int(self._port_row.get_value())
        self._server.username = username
        self._server.auth_method = auth_map.get(self._auth_combo.get_selected(), "password")
        self._server.key_file = self._key_entry.get_text().strip()
        self._server.initial_directory = self._dir_entry.get_text().strip() or "/"

        # Folder assignment
        folder_idx = self._folder_combo.get_selected()
        if folder_idx == 0:
            self._server.folder_id = ""
        else:
            self._server.folder_id = self._folders[folder_idx - 1].id

        self.emit("saved", self._server)
        self.close()
