# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

import os
from pathlib import Path
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from edith.services.config import ConfigService
from edith.widgets.path_bar import PathBar
from edith.widgets.server_list import ServerList
from edith.widgets.server_panel import ServerPanel
from edith.widgets.file_browser import FileBrowser
from edith.widgets.editor_panel import EditorPanel
from edith.widgets.status_bar import StatusBar
from edith.widgets.transfer_panel import TransferPanel
from edith.widgets.connect_dialog import ConnectDialog
from edith.widgets.welcome_view import WelcomeView
from edith.services import credential_store
from edith.i18n import _, ngettext


class EdithWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        w = ConfigService.get_preference("window_width", 1100)
        h = ConfigService.get_preference("window_height", 700)
        super().__init__(
            default_width=w,
            default_height=h,
            title=_("Edith"),
            **kwargs,
        )

        self._sftp_client = None
        self._connected_server = None
        self._transfer_queue = None
        self._force_close = False
        self._server_panel_populated = False
        self._remote_mtimes = {}       # remote_path -> last known mtime
        self._saving_paths = set()     # paths currently being uploaded (suppress poll)
        self._poll_timer_id = None
        self._poll_in_flight = False
        self._reload_dialog_paths = set()  # paths with an open reload dialog
        self._sidebar_width_timer = None   # debounces saving the paned position
        self._sidebar_width_suppress = None  # ignores programmatic resizes

        from edith.services.external_edit import ExternalEditManager
        self._external_edits = ExternalEditManager()

        self._build_ui()
        self._setup_actions()

    def _build_ui(self):
        # === Sidebar ToolbarView (has its own HeaderBar with window controls) ===
        sidebar_header = Adw.HeaderBar(show_end_title_buttons=False)
        sidebar_header.set_title_widget(Gtk.Label(label=_("Edith"), css_classes=["title"]))

        self._new_server_btn = Gtk.Button(
            icon_name="edith-server-add-symbolic",
            tooltip_text=_("Add Server (Ctrl+N)"),
        )
        self._new_server_btn.connect("clicked", lambda _: self._on_new_server(None, None))
        sidebar_header.pack_start(self._new_server_btn)

        self._new_folder_btn = Gtk.Button(
            icon_name="edith-group-new-symbolic",
            tooltip_text=_("New Server Group"),
        )
        self._new_folder_btn.connect("clicked", lambda _: self._server_list.show_new_folder_dialog())
        sidebar_header.pack_start(self._new_folder_btn)



        self._sidebar_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            vexpand=True,
        )

        self._server_list = ServerList()
        self._server_list.connect("group-selected", self._on_group_selected)
        self._server_list.connect("add-server-to-folder", self._on_add_server_to_folder)
        self._sidebar_stack.add_named(self._server_list, "server_list")

        self._file_browser = FileBrowser()
        self._file_browser.set_window(self)
        self._file_browser.connect("file-activated", self._on_file_activated)
        self._file_browser.connect("pin-requested", self._on_pin_requested)
        self._file_browser.connect("path-changed", self._on_path_changed)
        self._sidebar_stack.add_named(self._file_browser, "file_browser")

        self._sidebar_stack.set_visible_child_name("server_list")

        self._sidebar_toolbar = Adw.ToolbarView()
        self._sidebar_toolbar.add_css_class("app-sidebar")
        self._sidebar_toolbar.set_size_request(180, -1)
        self._sidebar_toolbar.add_top_bar(sidebar_header)
        self._sidebar_toolbar.set_content(self._sidebar_stack)

        # Sidebar bottom bar: pinned section (hidden until connected) + status row
        sidebar_bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Pins list
        self._pins_lb = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=["navigation-sidebar"],
        )
        self._pins_lb.connect("row-activated", self._on_pin_activated)

        self._pins_context_path = None


        self._pins_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)
        pins_header = Gtk.Label(
            label=_("Pinned"),
            xalign=0,
            css_classes=["dim-label", "caption"],
            margin_start=12,
            margin_top=6,
            margin_bottom=2,
        )
        self._pins_section.append(pins_header)
        self._pins_section.append(self._pins_lb)
        sidebar_bottom.append(self._pins_section)

        self._pins_separator = Gtk.Separator(visible=False)
        sidebar_bottom.append(self._pins_separator)

        # Connection status row — a flat MenuButton that reveals connection
        # details in a popover while connected.
        status_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._sidebar_status_icon = Gtk.Image.new_from_icon_name("edith-disconnected-symbolic")
        self._sidebar_status_icon.set_pixel_size(16)
        status_content.append(self._sidebar_status_icon)
        self._sidebar_status_label = Gtk.Label(
            label=_("Disconnected"),
            xalign=0,
            hexpand=True,
            ellipsize=3,
            css_classes=["dim-label", "caption"],
        )
        status_content.append(self._sidebar_status_label)

        self._sidebar_status_popover = Gtk.Popover(has_arrow=True)
        self._sidebar_status_popover.add_css_class("menu")

        self._sidebar_status_btn = Gtk.MenuButton(
            css_classes=["flat"],
            margin_start=6,
            margin_end=6,
            margin_top=3,
            margin_bottom=3,
            sensitive=False,
        )
        self._sidebar_status_btn.set_child(status_content)
        self._sidebar_status_btn.set_popover(self._sidebar_status_popover)
        sidebar_bottom.append(self._sidebar_status_btn)

        self._sidebar_toolbar.add_bottom_bar(sidebar_bottom)

        # === Main ToolbarView (no window controls — they live in the sidebar header) ===
        # The sidebar header carries start-side window controls and this one
        # carries end-side controls, so the buttons land on whichever edge the
        # user's gtk-decoration-layout puts them on.
        self._main_header = Adw.HeaderBar(
            show_start_title_buttons=False,
            show_end_title_buttons=True,
        )

        self._connect_btn = Gtk.Button(
            icon_name="edith-connect-symbolic",
            tooltip_text=_("Connect"),
            sensitive=False,
        )
        self._connect_btn.connect("clicked", self._on_connect_btn_clicked)
        self._main_header.pack_start(self._connect_btn)

        self._back_btn = Gtk.Button(
            icon_name="edith-back-symbolic",
            tooltip_text=_("Back"),
            visible=False,
            sensitive=False,
        )
        self._back_btn.connect("clicked", lambda _: self._file_browser.go_back())
        self._main_header.pack_start(self._back_btn)

        self._forward_btn = Gtk.Button(
            icon_name="edith-forward-symbolic",
            tooltip_text=_("Forward"),
            visible=False,
            sensitive=False,
        )
        self._forward_btn.connect("clicked", lambda _: self._file_browser.go_forward())
        self._main_header.pack_start(self._forward_btn)

        # Centre: switches between group title (idle) and path bar (connected)
        self._header_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            hhomogeneous=False,
        )
        self._idle_title = Adw.WindowTitle(title="", subtitle="")
        self._header_stack.add_named(self._idle_title, "title")

        self._path_bar = PathBar()
        self._path_bar.connect("navigate", self._on_pathbar_navigate)
        self._header_stack.add_named(self._path_bar, "pathbar")

        self._header_stack.set_visible_child_name("title")
        self._main_header.set_title_widget(self._header_stack)

        menu = Gio.Menu()
        window_section = Gio.Menu()
        window_section.append(_("New Window"), "app.new-window")
        menu.append_section(None, window_section)
        server_section = Gio.Menu()
        server_section.append(_("Import Servers\u2026"), "win.import-servers")
        server_section.append(_("Export Servers\u2026"), "win.export-servers")
        menu.append_section(None, server_section)
        prefs_section = Gio.Menu()
        prefs_section.append(_("Preferences\u2026"), "app.preferences")
        prefs_section.append(_("Keyboard Shortcuts"), "app.shortcuts")
        prefs_section.append(_("About Edith"), "app.about")
        menu.append_section(None, prefs_section)

        self._transfer_panel = TransferPanel()
        self._transfer_btn = Gtk.MenuButton(
            icon_name="edith-transfers-symbolic",
            popover=self._transfer_panel,
            tooltip_text=_("Transfers"),
            visible=False,
            sensitive=False,
        )
        self._sidebar_visible = True
        self._sidebar_toggle = Gtk.Button(
            icon_name="edith-sidebar-hide-symbolic",
            tooltip_text=_("Toggle Sidebar (F9)"),
            focusable=False,
        )
        self._sidebar_toggle.connect("clicked", self._on_sidebar_toggled)
        menu_btn = Gtk.MenuButton(
            icon_name="edith-open-menu-symbolic",
            menu_model=menu,
            tooltip_text=_("Main Menu"),
        )
        self._main_header.pack_end(menu_btn)
        self._main_header.pack_end(self._transfer_btn)

        self._main_header.pack_end(self._sidebar_toggle)

        # Content stack
        self._content_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            vexpand=True,
        )

        self._welcome_view = WelcomeView(
            on_add_server=lambda: self._on_new_server(None, None),
        )
        self._content_stack.add_named(self._welcome_view, "welcome")

        self._server_panel = ServerPanel()
        self._server_panel.connect("server-activated", self._on_server_activated)
        self._server_panel.connect("selection-changed", self._on_server_selection_changed)
        self._server_panel.connect("servers-changed", self._on_servers_changed)
        self._content_stack.add_named(self._server_panel, "servers")

        self._connected_page = Adw.StatusPage(
            icon_name="edith-status-connected-symbolic",
            title="",
            description=_("Open a file from the sidebar to start editing."),
            vexpand=True,
        )
        self._content_stack.add_named(self._connected_page, "connected")

        self._editor_panel = EditorPanel()
        self._editor_panel.set_window(self)
        self._editor_panel.connect("page-changed", self._on_editor_page_changed)
        self._editor_panel.connect("line-ending-ready", self._on_line_ending_ready)
        self._content_stack.add_named(self._editor_panel, "editor")

        self._content_stack.set_visible_child_name("welcome")

        self._toast_overlay = Adw.ToastOverlay(vexpand=True)
        self._toast_overlay.set_child(self._content_stack)

        _toast_css = Gtk.CssProvider()
        _toast_css.load_from_string("""
            .toast-error-icon  { color: @error_color;   }
            .toast-success-icon { color: @success_color; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), _toast_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self._status_bar = StatusBar()
        self._status_bar.connect("language-selected", self._on_language_selected)
        self._status_bar.connect("indent-changed", self._on_indent_changed)
        self._status_bar.connect("line-ending-changed", self._on_line_ending_changed)
        self._status_bar.connect("cursor-clicked", lambda _: self._on_goto_line(None, None))
        self._status_bar.connect("wrap-toggled", self._on_status_wrap_toggled)
        self._status_bar.hide_connection_status()

        main_toolbar = Adw.ToolbarView()
        main_toolbar.add_top_bar(self._main_header)
        main_toolbar.add_bottom_bar(self._status_bar)
        main_toolbar.set_content(self._toast_overlay)

        # === Resizable paned: sidebar | main ===
        self._paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._sidebar_width = max(
            int(ConfigService.get_preference("sidebar_width", 280)), 180
        )

        self._paned.set_start_child(self._sidebar_toolbar)
        self._paned.set_resize_start_child(False)
        self._paned.set_shrink_start_child(False)
        self._paned.set_end_child(main_toolbar)
        self._paned.set_resize_end_child(True)
        self._paned.set_shrink_end_child(False)
        self._paned.set_position(self._sidebar_width)
        self._paned.connect("notify::position", self._on_sidebar_position_changed)

        self.set_content(self._paned)

        # Pre-load server data into the sidebar counts (no content switch yet)
        self._server_list.load_servers()

    def _setup_actions(self):
        self.connect("close-request", self._on_close_request)
        app = self.get_application()

        # Toggle sidebar
        toggle_sidebar = Gio.SimpleAction.new("toggle-sidebar", None)
        toggle_sidebar.connect("activate", self._on_toggle_sidebar)
        self.add_action(toggle_sidebar)
        app.set_accels_for_action("win.toggle-sidebar", ["F9"])

        # New server
        new_server = Gio.SimpleAction.new("new-server", None)
        new_server.connect("activate", self._on_new_server)
        self.add_action(new_server)
        app.set_accels_for_action("win.new-server", ["<Control>n"])

        # Import servers (Edith export or FileZilla sitemanager)
        import_servers = Gio.SimpleAction.new("import-servers", None)
        import_servers.connect("activate", self._on_import_servers)
        self.add_action(import_servers)

        # Export servers
        export_servers = Gio.SimpleAction.new("export-servers", None)
        export_servers.connect("activate", self._on_export_servers)
        self.add_action(export_servers)

        # Disconnect
        disconnect = Gio.SimpleAction.new("disconnect", None)
        disconnect.connect("activate", self._on_disconnect)
        disconnect.set_enabled(False)
        self.add_action(disconnect)
        app.set_accels_for_action("win.disconnect", [])

        # Save
        save = Gio.SimpleAction.new("save", None)
        save.connect("activate", self._on_save)
        save.set_enabled(False)
        self.add_action(save)
        app.set_accels_for_action("win.save", ["<Control>s"])

        # Close tab
        close_tab = Gio.SimpleAction.new("close-tab", None)
        close_tab.connect("activate", self._on_close_tab)
        close_tab.set_enabled(False)
        self.add_action(close_tab)
        app.set_accels_for_action("win.close-tab", ["<Control>w"])

        reopen_tab = Gio.SimpleAction.new("reopen-tab", None)
        reopen_tab.connect("activate", lambda *_: self._editor_panel.reopen_last_closed())
        self.add_action(reopen_tab)
        app.set_accels_for_action("win.reopen-tab", ["<Control><Shift>t"])

        # Search servers (no standalone accel — routed through win.find)
        search_servers = Gio.SimpleAction.new("search-servers", None)
        search_servers.connect("activate", self._on_search_servers)
        self.add_action(search_servers)

        # Find in file (Ctrl+F) — also falls back to server search when no editor
        find = Gio.SimpleAction.new("find", None)
        find.connect("activate", self._on_find)
        self.add_action(find)
        app.set_accels_for_action("win.find", ["<Control>f"])

        # Find + Replace (Ctrl+H)
        find_replace = Gio.SimpleAction.new("find-replace", None)
        find_replace.connect("activate", self._on_find_replace)
        self.add_action(find_replace)
        app.set_accels_for_action("win.find-replace", ["<Control><Shift>f"])

        # Go to line (Ctrl+G)
        goto_line = Gio.SimpleAction.new("goto-line", None)
        goto_line.connect("activate", self._on_goto_line)
        self.add_action(goto_line)
        app.set_accels_for_action("win.goto-line", ["<Control>g"])

        # Toggle line wrap (Ctrl+Shift+W)
        toggle_wrap = Gio.SimpleAction.new("toggle-wrap", None)
        toggle_wrap.connect("activate", self._on_toggle_wrap)
        self.add_action(toggle_wrap)
        app.set_accels_for_action("win.toggle-wrap", ["<Control><Shift>w"])

        # Undo / Redo — promoted to window actions so Ctrl+Z / Ctrl+Shift+Z /
        # Ctrl+Y keep working even when keyboard focus has drifted outside the
        # Monaco WebView (e.g. after closing a dialog or clicking the tab bar).
        undo = Gio.SimpleAction.new("undo", None)
        undo.connect("activate", self._on_undo)
        self.add_action(undo)
        app.set_accels_for_action("win.undo", ["<Control>z"])

        redo = Gio.SimpleAction.new("redo", None)
        redo.connect("activate", self._on_redo)
        self.add_action(redo)
        app.set_accels_for_action("win.redo", ["<Control><Shift>z", "<Control>y"])

    # --- Signal handlers ---

    def _on_close_request(self, window):
        # Flush a pending width save; the timer won't survive the window.
        if self._sidebar_width_timer:
            self._cancel_sidebar_width_save()
            self._save_sidebar_width()

        if self._force_close:
            return False  # allow close

        has_unsaved = self._editor_panel.has_unsaved()
        has_transfers = (
            self._transfer_queue is not None and self._transfer_queue.is_busy
        )

        if not has_unsaved and not has_transfers:
            return False  # nothing to warn about

        parts = []
        if has_unsaved:
            names = self._editor_panel.unsaved_filenames()
            parts.append("Unsaved changes in: " + ", ".join(names))
        if has_transfers:
            parts.append("Active file transfers will be cancelled.")

        if has_unsaved and has_transfers:
            heading = "Quit with unsaved changes and active transfers?"
        elif has_unsaved:
            heading = "Quit with unsaved changes?"
        else:
            heading = "Quit with active transfers?"

        dialog = Adw.AlertDialog(
            heading=heading,
            body="\n\n".join(parts),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("quit", _("Quit Anyway"))
        dialog.set_response_appearance("quit", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        def on_response(d, response):
            if response == "quit":
                self._force_close = True
                self.close()

        dialog.connect("response", on_response)
        dialog.present(self)
        return True  # block the close

    def _on_sidebar_toggled(self, btn):
        self._toggle_sidebar()

    def _on_toggle_sidebar(self, action, param):
        self._toggle_sidebar()

    def _toggle_sidebar(self):
        if self._sidebar_visible:
            self._cancel_sidebar_width_save()
            self._sidebar_width = max(self._paned.get_position(), 180)
            ConfigService.set_preference("sidebar_width", self._sidebar_width)
            self._sidebar_toolbar.set_visible(False)
            self._sidebar_visible = False
            self._sidebar_toggle.set_icon_name("edith-sidebar-show-symbolic")
            # Window controls move to the main header when sidebar is hidden
            self._main_header.set_show_start_title_buttons(True)
        else:
            self._sidebar_toolbar.set_visible(True)
            self._paned.set_position(self._sidebar_width)
            self._sidebar_visible = True
            self._sidebar_toggle.set_icon_name("edith-sidebar-hide-symbolic")
            self._main_header.set_show_start_title_buttons(False)

    def _on_group_selected(self, server_list, group_key):
        folders = ConfigService.load_folders()
        servers = ConfigService.load_servers()
        self._server_panel.show_group(group_key, folders, servers)
        self._server_panel_populated = True
        if group_key == "__all__":
            title = "All Servers"
        elif group_key == "__ungrouped__":
            title = "Without Group"
        else:
            folder = next((f for f in folders if f.id == group_key), None)
            title = folder.name if folder else ""
        self._idle_title.set_title(title)
        if self._content_stack.get_visible_child_name() == "welcome":
            self._content_stack.set_visible_child_name("servers")

    def _on_servers_changed(self, *_args):
        # Preserve the currently selected group across add/edit/duplicate/delete
        # (load_servers() rebuilds the list and clears the selection).
        current_key = self._server_list.get_selected_key()
        self._server_list.load_servers()
        self._server_list.select_group(current_key or "__all__")
        self._welcome_view.refresh()

    def _on_new_server(self, action, param):
        self._server_panel.show_add_dialog()

    def _show_message(self, heading, body):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("ok", _("OK"))
        dialog.present(self)

    # --- Import ---

    def _on_import_servers(self, action, param):
        dialog = Gtk.FileDialog(title=_("Import Servers"))

        both = Gtk.FileFilter()
        both.set_name(_("Server lists"))
        both.add_pattern("*.json")
        both.add_pattern("*.xml")
        edith_filter = Gtk.FileFilter()
        edith_filter.set_name(_("Edith export (*.json)"))
        edith_filter.add_pattern("*.json")
        fz_filter = Gtk.FileFilter()
        fz_filter.set_name(_("FileZilla sites (*.xml)"))
        fz_filter.add_pattern("*.xml")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        for f in (both, edith_filter, fz_filter):
            filters.append(f)
        dialog.set_filters(filters)
        dialog.open(self, None, self._on_import_file_chosen)

    def _on_import_file_chosen(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if gfile is None:
            return

        from edith.services import servers_transfer

        # A non-local pick (gvfs mount, say) has no on-disk path to read.
        local_path = gfile.get_path()
        if local_path is None:
            self._show_message(
                _("Import Failed"),
                _("That location can't be read directly. Copy the file to this computer first."),
            )
            return

        path = Path(local_path)
        try:
            kind = servers_transfer.detect_format(path)
            if kind == "filezilla":
                from edith.services.filezilla_import import parse_sitemanager
                servers, folders = parse_sitemanager(path)
            else:
                servers, folders = servers_transfer.parse_export(path)
        except Exception as e:
            self._show_message(_("Import Failed"), str(e))
            return

        if not servers:
            self._show_message(
                _("No Servers Found"),
                _("The selected file contained no server entries."),
            )
            return

        # Replacing is only a question when there is something to lose, and it
        # is the destructive answer, so it is never the default.
        if ConfigService.load_servers():
            choice = Adw.AlertDialog(
                heading=_("Import Servers"),
                body=ngettext(
                    "The file contains {n} server.",
                    "The file contains {n} servers.",
                    len(servers),
                ).format(n=len(servers)) + "\n\n" + _(
                    "Merging keeps your current servers and updates any that "
                    "match. Replacing discards your current list entirely."
                ),
            )
            choice.add_response("cancel", _("Cancel"))
            choice.add_response("replace", _("Replace"))
            choice.add_response("merge", _("Merge"))
            choice.set_response_appearance("replace", Adw.ResponseAppearance.DESTRUCTIVE)
            choice.set_response_appearance("merge", Adw.ResponseAppearance.SUGGESTED)
            choice.set_default_response("merge")
            choice.set_close_response("cancel")

            def on_response(d, response):
                if response in ("merge", "replace"):
                    self._finish_import(
                        servers, folders, response == "replace", kind, path
                    )

            choice.connect("response", on_response)
            choice.present(self)
        else:
            self._finish_import(servers, folders, False, kind, path)

    def _finish_import(self, servers, folders, replace, kind, path):
        from edith.services import servers_transfer

        try:
            res = servers_transfer.apply_import(servers, folders, replace=replace)
        except Exception as e:
            self._show_message(_("Import Failed"), str(e))
            return

        self._server_list.load_servers()
        self._server_panel.reload()
        self._server_panel.emit("servers-changed")

        # Built from whole sentences: a translator can reorder within each one,
        # and every count gets its own plural form.
        # Re-importing the same export updates everything and adds nothing, and
        # "Imported 0 servers." is a poor way to report that it worked.
        parts = []
        if res.added or not res.updated:
            parts.append(
                ngettext(
                    "Imported {n} server.", "Imported {n} servers.", res.added
                ).format(n=res.added)
            )
        if res.updated:
            parts.append(
                ngettext(
                    "Updated {n} existing server.",
                    "Updated {n} existing servers.",
                    res.updated,
                ).format(n=res.updated)
            )
        if res.folders_added:
            parts.append(
                ngettext(
                    "Added {n} group.", "Added {n} groups.", res.folders_added
                ).format(n=res.folders_added)
            )

        summary = " ".join(parts)
        imported_ids = {s.id for s in servers}

        blob = (
            servers_transfer.read_secrets_blob(path) if kind != "filezilla" else None
        )
        if blob:
            self._ask_import_passphrase(blob, imported_ids, summary)
            return

        if kind == "filezilla":
            note = _("Passwords could not be imported — you'll need to re-enter them.")
        else:
            # Ids survive the round trip, so the keyring lookup still matches.
            note = _(
                "This file contains no saved passwords. On this machine the "
                "restored servers still find any passwords already in your "
                "keyring; on another machine you'll need to re-enter them."
            )

        self._show_message(_("Import Complete"), summary + "\n\n" + note)

    def _ask_import_passphrase(self, blob, imported_ids, summary, error=None):
        dialog = Adw.AlertDialog(
            heading=_("Saved Passwords Found"),
            body=error or _(
                "This file includes encrypted passwords. Enter the passphrase "
                "used when it was exported to add them to your keyring."
            ),
        )
        group = Adw.PreferencesGroup()
        pass_row = Adw.PasswordEntryRow(title=_("Passphrase"))
        group.add(pass_row)
        dialog.set_extra_child(group)

        dialog.add_response("skip", _("Skip"))
        dialog.add_response("restore", _("Restore Passwords"))
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("restore")
        dialog.set_close_response("skip")

        def on_response(d, response):
            if response != "restore":
                self._show_message(
                    _("Import Complete"),
                    summary + "\n\n" + _("Saved passwords were skipped."),
                )
                return
            self._do_restore_secrets(
                blob, pass_row.get_text(), imported_ids, summary
            )

        dialog.connect("response", on_response)
        dialog.present(self)

    def _do_restore_secrets(self, blob, passphrase, imported_ids, summary):
        from edith.services import servers_transfer
        from edith.services.async_worker import run_async

        try:
            secrets = servers_transfer.decrypt_secrets(blob, passphrase)
        except ValueError as e:
            # Wrong passphrase is the expected mistake, so ask again in place
            # rather than making the user redo the whole import.
            self._ask_import_passphrase(blob, imported_ids, summary, error=str(e))
            return

        # ~100ms per keyring write puts a few hundred of them well past the
        # freeze watchdog's 10s threshold, so this runs on a worker with a
        # progress bar rather than locking the window.
        progress = Adw.AlertDialog(
            heading=_("Restoring Passwords"),
            body=_("Adding saved passwords to your keyring…"),
        )
        bar = Gtk.ProgressBar(show_text=True)
        progress.set_extra_child(bar)
        progress.present(self)

        def on_progress(done, total):
            def update():
                bar.set_fraction(done / total if total else 1.0)
                bar.set_text(f"{done} / {total}")
                return GLib.SOURCE_REMOVE

            GLib.idle_add(update)

        def task():
            return servers_transfer.restore_secrets(
                secrets, imported_ids, progress=on_progress
            )

        def on_success(count):
            progress.close()
            note = ngettext(
                "Restored {n} saved password.",
                "Restored {n} saved passwords.",
                count,
            ).format(n=count)
            skipped = len(secrets) - count
            if skipped > 0:
                note += " " + ngettext(
                    "{n} belonged to a server that wasn't imported.",
                    "{n} belonged to servers that weren't imported.",
                    skipped,
                ).format(n=skipped)
            self._show_message(_("Import Complete"), summary + "\n\n" + note)

        def on_error(e):
            progress.close()
            self._show_message(_("Import Failed"), str(e))

        run_async(task, on_success, on_error)

    # --- Export ---

    def _on_export_servers(self, action, param):
        if not ConfigService.load_servers():
            self._show_message(
                _("Nothing to Export"),
                _("You have no saved servers yet."),
            )
            return

        from edith.services import servers_transfer

        dialog = Gtk.FileDialog(
            title=_("Export Servers"),
            initial_name=servers_transfer.default_export_name(),
        )
        json_filter = Gtk.FileFilter()
        json_filter.set_name(_("Edith export (*.json)"))
        json_filter.add_pattern("*.json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(json_filter)
        dialog.set_filters(filters)
        dialog.save(self, None, self._on_export_file_chosen)

    def _on_export_file_chosen(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
        except GLib.Error:
            return
        if gfile is None:
            return

        from edith.services import servers_transfer

        local_path = gfile.get_path()
        if local_path is None:
            self._show_message(
                _("Export Failed"),
                _("That location can't be written to directly. Choose a folder on this computer."),
            )
            return

        path = Path(local_path)

        # The keyring is local to this machine, so an export without passwords
        # migrates a server list that cannot connect to anything. Offer to
        # bring them along, encrypted — there is no option to write them in
        # plain text.
        dialog = Adw.AlertDialog(
            heading=_("Include Saved Passwords?"),
            body=_(
                "Passwords live in your system keyring and don't travel with a "
                "plain export. They can be included, encrypted with a "
                "passphrase you choose — you'll need that same passphrase to "
                "import them on the other machine."
            ),
        )

        group = Adw.PreferencesGroup()
        pass_row = Adw.PasswordEntryRow(title=_("Passphrase"))
        confirm_row = Adw.PasswordEntryRow(title=_("Confirm Passphrase"))
        group.add(pass_row)
        group.add(confirm_row)
        dialog.set_extra_child(group)

        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("skip", _("Without Passwords"))
        dialog.add_response("include", _("Include Passwords"))
        dialog.set_response_appearance("include", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("include")
        dialog.set_close_response("cancel")
        # A mistyped passphrase would produce a file nobody can ever open, so
        # the action stays disabled until both entries agree.
        dialog.set_response_enabled("include", False)

        def on_changed(*_args):
            text = pass_row.get_text()
            dialog.set_response_enabled(
                "include", bool(text) and text == confirm_row.get_text()
            )

        pass_row.connect("changed", on_changed)
        confirm_row.connect("changed", on_changed)

        def on_response(d, response):
            if response == "cancel":
                return
            passphrase = pass_row.get_text() if response == "include" else None
            self._do_export(path, passphrase)

        dialog.connect("response", on_response)
        dialog.present(self)

    def _do_export(self, path, passphrase):
        from edith.services import servers_transfer
        from edith.services.async_worker import run_async

        servers = ConfigService.load_servers()
        folders = ConfigService.load_folders()

        # Reading a few hundred passwords out of the keyring costs seconds, so
        # it does not belong on the main loop.
        def task():
            blob = None
            count = 0
            if passphrase:
                secrets = servers_transfer.collect_secrets(servers)
                count = len(secrets)
                if secrets:
                    blob = servers_transfer.encrypt_secrets(secrets, passphrase)
            servers_transfer.export_servers(path, servers, folders, secrets=blob)
            return count

        def on_success(count):
            summary = ngettext(
                "Exported {n} server.", "Exported {n} servers.", len(servers)
            ).format(n=len(servers))
            if count:
                note = ngettext(
                    "{n} saved password was included, encrypted.",
                    "{n} saved passwords were included, encrypted.",
                    count,
                ).format(n=count)
            elif passphrase:
                note = _("No saved passwords were found to include.")
            else:
                note = _("Passwords are not included — they stay in your system keyring.")
            self._show_message(_("Export Complete"), summary + "\n\n" + note)

        def on_error(e):
            self._show_message(_("Export Failed"), str(e))

        run_async(task, on_success, on_error)

    def _on_add_server_to_folder(self, server_list, folder_id):
        self._server_panel.show_add_dialog(folder_id=folder_id)

    def _on_disconnect(self, action, param):
        self.disconnect_server()

    def _on_save(self, action, param):
        self._editor_panel.save_current()

    def _on_close_tab(self, action, param):
        self._editor_panel.close_current()

    def _on_search_servers(self, action, param):
        if self._sidebar_stack.get_visible_child_name() == "server_list":
            self._server_panel.toggle_search()

    def _on_find(self, action, param):
        editor = self._editor_panel.get_current_editor()
        if editor:
            editor.show_find()
        elif self._sidebar_stack.get_visible_child_name() == "server_list":
            self._server_panel.toggle_search()

    def _on_find_replace(self, action, param):
        editor = self._editor_panel.get_current_editor()
        if editor:
            editor.show_replace()

    def _on_toggle_wrap(self, action, param):
        editor = self._editor_panel.get_current_editor()
        if editor:
            editor.toggle_wrap()

    def _on_undo(self, action, param):
        editor = self._editor_panel.get_current_editor()
        if editor:
            editor.undo()

    def _on_redo(self, action, param):
        editor = self._editor_panel.get_current_editor()
        if editor:
            editor.redo()

    def _on_goto_line(self, action, param):
        editor = self._editor_panel.get_current_editor()
        if not editor:
            return

        dialog = Adw.AlertDialog(heading=_("Go to Line"), body="")
        entry = Gtk.Entry(
            input_purpose=Gtk.InputPurpose.DIGITS,
            placeholder_text=_("Line number…"),
            activates_default=True,
            width_chars=12,
        )
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("go", _("Go"))
        dialog.set_default_response("go")
        dialog.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response):
            if response == "go":
                try:
                    line = int(entry.get_text()) - 1
                    editor.goto_line(line)
                except ValueError:
                    pass

        dialog.connect("map", lambda _: entry.grab_focus())
        dialog.connect("response", on_response)
        dialog.present(self)

    def _on_connect_btn_clicked(self, btn):
        if self._sftp_client:
            self.disconnect_server()
        else:
            server = self._server_panel.get_selected_server()
            if server:
                self._initiate_connection(server)

    def _on_server_selection_changed(self, server_panel, is_server):
        if not self._sftp_client:
            self._connect_btn.set_sensitive(is_server)

    def _on_server_activated(self, server_list, server_info):
        self._initiate_connection(server_info)

    def _on_file_activated(self, file_browser, remote_path):
        self.open_remote_file(remote_path)

    # --- Connection flow ---

    def _initiate_connection(self, server_info):
        """Start the connection flow — prompt for credentials if needed."""
        protocol = getattr(server_info, "protocol", "sftp")

        # Try stored credential first
        stored = credential_store.get_password(server_info.id)

        if protocol in ("ftp", "ftps"):
            # FTP uses password-only auth
            if stored:
                self.connect_to_server(server_info, password=stored)
            else:
                dialog = ConnectDialog(server_info)
                dialog.connect("connect", lambda d, pw, pp: self.connect_to_server(server_info, password=pw))
                dialog.present(self)
            return

        if server_info.auth_method == "key" and server_info.key_file:
            # Key-only auth, no password needed
            self.connect_to_server(server_info)
            return

        if stored:
            if server_info.auth_method == "password":
                self.connect_to_server(server_info, password=stored)
            else:
                self.connect_to_server(server_info, passphrase=stored)
            return

        # Show connect dialog for password/passphrase
        dialog = ConnectDialog(server_info)
        dialog.connect("connect", lambda d, pw, pp: self.connect_to_server(server_info, password=pw, passphrase=pp))
        dialog.present(self)

    def connect_to_server(self, server_info, password=None, passphrase=None):
        """Initiate connection to a server."""
        from edith.services.async_worker import run_async

        self._set_status("connecting", f"Connecting to {server_info.host}...")

        initial_dir = server_info.initial_directory or "/"
        protocol = getattr(server_info, "protocol", "sftp")

        def do_connect():
            if protocol in ("ftp", "ftps"):
                from edith.services.ftp_client import FtpClient
                client = FtpClient()
                encryption = getattr(server_info, "ftp_encryption", "none")
                # Migrate legacy "ftps" protocol value
                if protocol == "ftps" and encryption == "none":
                    encryption = "implicit"
                client.connect(
                    host=server_info.host,
                    port=server_info.port,
                    username=server_info.username,
                    password=password,
                    encryption=encryption,
                )
            else:
                from edith.services.sftp_client import SftpClient
                client = SftpClient()
                client.connect(
                    host=server_info.host,
                    port=server_info.port,
                    username=server_info.username,
                    password=password,
                    key_file=server_info.key_file or None,
                    passphrase=passphrase,
                )
            resolved = initial_dir
            if initial_dir == "~" or initial_dir.startswith("~/"):
                home = client.normalize(".")
                resolved = home + initial_dir[1:]
            # Probe exec capability (shared hosts often block it)
            if hasattr(client, "exec_command"):
                try:
                    code, _, _ = client.exec_command("echo ok", timeout=5)
                    client.can_exec = code == 0
                except Exception:
                    client.can_exec = False
            return client, resolved

        def on_success(result):
            client, resolved_dir = result
            self._sftp_client = client
            self._connected_server = server_info
            self._on_connected(server_info, resolved_dir)

        def on_error(error):
            self._set_status("error", f"Connection failed: {error}")
            dialog = Adw.AlertDialog(
                heading=_("Connection Failed"),
                body=str(error),
            )
            dialog.add_response("ok", _("OK"))
            dialog.present(self)

        run_async(do_connect, on_success, on_error)

    def disconnect_server(self):
        """Disconnect from current server, confirming if there are unsaved changes."""
        if self._editor_panel.has_unsaved():
            names = self._editor_panel.unsaved_filenames()
            body = "Unsaved changes in: " + ", ".join(names)
            dialog = Adw.AlertDialog(
                heading=_("Disconnect with unsaved changes?"),
                body=body,
            )
            dialog.add_response("cancel", _("Cancel"))
            dialog.add_response("discard", _("Discard & Disconnect"))
            dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", self._on_disconnect_response)
            dialog.present(self)
            return

        self._do_disconnect()

    def _on_disconnect_response(self, dialog, response):
        if response == "discard":
            self._do_disconnect()

    def _do_disconnect(self):
        """Actually disconnect and clean up."""
        if self._transfer_queue:
            self._transfer_queue.clear()
            self._transfer_queue = None
        self._transfer_panel.unbind_queue()
        self._transfer_btn.set_visible(False)
        self._transfer_btn.set_sensitive(False)

        if self._sftp_client:
            from edith.services.async_worker import run_async

            client = self._sftp_client
            self._sftp_client = None
            self._connected_server = None

            run_async(lambda: client.close(), lambda _: None, lambda _: None)

        self._on_disconnected()

    def _on_pathbar_navigate(self, path_bar, path):
        self._file_browser.load_directory(path)

    def _on_path_changed(self, browser, path):
        if self._connected_server:
            self._path_bar.set_path(path)
            self._back_btn.set_sensitive(browser.can_go_back)
            self._forward_btn.set_sensitive(browser.can_go_forward)

    def _on_connected(self, server_info, initial_dir=None):
        """Called after successful connection."""
        self._set_status("connected", f"Connected to {server_info.username}@{server_info.host}")
        self.lookup_action("disconnect").set_enabled(True)
        self._connect_btn.set_icon_name("edith-disconnect-symbolic")
        self._connect_btn.set_tooltip_text(_("Disconnect (Ctrl+D)"))

        self._header_stack.set_visible_child_name("pathbar")
        self._back_btn.set_visible(True)
        self._forward_btn.set_visible(True)

        # Switch sidebar to file browser, load initial directory
        initial = initial_dir or server_info.initial_directory or "/"
        self._file_browser.load_directory(initial)
        self._sidebar_stack.set_visible_child_name("file_browser")

        # Enable editor actions
        self.lookup_action("save").set_enabled(True)
        self.lookup_action("close-tab").set_enabled(True)

        # Hide server/folder buttons while connected
        self._new_server_btn.set_visible(False)
        self._new_folder_btn.set_visible(False)

        # Set up transfer queue
        from edith.services.transfer_queue import TransferQueue
        self._transfer_queue = TransferQueue()
        self._transfer_queue.connect("queued",   self._on_xfer_queued)
        self._transfer_queue.connect("started",  self._on_xfer_started)
        self._transfer_queue.connect("progress", self._on_xfer_progress)
        self._transfer_queue.connect("done",     self._on_xfer_done)
        self._transfer_queue.connect("failed",   self._on_xfer_failed)
        self._transfer_queue.connect("idle",     self._on_xfer_idle)
        self._transfer_panel.bind_queue(self._transfer_queue)
        self._transfer_btn.set_visible(True)
        self._transfer_btn.set_sensitive(False)

        # Start remote file-change polling
        self._poll_timer_id = GLib.timeout_add_seconds(3, self._poll_remote_mtimes)

        # Show connected placeholder until the user opens a file
        self._connected_page.set_title(_("Connected to {server}").format(server=server_info.display_name))
        self._rebuild_recents_child(server_info)
        self._rebuild_pins_bar(server_info)
        self._content_stack.set_visible_child_name("connected")

        self.show_toast(_("Connected to {server}").format(server=server_info.display_name), "success")

    def _rebuild_recents_child(self, server_info):
        recents = ConfigService.get_recents(server_info.id)
        if not recents:
            self._connected_page.set_child(None)
            self._connected_page.set_description(_("Open a file from the sidebar to start editing."))
            return

        self._connected_page.set_description(None)

        lb = Gtk.ListBox(
            css_classes=["boxed-list"],
            selection_mode=Gtk.SelectionMode.NONE,
        )

        self._recents_context_path = None

        for path in recents:
            row = Adw.ActionRow(title=os.path.basename(path), subtitle=path, activatable=True)
            row._recent_path = path
            row.connect("activated", self._on_recent_activated)

            row_menu = Gio.Menu()
            row_menu.append(_("Remove from List"), "recents.remove")
            row_popover = Gtk.PopoverMenu(menu_model=row_menu, has_arrow=False)
            row_popover.set_parent(row)

            row_group = Gio.SimpleActionGroup()
            remove_action = Gio.SimpleAction.new("remove", None)
            remove_action.connect("activate", self._on_recent_remove)
            row_group.add_action(remove_action)
            row.insert_action_group("recents", row_group)

            gesture = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            gesture.connect("pressed", self._on_row_right_click, row, row_popover)
            row.add_controller(gesture)
            lb.append(row)

        clamp = Adw.Clamp(maximum_size=480, margin_top=4, margin_bottom=4)
        clamp.set_child(lb)
        self._connected_page.set_child(clamp)

    def _on_recent_activated(self, row):
        self.open_remote_file(row._recent_path)

    def _on_row_right_click(self, gesture, n_press, x, y, row, popover):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._recents_context_path = row._recent_path
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _on_recent_remove(self, action, param):
        if self._recents_context_path and self._connected_server:
            ConfigService.delete_recent(self._connected_server.id, self._recents_context_path)
            self._recents_context_path = None
            self._rebuild_recents_child(self._connected_server)

    def _on_pin_requested(self, browser, path, is_dir):
        if self._connected_server:
            ConfigService.add_pin(self._connected_server.id, path, is_dir)
            self._rebuild_pins_bar(self._connected_server)

    def pin_path(self, path, is_dir=False):
        """Pin a path (used by the editor tab context menu)."""
        self._on_pin_requested(None, path, is_dir)

    def _rebuild_pins_bar(self, server_info):
        from edith.models.remote_file import RemoteFileInfo
        while (row := self._pins_lb.get_row_at_index(0)) is not None:
            self._pins_lb.remove(row)

        pins = ConfigService.get_pins(server_info.id)
        if not pins:
            self._pins_section.set_visible(False)
            self._pins_separator.set_visible(False)
            return

        for entry in pins:
            path = entry["path"]
            is_dir = entry["is_dir"]
            name = os.path.basename(path.rstrip("/")) or path
            fi = RemoteFileInfo(name=name, path=path, is_dir=is_dir)

            box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=6,
                margin_start=12,
                margin_end=12,
                margin_top=3,
                margin_bottom=3,
            )
            box.append(Gtk.Image(icon_name=fi.icon_name, pixel_size=16))
            lbl = Gtk.Label(label=name, xalign=0, hexpand=True, ellipsize=3,
                            css_classes=["caption"])
            box.append(lbl)

            row = Gtk.ListBoxRow(activatable=True)
            row._pin_entry = entry
            row.set_child(box)

            pin_menu = Gio.Menu()
            pin_menu.append(_("Unpin"), "pins.unpin")
            row_popover = Gtk.PopoverMenu(menu_model=pin_menu, has_arrow=False)
            row_popover.set_parent(row)

            row_group = Gio.SimpleActionGroup()
            unpin_action = Gio.SimpleAction.new("unpin", None)
            unpin_action.connect("activate", self._on_pin_remove)
            row_group.add_action(unpin_action)
            row.insert_action_group("pins", row_group)

            gesture = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            gesture.connect("pressed", self._on_pin_row_right_click, row, row_popover)
            row.add_controller(gesture)
            self._pins_lb.append(row)

        self._pins_section.set_visible(True)
        self._pins_separator.set_visible(True)

    def _on_pin_activated(self, lb, row):
        entry = row._pin_entry
        if entry["is_dir"]:
            self._file_browser.load_directory(entry["path"])
        else:
            self.open_remote_file(entry["path"])

    def _on_pin_row_right_click(self, gesture, n_press, x, y, row, popover):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._pins_context_path = row._pin_entry["path"]
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _on_pin_remove(self, action, param):
        if self._pins_context_path and self._connected_server:
            ConfigService.delete_pin(self._connected_server.id, self._pins_context_path)
            self._pins_context_path = None
            self._rebuild_pins_bar(self._connected_server)

    def _on_editor_page_changed(self, panel):
        from edith.services.config import ConfigService
        editor = panel.get_current_editor()
        if editor:
            self._status_bar.set_language_name(editor.get_language_name())
            insert_spaces = ConfigService.get_preference("editor_insert_spaces", True)
            tab_size = ConfigService.get_preference("editor_tab_size", 4)
            self._status_bar.set_indent(insert_spaces, tab_size)
            self._status_bar.set_line_ending(editor.get_line_ending())
            self._status_bar.set_cursor_position(*editor.get_cursor_position())
            self._status_bar.set_word_wrap(editor.get_word_wrap())
            # Connect per-editor signals once (flag prevents re-connecting on tab switch)
            if not getattr(editor, "_window_signals_connected", False):
                editor.connect("cursor-changed", self._on_editor_cursor_changed)
                editor.connect("wrap-changed", self._on_editor_wrap_changed)
                editor._window_signals_connected = True
        else:
            self._status_bar.hide_file_info()
            if not self._editor_panel.has_selected_page and self._connected_server:
                self._rebuild_recents_child(self._connected_server)
                self._content_stack.set_visible_child_name("connected")

    def _on_line_ending_ready(self, panel, eol):
        self._status_bar.set_line_ending(eol)

    def _on_language_selected(self, status_bar, lang_id):
        editor = self._editor_panel.get_current_editor()
        if editor:
            editor.set_language(lang_id or None)
            self._status_bar.set_language_name(editor.get_language_name())

    def _on_indent_changed(self, status_bar, insert_spaces, tab_size):
        self._editor_panel.apply_indent(insert_spaces, tab_size)

    def _on_line_ending_changed(self, status_bar, eol):
        self._editor_panel.set_current_line_ending(eol)

    def _on_status_wrap_toggled(self, status_bar):
        editor = self._editor_panel.get_current_editor()
        if editor:
            editor.toggle_wrap()

    def _on_editor_cursor_changed(self, editor, line, col):
        if editor is self._editor_panel.get_current_editor():
            self._status_bar.set_cursor_position(line, col)

    def _on_editor_wrap_changed(self, editor, enabled):
        if editor is self._editor_panel.get_current_editor():
            self._status_bar.set_word_wrap(enabled)

    def _on_disconnected(self):
        """Called after disconnection."""
        if self._poll_timer_id:
            GLib.source_remove(self._poll_timer_id)
            self._poll_timer_id = None
        self._external_edits.stop_all()
        self._remote_mtimes.clear()
        self._saving_paths.clear()
        self._reload_dialog_paths.clear()
        self._poll_in_flight = False
        self._status_bar.clear_transfer()
        self._set_status("disconnected", "Disconnected")
        self._status_bar.hide_file_info()
        self._header_stack.set_visible_child_name("title")
        self._back_btn.set_visible(False)
        self._forward_btn.set_visible(False)
        self._back_btn.set_sensitive(False)
        self._forward_btn.set_sensitive(False)
        self._file_browser.reset_history()
        # Leave extended view so the sidebar returns to its default width.
        self._file_browser.reset_detail_mode()
        self.lookup_action("disconnect").set_enabled(False)
        self._connect_btn.set_icon_name("edith-connect-symbolic")
        self._connect_btn.set_tooltip_text(_("Connect"))
        self._connect_btn.set_sensitive(self._server_panel.get_selected_server() is not None)

        # Hide pinned section
        self._pins_section.set_visible(False)
        self._pins_separator.set_visible(False)

        # Switch sidebar back to server list
        self._sidebar_stack.set_visible_child_name("server_list")

        # Close all editor tabs
        self._editor_panel.close_all()

        # Show server panel (populate first if the user never clicked a group)
        if not self._server_panel_populated:
            self._server_list.select_group("__all__")
        self._content_stack.set_visible_child_name("servers")

        # Disable editor actions
        self.lookup_action("save").set_enabled(False)
        self.lookup_action("close-tab").set_enabled(False)

        # Show server/folder buttons
        self._new_server_btn.set_visible(True)
        self._new_folder_btn.set_visible(True)

    def open_remote_file(self, remote_path):
        """Download and open a remote file for editing."""
        if not self._sftp_client or not self._transfer_queue:
            return

        # Check if already open
        existing = self._editor_panel.find_tab(remote_path)
        if existing is not None:
            self._editor_panel.focus_tab(existing)
            return

        from edith.services.temp_manager import TempManager
        from edith.services.transfer_queue import TransferAborted

        name = os.path.basename(remote_path)
        client = self._sftp_client

        def do_download(progress_cb, cancel_event, set_channel):
            local_path = TempManager.get_temp_path(remote_path)
            client.download(remote_path, str(local_path), progress_cb=progress_cb,
                            cancel_event=cancel_event, set_channel=set_channel)
            mtime = client.stat(remote_path).st_mtime
            return local_path, mtime

        def on_success(result):
            local_path, mtime = result
            self._remote_mtimes[remote_path] = mtime
            self._editor_panel.open_file(remote_path, str(local_path))
            self._content_stack.set_visible_child_name("editor")
            if self._connected_server:
                ConfigService.push_recent(self._connected_server.id, remote_path)

        def on_error(error):
            if isinstance(error, TransferAborted):
                return
            self._set_status("error", _("Download failed: {error}").format(error=error))
            self.show_toast(_("Failed to download: {error}").format(error=error), "error")

        self._transfer_queue.enqueue(name, do_download, on_success, on_error)

    def enqueue_download(self, remote_path, local_path, on_done=None):
        """Queue a download of a remote file to a local path."""
        if not self._sftp_client or not self._transfer_queue:
            return

        from edith.services.transfer_queue import TransferAborted

        name = os.path.basename(remote_path)
        client = self._sftp_client

        def do_download(progress_cb, cancel_event, set_channel):
            client.download_recursive(remote_path, local_path, progress_cb=progress_cb,
                                      cancel_event=cancel_event, set_channel=set_channel)

        def on_success(_):
            if on_done:
                on_done()
            self.show_toast(_("Downloaded {name}").format(name=name), "success")

        def on_error(error):
            if isinstance(error, TransferAborted):
                return
            self.show_toast(_("Download failed: {error}").format(error=error), "error")

        self._transfer_queue.enqueue(name, do_download, on_success, on_error)

    def enqueue_bulk_download(self, items: list, on_done=None):
        """Download multiple files with a single summary notification.

        items: list of (remote_path, local_path) tuples.
        """
        if not self._sftp_client or not self._transfer_queue:
            return

        from edith.services.transfer_queue import TransferAborted

        n = len(items)
        label = ngettext("{n} file", "{n} files", n).format(n=n)
        client = self._sftp_client

        def do_download(progress_cb, cancel_event, set_channel):
            for remote_path, local_path in items:
                client.download_recursive(remote_path, local_path, progress_cb=progress_cb,
                                          cancel_event=cancel_event, set_channel=set_channel)

        def on_success(_):
            if on_done:
                on_done()
            self.show_toast(_("Downloaded {name}").format(name=label), "success")

        def on_error(error):
            if isinstance(error, TransferAborted):
                return
            self.show_toast(_("Download failed: {error}").format(error=error), "error")

        self._transfer_queue.enqueue(label, do_download, on_success, on_error)

    def enqueue_upload(self, local_path, remote_path, on_done=None, overwrite=False):
        """Queue an upload of any local file/directory to a remote path."""
        if not self._sftp_client or not self._transfer_queue:
            return

        name = os.path.basename(remote_path)
        client = self._sftp_client

        def do_upload(progress_cb, cancel_event, set_channel):
            if os.path.isdir(local_path):
                client.upload_directory(local_path, remote_path, overwrite=overwrite)
            else:
                client.upload(local_path, remote_path, progress_cb=progress_cb, overwrite=overwrite)

        self._transfer_queue.enqueue(name, do_upload, on_done, None)

    def watch_external_edit(self, remote_path, local_path):
        """Track a file opened in an external app and upload it when it's saved."""
        self._external_edits.watch(remote_path, local_path, self._on_external_edit_saved)

    def _on_external_edit_saved(self, remote_path, local_path):
        """A locally-opened file was written by its external app — upload it."""
        if not self._sftp_client or not self._transfer_queue:
            return

        name = os.path.basename(remote_path)
        client = self._sftp_client
        # Suppress the mtime poller so our own upload doesn't look like a
        # remote change if the file is also open in an editor tab.
        self._saving_paths.add(remote_path)

        def do_upload(progress_cb, cancel_event, set_channel):
            client.upload(local_path, remote_path, progress_cb=progress_cb, overwrite=True)
            return client.stat(remote_path).st_mtime

        def on_success(mtime):
            # Record the post-upload mtime before clearing the suppression
            # flag, so the poller never sees our own write as a remote change.
            if remote_path in self._remote_mtimes:
                self._remote_mtimes[remote_path] = mtime
            self._saving_paths.discard(remote_path)
            self.show_toast(_("Uploaded {name}").format(name=name), "success")
            self._file_browser.refresh_path(os.path.dirname(remote_path))
            # Keep an editor tab on the same file in sync with the external edit.
            viewer = self._viewer_for_path(remote_path)
            if viewer and not viewer.open_file.is_modified:
                self._redownload_and_reload(remote_path)

        def on_error(error):
            self._saving_paths.discard(remote_path)
            self.show_toast(_("Failed to upload {name}: {error}").format(name=name, error=error), "error")

        self._transfer_queue.enqueue(name, do_upload, on_success, on_error)

    def save_remote_file(self, remote_path, local_path):
        """Queue an upload of a saved local file back to the server."""
        if not self._sftp_client or not self._transfer_queue:
            return

        name = os.path.basename(remote_path)
        client = self._sftp_client
        self._saving_paths.add(remote_path)

        def do_upload(progress_cb, cancel_event, set_channel):
            client.upload(local_path, remote_path, progress_cb=progress_cb, overwrite=True)
            return client.stat(remote_path).st_mtime

        def on_success(mtime):
            self._remote_mtimes[remote_path] = mtime
            self._saving_paths.discard(remote_path)
            self.show_toast(_("Saved {name}").format(name=name), "success")

        def on_error(error):
            self._saving_paths.discard(remote_path)
            dialog = Adw.AlertDialog(
                heading=_("Upload Failed"),
                body=str(error),
            )
            dialog.add_response("ok", _("OK"))
            dialog.present(self)

        self._transfer_queue.enqueue(name, do_upload, on_success, on_error)

    # --- Remote file-change polling ---

    def _poll_remote_mtimes(self):
        """Periodically check remote mtimes for open, unmodified files."""
        if not self._sftp_client or self._poll_in_flight:
            return True  # keep timer alive

        # Collect paths to check (skip files being saved)
        paths_to_check = {}
        for remote_path, known_mtime in self._remote_mtimes.items():
            if remote_path in self._saving_paths:
                continue
            if remote_path in self._reload_dialog_paths:
                continue
            if not self._editor_panel.find_tab(remote_path):
                continue
            paths_to_check[remote_path] = known_mtime

        if not paths_to_check:
            return True

        self._poll_in_flight = True
        client = self._sftp_client

        from edith.services.async_worker import run_async

        def do_stat():
            changed = []
            for rpath, old_mtime in paths_to_check.items():
                try:
                    new_mtime = client.stat(rpath).st_mtime
                    if new_mtime != old_mtime:
                        changed.append((rpath, new_mtime))
                except OSError:
                    pass
            return changed

        def on_stat_done(changed):
            self._poll_in_flight = False
            for rpath, new_mtime in changed:
                # A save may have started *after* paths_to_check was collected;
                # its own upload bumps the remote mtime, which would otherwise
                # look like a remote change and trigger a pointless reload.
                if rpath in self._saving_paths:
                    continue
                if rpath in self._reload_dialog_paths:
                    continue
                self._remote_mtimes[rpath] = new_mtime
                viewer = self._viewer_for_path(rpath)
                if not viewer:
                    continue
                if viewer.open_file.is_modified:
                    self._confirm_remote_reload(rpath)
                else:
                    self._redownload_and_reload(rpath)

        def on_stat_error(_):
            self._poll_in_flight = False

        run_async(do_stat, on_stat_done, on_stat_error)
        return True  # keep timer alive

    def _editor_for_path(self, remote_path):
        """Return the MonacoEditor widget for a given remote path, or None."""
        from edith.widgets.monaco_editor import MonacoEditor
        page = self._editor_panel._tabs.get(remote_path)
        if page:
            widget = page.get_child()
            if isinstance(widget, MonacoEditor):
                return widget
        return None

    def _viewer_for_path(self, remote_path):
        """Return the tab widget for a path — editor or image viewer."""
        page = self._editor_panel._tabs.get(remote_path)
        if page:
            widget = page.get_child()
            if hasattr(widget, "reload_from_disk"):
                return widget
        return None

    def _redownload_and_reload(self, remote_path):
        """Re-download a remote file and reload its tab content."""
        viewer = self._viewer_for_path(remote_path)
        if not viewer or not self._sftp_client:
            return

        client = self._sftp_client
        local_path = viewer.open_file.local_path

        from edith.services.async_worker import run_async

        def read_local():
            try:
                with open(local_path, "rb") as f:
                    return f.read()
            except OSError:
                return None

        def do_download():
            before = read_local()
            client.download(remote_path, local_path)
            # An mtime bump without a content change (touch, rsync, our own
            # round-trip) must not disturb the open tab at all.
            return before is None or before != read_local()

        def on_done(changed):
            if not changed:
                return
            v = self._viewer_for_path(remote_path)
            if v:
                v.reload_from_disk()

        run_async(do_download, on_done, lambda _: None)

    def _confirm_remote_reload(self, remote_path):
        """Ask the user whether to reload a file that changed remotely while
        the editor has unsaved local edits."""
        editor = self._editor_for_path(remote_path)
        if not editor:
            return
        filename = editor.open_file.filename

        dialog = Adw.AlertDialog(
            heading=_("File Changed on Server"),
            body=_(
                "\u201c{filename}\u201d has been modified on the server.\n"
                "Do you want to reload it? Your unsaved changes will be lost."
            ).format(filename=filename),
        )
        dialog.add_response("keep", _("Keep Local"))
        dialog.add_response("reload", _("Reload"))
        dialog.set_response_appearance("reload", Adw.ResponseAppearance.DESTRUCTIVE)

        self._reload_dialog_paths.add(remote_path)
        dialog.connect("response", self._on_reload_response, remote_path)
        dialog.present(self)

    def _on_reload_response(self, dialog, response, remote_path):
        self._reload_dialog_paths.discard(remote_path)
        if response == "reload":
            self._redownload_and_reload(remote_path)

    # --- Transfer queue signal handlers ---

    def _on_xfer_queued(self, queue, label, job_id):
        self._transfer_btn.set_sensitive(True)

    def _on_xfer_started(self, queue, label, job_id, pending):
        self._status_bar.show_transfer(label, 0.0, pending)

    def _on_xfer_progress(self, queue, label, fraction, pending):
        self._status_bar.show_transfer(label, fraction, pending)

    def _on_xfer_done(self, queue, label):
        self._status_bar.clear_transfer()

    def _on_xfer_failed(self, queue, label, msg):
        self._status_bar.clear_transfer()

    def _on_xfer_idle(self, queue):
        """All transfers finished — restore normal connected status."""
        self._status_bar.clear_transfer()
        self._transfer_btn.set_sensitive(False)

    def _set_status(self, state, message):
        """Update the status bar and sidebar connection indicator."""
        if self._status_bar:
            self._status_bar.set_status(state, message)
        _app_icons = {
            "disconnected": "edith-disconnected-symbolic",
            "connected":    "edith-connected-symbolic",
        }
        _sys_icons = {
            "connecting":  "network-transmit-symbolic",
            "error":       "dialog-error-symbolic",
        }
        icon = _app_icons.get(state) or _sys_icons.get(state, "network-offline-symbolic")
        self._sidebar_status_icon.set_from_icon_name(icon)
        self._sidebar_status_label.set_label(message)
        if state == "connected":
            self._sidebar_status_label.remove_css_class("dim-label")
            self._sidebar_status_label.remove_css_class("success")
        else:
            self._sidebar_status_label.remove_css_class("success")
            self._sidebar_status_label.add_css_class("dim-label")

        # The details popover is only meaningful while connected.
        connected = state == "connected"
        self._sidebar_status_btn.set_sensitive(connected)
        if connected and self._connected_server:
            self._populate_connection_popover(self._connected_server)
        else:
            self._sidebar_status_popover.set_child(None)

    def _populate_connection_popover(self, server_info):
        """Fill the sidebar status popover with connection details."""
        if server_info.protocol == "ftp":
            _enc = {
                "none":               "FTP",
                "explicit_optional":  "FTP (FTPS, explicit)",
                "explicit_required":  "FTP (FTPS, explicit, required)",
                "implicit":           "FTP (FTPS, implicit)",
            }
            protocol_text = _enc.get(server_info.ftp_encryption, "FTP")
        else:
            protocol_text = "SFTP"

        auth_text = {
            "password":        "Password",
            "key":             "SSH key",
            "key+passphrase":  "SSH key + passphrase",
        }.get(server_info.auth_method, server_info.auth_method)

        rows = [
            ("Host", server_info.host),
            ("Username", server_info.username),
            ("Port", str(server_info.port)),
            ("Protocol", protocol_text),
            ("Auth", auth_text),
            ("Path", server_info.initial_directory or "/"),
        ]

        grid = Gtk.Grid(
            row_spacing=4,
            column_spacing=16,
            margin_top=8,
            margin_bottom=8,
            margin_start=10,
            margin_end=10,
        )
        title = Gtk.Label(
            label=server_info.display_name,
            xalign=0,
            ellipsize=3,
            css_classes=["heading"],
        )
        grid.attach(title, 0, 0, 2, 1)

        for i, (key, value) in enumerate(rows, start=1):
            key_lbl = Gtk.Label(label=key, xalign=0, css_classes=["dim-label", "caption"])
            val_lbl = Gtk.Label(
                label=value or "—",
                xalign=0,
                selectable=True,
                ellipsize=3,
                max_width_chars=32,
            )
            grid.attach(key_lbl, 0, i, 1, 1)
            grid.attach(val_lbl, 1, i, 1, 1)

        self._sidebar_status_popover.set_child(grid)

    def adjust_sidebar_width(self, width: int | None = None):
        """Set the paned position without changing the user's saved width.

        Used by the detail-mode toggle. `None` restores the saved width.
        """
        if width is None:
            width = self._sidebar_width
        # Programmatic resizes (and the allocation churn they cause) must not
        # overwrite the width the user chose by dragging.
        self._cancel_sidebar_width_save()
        if self._sidebar_width_suppress:
            GLib.source_remove(self._sidebar_width_suppress)
        self._sidebar_width_suppress = GLib.timeout_add(
            600, self._resume_sidebar_width_save
        )
        if self._sidebar_visible:
            self._paned.set_position(width)

    def _resume_sidebar_width_save(self):
        self._sidebar_width_suppress = None
        return GLib.SOURCE_REMOVE

    def _cancel_sidebar_width_save(self):
        if self._sidebar_width_timer:
            GLib.source_remove(self._sidebar_width_timer)
            self._sidebar_width_timer = None

    def _on_sidebar_position_changed(self, paned, pspec):
        """Debounce saving so a drag writes the config once, not per pixel."""
        if self._sidebar_width_suppress or not self._sidebar_visible:
            return
        self._cancel_sidebar_width_save()
        self._sidebar_width_timer = GLib.timeout_add(400, self._save_sidebar_width)

    def _save_sidebar_width(self):
        self._sidebar_width_timer = None
        position = self._paned.get_position()
        if position >= 180 and position != self._sidebar_width:
            self._sidebar_width = position
            ConfigService.set_preference("sidebar_width", position)
        return GLib.SOURCE_REMOVE

    def reveal_in_sidebar(self, remote_path: str):
        """Show the file in the sidebar file browser."""
        self._sidebar_toolbar.set_visible(True)
        self._paned.set_position(self._sidebar_width)
        self._sidebar_visible = True
        self._main_header.set_show_start_title_buttons(False)
        self._sidebar_stack.set_visible_child_name("file_browser")
        self._file_browser.reveal_file(remote_path)

    def apply_syntax_scheme(self, scheme_id: str):
        """Apply a syntax colour scheme to all open editor tabs."""
        self._editor_panel.apply_syntax_scheme(scheme_id)

    def apply_editor_font(self, font_family: str, font_size: int):
        """Apply a font to all open editor tabs."""
        self._editor_panel.apply_font(font_family, font_size)

    def apply_navigation_settings(self):
        """Re-read the single/double click behaviour from config."""
        self._file_browser.apply_navigation_settings()
        self._server_panel.apply_navigation_settings()

    def apply_editor_settings(self):
        """Re-read global editor settings from config and push to all open tabs."""
        from edith.services.config import ConfigService
        settings = {
            "minimap":          ConfigService.get_preference("editor_minimap", False),
            "renderWhitespace": ConfigService.get_preference("editor_render_whitespace", "selection"),
            "stickyScroll":     ConfigService.get_preference("editor_sticky_scroll", False),
            "fontLigatures":    ConfigService.get_preference("editor_font_ligatures", False),
            "lineNumbers":      ConfigService.get_preference("editor_line_numbers", "on"),
            "customOptions":    ConfigService.get_preference("editor_overrides", {}),
        }
        self._editor_panel.apply_editor_settings(settings)
        # Refresh the indent display in the status bar too
        insert_spaces = ConfigService.get_preference("editor_insert_spaces", True)
        tab_size = ConfigService.get_preference("editor_tab_size", 4)
        self._status_bar.set_indent(insert_spaces, tab_size)

    def show_toast(self, title: str, kind: str = "info", timeout: int = 3):
        """Show a transient toast. kind: 'info', 'success', 'error'."""
        _icons = {"error": ("dialog-error-symbolic", "toast-error-icon"),
                  "success": ("emblem-ok-symbolic", "toast-success-icon")}
        toast = Adw.Toast(timeout=timeout)
        if kind in _icons:
            icon_name, css_class = _icons[kind]
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=8, valign=Gtk.Align.CENTER)
            box.append(Gtk.Image(icon_name=icon_name, pixel_size=16,
                                 css_classes=[css_class]))
            box.append(Gtk.Label(label=title))
            toast.set_custom_title(box)
        else:
            toast.set_title(title)
        self._toast_overlay.add_toast(toast)

    @property
    def sftp_client(self):
        return self._sftp_client

    @property
    def connected_server(self):
        """The ServerInfo this window is connected to, or None."""
        return self._connected_server

    @property
    def editor_panel(self):
        """The tab view holding this window's editors."""
        return self._editor_panel
