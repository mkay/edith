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

    def __init__(self, server_info: ServerInfo | None = None, folder_id: str = ""):
        super().__init__(
            title="Add Server" if server_info is None else "Edit Server",
            content_width=420,
            content_height=500,
        )

        self._server = server_info or ServerInfo(folder_id=folder_id)
        self._is_edit = server_info is not None
        self._test_client = None

        self._build_ui()

    def _build_ui(self):
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", self._on_cancel)
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
        sw = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        clamp = Adw.Clamp(maximum_size=400, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # Main group — connection, auth, everything
        main_group = Adw.PreferencesGroup()

        self._name_entry = Adw.EntryRow(title="Name (optional)")
        self._name_entry.set_text(self._server.name)
        main_group.add(self._name_entry)

        self._protocol_combo = Adw.ComboRow(title="Protocol")
        protocol_model = Gtk.StringList.new(["SFTP", "FTP"])
        self._protocol_combo.set_model(protocol_model)
        protocol_map = {"sftp": 0, "ftp": 1}
        self._protocol_combo.set_selected(protocol_map.get(self._server.protocol, 0))
        self._protocol_combo.connect("notify::selected", self._on_protocol_changed)
        main_group.add(self._protocol_combo)

        self._encryption_combo = Adw.ComboRow(title="Encryption")
        encryption_model = Gtk.StringList.new([
            "None (insecure)",
            "Explicit TLS (if available)",
            "Explicit TLS (required)",
            "Implicit TLS",
        ])
        self._encryption_combo.set_model(encryption_model)
        encryption_map = {"none": 0, "explicit_optional": 1, "explicit_required": 2, "implicit": 3}
        ftp_enc = getattr(self._server, "ftp_encryption", "none")
        if self._server.protocol == "ftps":
            ftp_enc = "implicit"
        self._encryption_combo.set_selected(encryption_map.get(ftp_enc, 0))
        is_ftp = self._server.protocol in ("ftp", "ftps")
        self._encryption_combo.set_visible(is_ftp)
        self._encryption_combo.connect("notify::selected", self._on_encryption_changed)
        main_group.add(self._encryption_combo)

        self._host_entry = Adw.EntryRow(title="Host")
        self._host_entry.set_text(self._server.host)
        main_group.add(self._host_entry)

        default_port = 22 if self._server.protocol == "sftp" else self._get_ftp_default_port(ftp_enc)
        port_value = self._server.port if self._is_edit else default_port
        self._port_adj = Gtk.Adjustment(
            value=port_value, lower=1, upper=65535, step_increment=1
        )
        self._port_row = Adw.SpinRow(
            title="Port",
            adjustment=self._port_adj,
        )
        main_group.add(self._port_row)

        self._user_entry = Adw.EntryRow(title="Username")
        self._user_entry.set_text(self._server.username)
        main_group.add(self._user_entry)

        self._auth_combo = Adw.ComboRow(title="Authentication")
        auth_model = Gtk.StringList.new(["Password", "SSH Key", "SSH Key + Passphrase"])
        self._auth_combo.set_model(auth_model)
        method_map = {"password": 0, "key": 1, "key+passphrase": 2}
        self._auth_combo.set_selected(method_map.get(self._server.auth_method, 0))
        self._auth_combo.connect("notify::selected", self._on_auth_changed)
        self._auth_combo.set_visible(not is_ftp)
        main_group.add(self._auth_combo)

        self._key_entry = Adw.EntryRow(title="Key File Path")
        self._key_entry.set_text(self._server.key_file)
        self._key_entry.set_visible(
            not is_ftp and self._server.auth_method != "password"
        )
        main_group.add(self._key_entry)

        # Password / passphrase field — pre-fill from keyring
        self._pw_entry = Adw.PasswordEntryRow(title=self._password_label())
        if self._is_edit:
            from edith.services import credential_store
            stored = credential_store.get_password(self._server.id)
            if stored:
                self._pw_entry.set_text(stored)
        self._pw_entry.set_visible(self._needs_password())
        main_group.add(self._pw_entry)

        self._ftp_note = Gtk.Label(
            label="FTP uses passive mode (PASV).",
            xalign=0,
            css_classes=["dim-label", "caption"],
            margin_start=12,
            margin_top=4,
        )
        self._ftp_note.set_visible(is_ftp)
        main_group.add(self._ftp_note)

        box.append(main_group)

        # Options group
        opts_group = Adw.PreferencesGroup(title="Options")
        self._dir_entry = Adw.EntryRow(title="Initial Directory")
        self._dir_entry.set_text(self._server.initial_directory)
        opts_group.add(self._dir_entry)

        self._folders = ConfigService.load_folders()
        folder_names = ["None"] + [f.name for f in self._folders]
        self._folder_combo = Adw.ComboRow(title="Group")
        self._folder_combo.set_model(Gtk.StringList.new(folder_names))

        selected_idx = 0
        for i, f in enumerate(self._folders):
            if f.id == self._server.folder_id:
                selected_idx = i + 1
                break
        self._folder_combo.set_selected(selected_idx)
        opts_group.add(self._folder_combo)

        box.append(opts_group)

        # Test connection
        test_group = Adw.PreferencesGroup()

        self._test_status = Gtk.Label(
            xalign=0.5,
            css_classes=["caption"],
            visible=False,
            wrap=True,
            margin_bottom=4,
        )
        test_group.add(self._test_status)

        self._test_btn = Gtk.Button(
            label="Test Connection",
            css_classes=["flat"],
            halign=Gtk.Align.CENTER,
        )
        self._test_btn.connect("clicked", self._on_test)
        test_group.add(self._test_btn)

        box.append(test_group)

        clamp.set_child(box)
        sw.set_child(clamp)
        toolbar_view.set_content(sw)

        self.set_child(toolbar_view)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_ftp(self) -> bool:
        return self._protocol_combo.get_selected() != 0

    def _needs_password(self) -> bool:
        if self._is_ftp():
            return True
        auth = self._auth_combo.get_selected()
        # 0=password, 2=key+passphrase
        return auth != 1

    def _password_label(self) -> str:
        if self._is_ftp():
            return "Password (optional)"
        auth = self._auth_combo.get_selected()
        if auth == 2:
            return "Passphrase (optional)"
        return "Password (optional)"

    def _update_pw_visibility(self):
        self._pw_entry.set_visible(self._needs_password())
        self._pw_entry.set_title(self._password_label())

    @staticmethod
    def _get_ftp_default_port(encryption: str) -> int:
        return 990 if encryption == "implicit" else 21

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------

    def _on_protocol_changed(self, combo, pspec):
        is_sftp = combo.get_selected() == 0
        is_ftp = not is_sftp
        self._auth_combo.set_visible(is_sftp)
        self._key_entry.set_visible(is_sftp and self._auth_combo.get_selected() != 0)
        self._encryption_combo.set_visible(is_ftp)
        self._ftp_note.set_visible(is_ftp)
        self._update_pw_visibility()
        if is_ftp:
            self._encryption_combo.set_selected(1)
        current_port = int(self._port_row.get_value())
        if current_port in (21, 22, 990):
            if is_sftp:
                self._port_row.set_value(22)
            else:
                enc_map = {0: "none", 1: "explicit_optional", 2: "explicit_required", 3: "implicit"}
                enc = enc_map.get(self._encryption_combo.get_selected(), "none")
                self._port_row.set_value(self._get_ftp_default_port(enc))

    def _on_encryption_changed(self, combo, pspec):
        enc_map = {0: "none", 1: "explicit_optional", 2: "explicit_required", 3: "implicit"}
        enc = enc_map.get(combo.get_selected(), "none")
        current_port = int(self._port_row.get_value())
        if current_port in (21, 990):
            self._port_row.set_value(self._get_ftp_default_port(enc))

    def _on_auth_changed(self, combo, pspec):
        sel = combo.get_selected()
        self._key_entry.set_visible(sel != 0)
        self._update_pw_visibility()

    # ------------------------------------------------------------------
    # Test connection
    # ------------------------------------------------------------------

    def _on_test(self, btn):
        host = self._host_entry.get_text().strip()
        username = self._user_entry.get_text().strip()
        if not host or not username:
            self._show_test_status("Host and Username are required.", error=True)
            return

        self._test_btn.set_sensitive(False)
        self._test_btn.set_label("Connecting\u2026")
        self._test_status.set_visible(False)

        from edith.services.async_worker import run_async

        protocol_map = {0: "sftp", 1: "ftp"}
        encryption_map = {0: "none", 1: "explicit_optional", 2: "explicit_required", 3: "implicit"}
        protocol = protocol_map.get(self._protocol_combo.get_selected(), "sftp")
        encryption = encryption_map.get(self._encryption_combo.get_selected(), "none")
        port = int(self._port_row.get_value())
        password = self._pw_entry.get_text() or None
        if not password:
            from edith.services import credential_store
            password = credential_store.get_password(self._server.id)
        key_file = self._key_entry.get_text().strip() or None
        auth_sel = self._auth_combo.get_selected()

        def do_test():
            if protocol == "ftp":
                from edith.services.ftp_client import FtpClient
                client = FtpClient()
                client.connect(
                    host=host, port=port, username=username,
                    password=password, encryption=encryption,
                )
            else:
                from edith.services.sftp_client import SftpClient
                client = SftpClient()
                passphrase = password if auth_sel == 2 else None
                pw = password if auth_sel != 2 else None
                client.connect(
                    host=host, port=port, username=username,
                    password=pw, key_file=key_file, passphrase=passphrase,
                )
            # Store so we can close it
            self._test_client = client
            return client

        def on_success(client):
            self._show_test_status("Connection successful.", error=False)
            self._test_btn.set_sensitive(True)
            self._test_btn.set_label("Test Connection")
            # Store password on successful test so it survives into save
            if password:
                from edith.services import credential_store
                credential_store.store_password(self._server.id, password)
            # Close test connection
            try:
                client.close()
            except Exception:
                pass
            self._test_client = None

        def on_error(err):
            self._show_test_status(str(err), error=True)
            self._test_btn.set_sensitive(True)
            self._test_btn.set_label("Test Connection")
            self._test_client = None

        run_async(do_test, on_success, on_error)

    def _show_test_status(self, text: str, error: bool):
        self._test_status.set_label(text)
        for cls in ("success", "error"):
            self._test_status.remove_css_class(cls)
        self._test_status.add_css_class("error" if error else "success")
        self._test_status.set_visible(True)

    # ------------------------------------------------------------------
    # Save / Cancel
    # ------------------------------------------------------------------

    def _on_cancel(self, btn):
        self._close_test_client()
        self.close()

    def _close_test_client(self):
        if self._test_client:
            try:
                self._test_client.close()
            except Exception:
                pass
            self._test_client = None

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
        protocol_map = {0: "sftp", 1: "ftp"}
        encryption_map = {0: "none", 1: "explicit_optional", 2: "explicit_required", 3: "implicit"}

        self._server.name = self._name_entry.get_text().strip()
        self._server.host = host
        self._server.port = int(self._port_row.get_value())
        self._server.username = username
        self._server.protocol = protocol_map.get(self._protocol_combo.get_selected(), "sftp")
        self._server.ftp_encryption = encryption_map.get(self._encryption_combo.get_selected(), "none")
        self._server.auth_method = auth_map.get(self._auth_combo.get_selected(), "password")
        self._server.key_file = self._key_entry.get_text().strip()
        self._server.initial_directory = self._dir_entry.get_text().strip() or "/"

        # Folder assignment
        folder_idx = self._folder_combo.get_selected()
        if folder_idx == 0:
            self._server.folder_id = ""
        else:
            self._server.folder_id = self._folders[folder_idx - 1].id

        # Store password in keyring if provided
        password = self._pw_entry.get_text()
        if password:
            from edith.services import credential_store
            credential_store.store_password(self._server.id, password)

        self._close_test_client()
        self.emit("saved", self._server)
        self.close()
