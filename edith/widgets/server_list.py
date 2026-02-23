import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk, GObject, Gdk

from edith.models.server import ServerInfo, FolderInfo
from edith.services.config import ConfigService
from edith.services import credential_store
from edith.widgets.server_row import ServerRow
from edith.widgets.folder_row import FolderRow
from edith.widgets.server_edit_dialog import ServerEditDialog
from edith.widgets.file_dialogs import NameDialog


class ServerList(Gtk.Box):
    """Sidebar widget showing saved servers grouped in folders."""

    __gsignals__ = {
        "server-activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._servers = []
        self._folders = []
        self._rows = {}
        self._folder_rows = {}
        self._search_query = ""

        # Search bar
        self._search_entry = Gtk.SearchEntry(placeholder_text="Search servers\u2026")
        self._search_entry.connect("search-changed", self._on_search_changed)
        self._search_entry.connect("stop-search", self._on_search_stop)

        self._search_bar = Gtk.SearchBar(
            child=self._search_entry,
            show_close_button=True,
        )
        self._search_bar.connect("notify::search-mode-enabled", self._on_search_mode_changed)
        self.append(self._search_bar)

        # Scrolled list
        sw = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )

        self._list_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            css_classes=["navigation-sidebar"],
        )
        self._list_box.connect("row-activated", self._on_row_activated)
        self._list_box.connect("row-selected", self._on_row_selected)

        dbl_click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        dbl_click.connect("pressed", self._on_list_double_click)
        self._list_box.add_controller(dbl_click)

        sw.set_child(self._list_box)
        self.append(sw)

        # Context menus
        self._setup_context_menus()

        # Drop target on the list box for ungrouping servers
        self._setup_listbox_drop_target()

        self.load_servers()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def toggle_search(self):
        active = self._search_bar.get_search_mode()
        self._search_bar.set_search_mode(not active)
        if not active:
            self._search_entry.grab_focus()

    def _on_search_changed(self, entry):
        self._search_query = entry.get_text().strip().lower()
        self._rebuild_list()

    def _on_search_stop(self, entry):
        self._search_bar.set_search_mode(False)

    def _on_search_mode_changed(self, bar, pspec):
        if not self._search_bar.get_search_mode():
            self._search_entry.set_text("")
            self._search_query = ""
            self._rebuild_list()

    # ------------------------------------------------------------------
    # Context menus
    # ------------------------------------------------------------------

    def _setup_context_menus(self):
        """Three context menus: server, folder, empty area."""
        # --- Server context menu ---
        server_menu = Gio.Menu()
        server_menu.append("Connect", "server.connect")
        server_menu.append("Edit", "server.edit")
        server_menu.append("Change Password\u2026", "server.change-password")
        server_menu.append("Remove from Group", "server.ungroup")
        server_menu.append("Delete", "server.delete")

        self._server_menu = Gtk.PopoverMenu(menu_model=server_menu, has_arrow=False)
        self._server_menu.set_parent(self._list_box)

        server_group = Gio.SimpleActionGroup()

        connect_action = Gio.SimpleAction.new("connect", None)
        connect_action.connect("activate", self._on_context_connect)
        server_group.add_action(connect_action)

        edit_action = Gio.SimpleAction.new("edit", None)
        edit_action.connect("activate", self._on_context_edit)
        server_group.add_action(edit_action)

        delete_action = Gio.SimpleAction.new("delete", None)
        delete_action.connect("activate", self._on_context_delete)
        server_group.add_action(delete_action)

        self._ungroup_action = Gio.SimpleAction.new("ungroup", None)
        self._ungroup_action.connect("activate", self._on_context_ungroup)
        server_group.add_action(self._ungroup_action)

        self._change_password_action = Gio.SimpleAction.new("change-password", None)
        self._change_password_action.connect("activate", self._on_context_change_password)
        server_group.add_action(self._change_password_action)

        self._list_box.insert_action_group("server", server_group)

        # --- Folder context menu ---
        folder_menu = Gio.Menu()
        folder_menu.append("Rename Group\u2026", "folder.rename")
        folder_menu.append("Delete Group", "folder.delete-folder")

        self._folder_menu = Gtk.PopoverMenu(menu_model=folder_menu, has_arrow=False)
        self._folder_menu.set_parent(self._list_box)

        folder_group = Gio.SimpleActionGroup()

        rename_action = Gio.SimpleAction.new("rename", None)
        rename_action.connect("activate", self._on_folder_rename)
        folder_group.add_action(rename_action)

        delete_folder_action = Gio.SimpleAction.new("delete-folder", None)
        delete_folder_action.connect("activate", self._on_folder_delete)
        folder_group.add_action(delete_folder_action)

        self._list_box.insert_action_group("folder", folder_group)

        # --- Empty area context menu ---
        empty_menu = Gio.Menu()
        empty_menu.append("New Server Group\u2026", "area.new-folder")

        self._empty_menu = Gtk.PopoverMenu(menu_model=empty_menu, has_arrow=False)
        self._empty_menu.set_parent(self._list_box)

        area_group = Gio.SimpleActionGroup()

        new_folder_action = Gio.SimpleAction.new("new-folder", None)
        new_folder_action.connect("activate", lambda *_: self.show_new_folder_dialog())
        area_group.add_action(new_folder_action)

        self._list_box.insert_action_group("area", area_group)

        # Right-click gesture
        gesture = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        gesture.connect("pressed", self._on_right_click)
        self._list_box.add_controller(gesture)

        self._context_row = None

    def _on_right_click(self, gesture, n_press, x, y):
        row = self._list_box.get_row_at_y(int(y))
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1

        if row is None:
            # Empty area
            self._empty_menu.set_pointing_to(rect)
            self._empty_menu.popup()
            return

        self._context_row = row
        child = row.get_child()

        if isinstance(child, FolderRow):
            self._folder_menu.set_pointing_to(rect)
            self._folder_menu.popup()
        elif isinstance(child, ServerRow):
            # Enable/disable actions based on server state
            self._ungroup_action.set_enabled(child.server_info.folder_id != "")
            self._change_password_action.set_enabled(child.server_info.auth_method != "key")
            self._server_menu.set_pointing_to(rect)
            self._server_menu.popup()

    # ------------------------------------------------------------------
    # Context menu actions — server
    # ------------------------------------------------------------------

    def _on_context_connect(self, action, param):
        if self._context_row:
            self._on_row_activated(self._list_box, self._context_row)

    def _on_context_edit(self, action, param):
        if self._context_row:
            child = self._context_row.get_child()
            if isinstance(child, ServerRow):
                self._show_edit_dialog(child.server_info)

    def _on_context_delete(self, action, param):
        if self._context_row:
            child = self._context_row.get_child()
            if isinstance(child, ServerRow):
                self._confirm_delete(child.server_info)

    def _on_context_ungroup(self, action, param):
        if self._context_row:
            child = self._context_row.get_child()
            if isinstance(child, ServerRow):
                child.server_info.folder_id = ""
                ConfigService.update_server(child.server_info)
                self._servers = ConfigService.load_servers()
                self._rebuild_list()

    def _on_context_change_password(self, action, param):
        if self._context_row:
            child = self._context_row.get_child()
            if isinstance(child, ServerRow):
                self._show_change_password_dialog(child.server_info)

    def _show_change_password_dialog(self, server_info):
        dialog = Adw.Dialog(
            title="Change Password",
            content_width=360,
            content_height=180,
        )

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _: dialog.close())
        header.pack_start(cancel_btn)

        save_btn = Gtk.Button(label="Save", css_classes=["suggested-action"])
        header.pack_end(save_btn)
        toolbar_view.add_top_bar(header)

        clamp = Adw.Clamp(maximum_size=360, margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)

        group = Adw.PreferencesGroup()
        label = "Key Passphrase" if server_info.auth_method == "key+passphrase" else "Password"
        pw_row = Adw.PasswordEntryRow(title=label)
        group.add(pw_row)

        clamp.set_child(group)
        toolbar_view.set_content(clamp)
        dialog.set_child(toolbar_view)

        def on_save(_):
            password = pw_row.get_text()
            if password:
                credential_store.store_password(server_info.id, password)
            dialog.close()

        save_btn.connect("clicked", on_save)
        pw_row.connect("entry-activated", on_save)

        dialog.present(self.get_root())

    # ------------------------------------------------------------------
    # Context menu actions — folder
    # ------------------------------------------------------------------

    def _on_folder_rename(self, action, param):
        if not self._context_row:
            return
        child = self._context_row.get_child()
        if not isinstance(child, FolderRow):
            return
        folder = child.folder_info
        win = self.get_root()
        dialog = NameDialog("Rename Group", "Group name", folder.name)
        dialog.connect("submitted", self._on_folder_rename_submitted, folder)
        dialog.present(win)

    def _on_folder_rename_submitted(self, dialog, new_name, folder):
        folder.name = new_name
        self._folders = ConfigService.update_folder(folder)
        self._rebuild_list()

    def _on_folder_delete(self, action, param):
        if not self._context_row:
            return
        child = self._context_row.get_child()
        if not isinstance(child, FolderRow):
            return
        folder = child.folder_info
        win = self.get_root()
        dlg = Adw.AlertDialog(
            heading="Delete Group?",
            body=f"Delete \u201c{folder.name}\u201d? Servers inside will become ungrouped.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", self._on_folder_delete_response, folder)
        dlg.present(win)

    def _on_folder_delete_response(self, dialog, response, folder):
        if response == "delete":
            # Ungroup member servers
            for s in self._servers:
                if s.folder_id == folder.id:
                    s.folder_id = ""
            ConfigService.save_servers(self._servers)
            self._folders = ConfigService.delete_folder(folder.id)
            self._servers = ConfigService.load_servers()
            self._rebuild_list()

    # ------------------------------------------------------------------
    # New folder dialog
    # ------------------------------------------------------------------

    def show_new_folder_dialog(self):
        win = self.get_root()
        dialog = NameDialog("New Server Group", "Group name", "")
        dialog.connect("submitted", self._on_new_folder_submitted)
        dialog.present(win)

    def _on_new_folder_submitted(self, dialog, name):
        folder = FolderInfo(name=name)
        self._folders = ConfigService.add_folder(folder)
        self._rebuild_list()

    # ------------------------------------------------------------------
    # Folder toggle (expand/collapse)
    # ------------------------------------------------------------------

    def _on_folder_toggled(self, folder_row, expanded):
        ConfigService.update_folder(folder_row.folder_info)
        self._folders = ConfigService.load_folders()
        self._rebuild_list()

    # ------------------------------------------------------------------
    # Loading and rebuilding the list
    # ------------------------------------------------------------------

    def load_servers(self):
        self._servers = ConfigService.load_servers()
        self._folders = ConfigService.load_folders()
        self._rebuild_list()

    def _server_matches(self, server) -> bool:
        haystack = f"{server.display_name} {server.host} {server.username}".lower()
        return self._search_query in haystack

    def _rebuild_list(self):
        # Remove all rows
        while True:
            row = self._list_box.get_row_at_index(0)
            if row is None:
                break
            self._list_box.remove(row)
        self._rows.clear()
        self._folder_rows.clear()

        # Search mode: flat filtered list (ignores folder grouping/collapse state)
        if self._search_query:
            matched = sorted(
                [s for s in self._servers if self._server_matches(s)],
                key=lambda s: s.display_name.lower(),
            )
            for server in matched:
                server_row = ServerRow(server)
                self._list_box.append(server_row)
                self._rows[server.id] = server_row
            return

        # Build a set of valid folder IDs
        folder_ids = {f.id for f in self._folders}

        # Group servers by folder
        folder_servers = {f.id: [] for f in self._folders}
        ungrouped = []
        for server in self._servers:
            if server.folder_id and server.folder_id in folder_ids:
                folder_servers[server.folder_id].append(server)
            else:
                ungrouped.append(server)

        # Sort folders and ungrouped servers alphabetically
        sorted_folders = sorted(self._folders, key=lambda f: f.name.lower())
        for fid in folder_servers:
            folder_servers[fid].sort(key=lambda s: s.name.lower())
        ungrouped.sort(key=lambda s: s.name.lower())

        # 1) Folders first
        for folder in sorted_folders:
            members = folder_servers.get(folder.id, [])

            folder_row = FolderRow(folder)
            folder_row.set_count(len(members))
            folder_row.connect("toggled", self._on_folder_toggled)
            self._list_box.append(folder_row)
            self._folder_rows[folder.id] = folder_row

            # Attach drop target to the folder row's ListBoxRow parent
            # (will be available after append)
            listbox_row = folder_row.get_parent()
            if listbox_row:
                self._attach_folder_drop_target(listbox_row, folder)

            # If expanded, show member servers indented
            if folder.expanded:
                for server in members:
                    server_row = ServerRow(server)
                    server_row.set_margin_start(16)
                    self._list_box.append(server_row)
                    self._rows[server.id] = server_row
                    # Attach drag source
                    lbr = server_row.get_parent()
                    if lbr:
                        self._attach_server_drag_source(lbr, server)

        # 2) Ungrouped servers
        for server in ungrouped:
            server_row = ServerRow(server)
            self._list_box.append(server_row)
            self._rows[server.id] = server_row
            lbr = server_row.get_parent()
            if lbr:
                self._attach_server_drag_source(lbr, server)

    # ------------------------------------------------------------------
    # Row activation
    # ------------------------------------------------------------------

    def _on_row_selected(self, list_box, row):
        is_server = row is not None and isinstance(row.get_child(), ServerRow)
        self.emit("selection-changed", is_server)

    def _on_row_activated(self, list_box, row):
        child = row.get_child()
        if isinstance(child, FolderRow):
            child._on_toggle_clicked(None)

    def _on_list_double_click(self, gesture, n_press, x, y):
        if n_press != 2:
            return
        row = self._list_box.get_row_at_y(int(y))
        if row is None:
            return
        child = row.get_child()
        if isinstance(child, ServerRow):
            self.emit("server-activated", child.server_info)

    # ------------------------------------------------------------------
    # Server add / edit / delete dialogs
    # ------------------------------------------------------------------

    def show_add_dialog(self):
        win = self.get_root()
        dialog = ServerEditDialog()
        dialog.connect("saved", self._on_server_added)
        dialog.present(win)

    def _show_edit_dialog(self, server_info):
        win = self.get_root()
        dialog = ServerEditDialog(server_info=server_info)
        dialog.connect("saved", self._on_server_edited)
        dialog.present(win)

    def _on_server_added(self, dialog, server):
        self._servers = ConfigService.add_server(server)
        self._rebuild_list()

    def _on_server_edited(self, dialog, server):
        self._servers = ConfigService.update_server(server)
        self._rebuild_list()

    def _confirm_delete(self, server_info):
        win = self.get_root()
        dialog = Adw.AlertDialog(
            heading="Delete Server?",
            body=f"Remove \u201c{server_info.display_name}\u201d from your servers?",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_response, server_info)
        dialog.present(win)

    def _on_delete_response(self, dialog, response, server_info):
        if response == "delete":
            credential_store.delete_password(server_info.id)
            self._servers = ConfigService.delete_server(server_info.id)
            self._rebuild_list()

    def get_selected_server(self) -> ServerInfo | None:
        row = self._list_box.get_selected_row()
        if row:
            child = row.get_child()
            if isinstance(child, ServerRow):
                return child.server_info
        return None

    # ------------------------------------------------------------------
    # Drag and drop
    # ------------------------------------------------------------------

    def _attach_server_drag_source(self, listbox_row, server):
        """Attach a DragSource to a server's ListBoxRow."""
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_drag_prepare, server)
        source.connect("drag-begin", self._on_drag_begin, server)
        listbox_row.add_controller(source)

    def _on_drag_prepare(self, source, x, y, server):
        value = GObject.Value(GObject.TYPE_STRING, server.id)
        return Gdk.ContentProvider.new_for_value(value)

    def _on_drag_begin(self, source, drag, server):
        label = Gtk.Label(label=server.display_name, css_classes=["heading"])
        icon = Gtk.DragIcon.get_for_drag(drag)
        icon.set_child(label)

    def _attach_folder_drop_target(self, listbox_row, folder):
        """Attach a DropTarget to a folder's ListBoxRow."""
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("drop", self._on_folder_drop, folder)
        target.connect("enter", self._on_folder_drag_enter, listbox_row)
        target.connect("leave", self._on_folder_drag_leave, listbox_row)
        listbox_row.add_controller(target)

    def _on_folder_drop(self, target, value, x, y, folder):
        server_id = value
        for s in self._servers:
            if s.id == server_id:
                s.folder_id = folder.id
                break
        ConfigService.save_servers(self._servers)
        self._rebuild_list()
        return True

    def _on_folder_drag_enter(self, target, x, y, listbox_row):
        listbox_row.add_css_class("drop-target")
        return Gdk.DragAction.MOVE

    def _on_folder_drag_leave(self, target, listbox_row):
        listbox_row.remove_css_class("drop-target")

    def _setup_listbox_drop_target(self):
        """Drop target on the list box itself for ungrouping servers."""
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("drop", self._on_listbox_drop)
        self._list_box.add_controller(target)

    def _on_listbox_drop(self, target, value, x, y):
        # Check if we dropped on a folder row — if so, let the folder handle it
        row = self._list_box.get_row_at_y(int(y))
        if row:
            child = row.get_child()
            if isinstance(child, FolderRow):
                return False

        server_id = value
        for s in self._servers:
            if s.id == server_id:
                s.folder_id = ""
                break
        ConfigService.save_servers(self._servers)
        self._rebuild_list()
        return True
