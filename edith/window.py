import os
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

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


class EdithWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        w = ConfigService.get_preference("window_width", 1100)
        h = ConfigService.get_preference("window_height", 700)
        super().__init__(
            default_width=w,
            default_height=h,
            title="Edith",
            **kwargs,
        )

        self._sftp_client = None
        self._connected_server = None
        self._transfer_queue = None
        self._force_close = False
        self._server_panel_populated = False

        self._build_ui()
        self._setup_actions()

    def _build_ui(self):
        # === Sidebar ToolbarView (has its own HeaderBar with window controls) ===
        sidebar_header = Adw.HeaderBar(show_end_title_buttons=False)
        sidebar_header.set_title_widget(Gtk.Label(label="Edith", css_classes=["title"]))

        self._new_server_btn = Gtk.Button(
            icon_name="edith-server-add-symbolic",
            tooltip_text="Add Server (Ctrl+N)",
        )
        self._new_server_btn.connect("clicked", lambda _: self._on_new_server(None, None))
        sidebar_header.pack_start(self._new_server_btn)

        self._new_folder_btn = Gtk.Button(
            icon_name="edith-group-new-symbolic",
            tooltip_text="New Server Group",
        )
        self._new_folder_btn.connect("clicked", lambda _: self._server_list.show_new_folder_dialog())
        sidebar_header.pack_start(self._new_folder_btn)

        self._sidebar_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            vexpand=True,
        )

        self._server_list = ServerList()
        self._server_list.connect("group-selected", self._on_group_selected)
        self._sidebar_stack.add_named(self._server_list, "server_list")

        self._file_browser = FileBrowser()
        self._file_browser.set_window(self)
        self._file_browser.connect("file-activated", self._on_file_activated)
        self._file_browser.connect("path-changed", self._on_path_changed)
        self._sidebar_stack.add_named(self._file_browser, "file_browser")

        self._sidebar_stack.set_visible_child_name("server_list")

        self._sidebar_toolbar = Adw.ToolbarView()
        self._sidebar_toolbar.add_css_class("app-sidebar")
        self._sidebar_toolbar.set_size_request(180, -1)
        self._sidebar_toolbar.add_top_bar(sidebar_header)
        self._sidebar_toolbar.set_content(self._sidebar_stack)

        # Sidebar status bar (connection indicator at the sidebar bottom)
        sidebar_status_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            margin_start=12,
            margin_end=12,
            margin_top=6,
            margin_bottom=6,
        )
        sidebar_status_bar.add_css_class("toolbar")
        self._sidebar_status_icon = Gtk.Image.new_from_icon_name("edith-disconnected-symbolic")
        self._sidebar_status_icon.set_pixel_size(16)
        sidebar_status_bar.append(self._sidebar_status_icon)
        self._sidebar_status_label = Gtk.Label(
            label="Disconnected",
            xalign=0,
            hexpand=True,
            ellipsize=3,
            css_classes=["dim-label", "caption"],
        )
        sidebar_status_bar.append(self._sidebar_status_label)
        self._sidebar_toolbar.add_bottom_bar(sidebar_status_bar)

        # === Main ToolbarView (no window controls — they live in the sidebar header) ===
        self._main_header = Adw.HeaderBar(
            show_start_title_buttons=False,
            show_end_title_buttons=False,
        )

        self._connect_btn = Gtk.Button(
            icon_name="edith-connect-symbolic",
            tooltip_text="Connect",
            sensitive=False,
        )
        self._connect_btn.connect("clicked", self._on_connect_btn_clicked)
        self._main_header.pack_start(self._connect_btn)

        self._back_btn = Gtk.Button(
            icon_name="edith-back-symbolic",
            tooltip_text="Back",
            visible=False,
            sensitive=False,
        )
        self._back_btn.connect("clicked", lambda _: self._file_browser.go_back())
        self._main_header.pack_start(self._back_btn)

        self._forward_btn = Gtk.Button(
            icon_name="edith-forward-symbolic",
            tooltip_text="Forward",
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
        window_section.append("New Window", "app.new-window")
        menu.append_section(None, window_section)
        prefs_section = Gio.Menu()
        prefs_section.append("Syntax Theme\u2026", "app.syntax-theme")
        prefs_section.append("Syntax Associations\u2026", "app.syntax-associations")
        prefs_section.append("Editor Font\u2026", "app.editor-font")
        prefs_section.append("Editor Settings\u2026", "app.editor-settings")
        prefs_section.append("Window Size\u2026", "app.window-size")
        prefs_section.append("Keyboard Shortcuts", "app.shortcuts")
        prefs_section.append("About Edith", "app.about")
        menu.append_section(None, prefs_section)

        self._transfer_panel = TransferPanel()
        self._transfer_btn = Gtk.MenuButton(
            icon_name="edith-transfers-symbolic",
            popover=self._transfer_panel,
            tooltip_text="Transfers",
            visible=False,
            sensitive=False,
        )
        self._main_header.pack_end(self._transfer_btn)

        self._sidebar_visible = True
        self._sidebar_toggle = Gtk.Button(
            icon_name="edith-sidebar-symbolic",
            tooltip_text="Toggle Sidebar (F9)",
            focusable=False,
        )
        self._sidebar_toggle.connect("clicked", self._on_sidebar_toggled)
        self._main_header.pack_end(self._sidebar_toggle)

        menu_btn = Gtk.MenuButton(
            icon_name="edith-open-menu-symbolic",
            menu_model=menu,
            tooltip_text="Main Menu",
        )
        self._main_header.pack_end(menu_btn)

        # Content stack
        self._content_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            vexpand=True,
        )

        self._welcome_view = WelcomeView()
        self._content_stack.add_named(self._welcome_view, "welcome")

        self._server_panel = ServerPanel()
        self._server_panel.connect("server-activated", self._on_server_activated)
        self._server_panel.connect("selection-changed", self._on_server_selection_changed)
        self._server_panel.connect("servers-changed", lambda *_: self._server_list.load_servers())
        self._content_stack.add_named(self._server_panel, "servers")

        self._connected_page = Adw.StatusPage(
            icon_name="edith-connect-symbolic",
            title="",
            description="Open a file from the sidebar to start editing.",
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

        self._status_bar = StatusBar()
        self._status_bar.connect("language-selected", self._on_language_selected)
        self._status_bar.connect("indent-changed", self._on_indent_changed)
        self._status_bar.connect("line-ending-changed", self._on_line_ending_changed)
        self._status_bar.hide_connection_status()

        main_toolbar = Adw.ToolbarView()
        main_toolbar.add_top_bar(self._main_header)
        main_toolbar.add_bottom_bar(self._status_bar)
        main_toolbar.set_content(self._toast_overlay)

        # === Resizable paned: sidebar | main ===
        self._paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._sidebar_width = 260

        self._paned.set_start_child(self._sidebar_toolbar)
        self._paned.set_resize_start_child(False)
        self._paned.set_shrink_start_child(False)
        self._paned.set_end_child(main_toolbar)
        self._paned.set_resize_end_child(True)
        self._paned.set_shrink_end_child(False)
        self._paned.set_position(self._sidebar_width)

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

    # --- Signal handlers ---

    def _on_close_request(self, window):
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
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("quit", "Quit Anyway")
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
            self._sidebar_width = max(self._paned.get_position(), 180)
            self._sidebar_toolbar.set_visible(False)
            self._sidebar_visible = False
            # Window controls move to the main header when sidebar is hidden
            self._main_header.set_show_start_title_buttons(True)
        else:
            self._sidebar_toolbar.set_visible(True)
            self._paned.set_position(self._sidebar_width)
            self._sidebar_visible = True
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

    def _on_new_server(self, action, param):
        self._server_panel.show_add_dialog()

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

    def _on_goto_line(self, action, param):
        editor = self._editor_panel.get_current_editor()
        if not editor:
            return

        dialog = Adw.AlertDialog(heading="Go to Line", body="")
        entry = Gtk.Entry(
            input_purpose=Gtk.InputPurpose.DIGITS,
            placeholder_text="Line number…",
            activates_default=True,
            width_chars=12,
        )
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("go", "Go")
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
        # Try stored credential first
        stored = credential_store.get_password(server_info.id)

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
        from edith.services.sftp_client import SftpClient
        from edith.services.async_worker import run_async

        self._set_status("connecting", f"Connecting to {server_info.host}...")

        initial_dir = server_info.initial_directory or "/"

        def do_connect():
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
            return client, resolved

        def on_success(result):
            client, resolved_dir = result
            self._sftp_client = client
            self._connected_server = server_info
            self._on_connected(server_info, resolved_dir)

        def on_error(error):
            self._set_status("error", f"Connection failed: {error}")
            dialog = Adw.AlertDialog(
                heading="Connection Failed",
                body=str(error),
            )
            dialog.add_response("ok", "OK")
            dialog.present(self)

        run_async(do_connect, on_success, on_error)

    def disconnect_server(self):
        """Disconnect from current server, confirming if there are unsaved changes."""
        if self._editor_panel.has_unsaved():
            names = self._editor_panel.unsaved_filenames()
            body = "Unsaved changes in: " + ", ".join(names)
            dialog = Adw.AlertDialog(
                heading="Disconnect with unsaved changes?",
                body=body,
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("discard", "Discard & Disconnect")
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
        self._connect_btn.set_tooltip_text("Disconnect (Ctrl+D)")

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

        # Show connected placeholder until the user opens a file
        self._connected_page.set_title(f"Connected to {server_info.display_name}")
        self._content_stack.set_visible_child_name("connected")

        toast = Adw.Toast(title=f"Connected to {server_info.display_name}")
        self._toast_overlay.add_toast(toast)

    def _on_editor_page_changed(self, panel):
        from edith.services.config import ConfigService
        editor = panel.get_current_editor()
        if editor:
            self._status_bar.set_language_name(editor.get_language_name())
            insert_spaces = ConfigService.get_preference("editor_insert_spaces", True)
            tab_size = ConfigService.get_preference("editor_tab_size", 4)
            self._status_bar.set_indent(insert_spaces, tab_size)
            self._status_bar.set_line_ending(editor.get_line_ending())
        else:
            self._status_bar.hide_file_info()

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

    def _on_disconnected(self):
        """Called after disconnection."""
        self._status_bar.clear_transfer()
        self._set_status("disconnected", "Disconnected")
        self._status_bar.hide_file_info()
        self._header_stack.set_visible_child_name("title")
        self._back_btn.set_visible(False)
        self._forward_btn.set_visible(False)
        self._back_btn.set_sensitive(False)
        self._forward_btn.set_sensitive(False)
        self._file_browser.reset_history()
        self.lookup_action("disconnect").set_enabled(False)
        self._connect_btn.set_icon_name("edith-connect-symbolic")
        self._connect_btn.set_tooltip_text("Connect")
        self._connect_btn.set_sensitive(self._server_panel.get_selected_server() is not None)

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

        def do_download(progress_cb):
            local_path = TempManager.get_temp_path(remote_path)
            client.download(remote_path, str(local_path), progress_cb=progress_cb)
            return local_path

        def on_success(local_path):
            self._editor_panel.open_file(remote_path, str(local_path))
            self._content_stack.set_visible_child_name("editor")

        def on_error(error):
            if isinstance(error, TransferAborted):
                return
            self._set_status("error", f"Download failed: {error}")
            toast = Adw.Toast(title=f"Failed to download: {error}")
            self._toast_overlay.add_toast(toast)

        self._transfer_queue.enqueue(name, do_download, on_success, on_error)

    def enqueue_download(self, remote_path, local_path, on_done=None):
        """Queue a download of a remote file to a local path."""
        if not self._sftp_client or not self._transfer_queue:
            return

        from edith.services.transfer_queue import TransferAborted

        name = os.path.basename(remote_path)
        client = self._sftp_client

        def do_download(progress_cb):
            client.download(remote_path, local_path, progress_cb=progress_cb)

        def on_success(_):
            if on_done:
                on_done()
            toast = Adw.Toast(title=f"Downloaded {name}")
            self._toast_overlay.add_toast(toast)

        def on_error(error):
            if isinstance(error, TransferAborted):
                return
            toast = Adw.Toast(title=f"Download failed: {error}")
            self._toast_overlay.add_toast(toast)

        self._transfer_queue.enqueue(name, do_download, on_success, on_error)

    def enqueue_upload(self, local_path, remote_path, on_done=None):
        """Queue an upload of any local file/directory to a remote path."""
        if not self._sftp_client or not self._transfer_queue:
            return

        name = os.path.basename(remote_path)
        client = self._sftp_client

        def do_upload(progress_cb):
            if os.path.isdir(local_path):
                client.upload_directory(local_path, remote_path)
            else:
                client.upload(local_path, remote_path, progress_cb=progress_cb, overwrite=True)

        self._transfer_queue.enqueue(name, do_upload, on_done, None)

    def save_remote_file(self, remote_path, local_path):
        """Queue an upload of a saved local file back to the server."""
        if not self._sftp_client or not self._transfer_queue:
            return

        name = os.path.basename(remote_path)
        client = self._sftp_client

        def do_upload(progress_cb):
            client.upload(local_path, remote_path, progress_cb=progress_cb, overwrite=True)

        def on_success(_):
            toast = Adw.Toast(title=f"Saved {name}")
            self._toast_overlay.add_toast(toast)

        def on_error(error):
            dialog = Adw.AlertDialog(
                heading="Upload Failed",
                body=str(error),
            )
            dialog.add_response("ok", "OK")
            dialog.present(self)

        self._transfer_queue.enqueue(name, do_upload, on_success, on_error)

    # --- Transfer queue signal handlers ---

    def _on_xfer_queued(self, queue, label, job_id):
        self._transfer_btn.set_sensitive(True)

    def _on_xfer_started(self, queue, label, job_id, pending):
        self._status_bar.show_transfer(label, 0.0, pending)

    def _on_xfer_progress(self, queue, label, fraction, pending):
        self._status_bar.show_transfer(label, fraction, pending)

    def _on_xfer_done(self, queue, label):
        pass  # _on_xfer_idle restores the status bar once the queue drains

    def _on_xfer_failed(self, queue, label, msg):
        pass  # on_error callback already shows a dialog

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

    @property
    def sftp_client(self):
        return self._sftp_client
