# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk, GObject

from edith.models.server import ServerInfo
from edith.i18n import _


class ConnectDialog(Adw.Dialog):
    """Dialog prompting for password or passphrase when connecting."""

    __gsignals__ = {
        "connect": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }

    def __init__(self, server_info: ServerInfo):
        super().__init__(
            title=_("Connect to {server}").format(server=server_info.display_name),
            content_width=360,
            content_height=240,
        )

        self._server = server_info
        self._build_ui()

    def _build_ui(self):
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)

        cancel_btn = Gtk.Button(label=_("Cancel"))
        cancel_btn.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_btn)

        connect_btn = Gtk.Button(
            label=_("Connect"),
            css_classes=["suggested-action"],
        )
        connect_btn.connect("clicked", self._on_connect)
        header.pack_end(connect_btn)

        toolbar_view.add_top_bar(header)

        clamp = Adw.Clamp(maximum_size=360, margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        group = Adw.PreferencesGroup()

        needs_password = self._server.auth_method == "password"
        needs_passphrase = self._server.auth_method == "key+passphrase"

        self._password_row = None
        self._passphrase_row = None

        if needs_password:
            self._password_row = Adw.PasswordEntryRow(title=_("Password"))
            self._password_row.connect("entry-activated", lambda _: self._on_connect(None))
            group.add(self._password_row)

        if needs_passphrase:
            self._passphrase_row = Adw.PasswordEntryRow(title=_("Key Passphrase"))
            self._passphrase_row.connect("entry-activated", lambda _: self._on_connect(None))
            group.add(self._passphrase_row)

        self._remember_check = Gtk.CheckButton(label=_("Remember credential"), active=False)
        group.add(self._remember_check)

        box.append(group)

        info_label = Gtk.Label(
            label=_("Connecting as {user}@{host}:{port}").format(
                user=self._server.username, host=self._server.host, port=self._server.port),
            css_classes=["dim-label", "caption"],
            xalign=0,
        )
        box.append(info_label)

        clamp.set_child(box)
        toolbar_view.set_content(clamp)
        self.set_child(toolbar_view)

    def _on_connect(self, btn):
        password = ""  # nosec B105
        passphrase = ""  # nosec B105

        if self._password_row:
            password = self._password_row.get_text()
        if self._passphrase_row:
            passphrase = self._passphrase_row.get_text()

        # Store if requested
        if self._remember_check.get_active():
            from edith.services.credential_store import store_password
            credential = password or passphrase
            if credential:
                store_password(self._server.id, credential)

        self.emit("connect", password, passphrase)
        self.close()
