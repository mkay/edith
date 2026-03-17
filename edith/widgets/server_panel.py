import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk, GObject, Gdk, Graphene

from edith.models.server import ServerInfo
from edith.services.config import ConfigService
from edith.services import credential_store
from edith.widgets.server_row import ServerRow
from edith.widgets.server_edit_dialog import ServerEditDialog


class ServerPanel(Gtk.Box):
    """Main pane showing a filtered server list with search and CRUD."""

    __gsignals__ = {
        "server-activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "servers-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._current_group_key = "__all__"
        self._servers = []
        self._folders = []
        self._search_query = ""
        self._list_boxes = []        # all active per-group ListBoxes
        self._active_list_box = None # the one that holds the current selection
        self._selection_updating = False

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

        # Scrolled window → Clamp → content box (holds labels + per-group ListBoxes)
        self._sw = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )

        self._content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_start=12,
            margin_end=12,
            margin_top=12,
            margin_bottom=12,
        )

        _clamp = Adw.Clamp(maximum_size=640, tightening_threshold=400)
        _clamp.set_child(self._content_box)
        self._sw.set_child(_clamp)
        self.append(self._sw)

        # Empty state
        self._empty_page = Adw.StatusPage(
            title="No Servers",
            icon_name="edith-status-no-servers-symbolic",
            description="Add a server using the button in the toolbar.",
            vexpand=True,
            visible=False,
        )
        self.append(self._empty_page)

        self._setup_context_menu()

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

    def _server_matches(self, server) -> bool:
        haystack = f"{server.display_name} {server.host} {server.username}".lower()
        return self._search_query in haystack

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _setup_context_menu(self):
        self._server_menu_model = Gio.Menu()
        self._server_menu_model.append("Connect", "server.connect")
        self._server_menu_model.append("Edit", "server.edit")
        self._server_menu_model.append("Change Password\u2026", "server.change-password")

        self._pin_menu_item = Gio.MenuItem.new("Pin", "server.toggle-pin")
        pin_section = Gio.Menu()
        pin_section.append_item(self._pin_menu_item)
        self._server_menu_model.append_section(None, pin_section)

        self._move_submenu = Gio.Menu()
        other_section = Gio.Menu()
        other_section.append_submenu("Move to Group", self._move_submenu)
        other_section.append("Delete", "server.delete")
        self._server_menu_model.append_section(None, other_section)

        self._server_menu = Gtk.PopoverMenu(menu_model=self._server_menu_model, has_arrow=False)

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

        move_to_group_action = Gio.SimpleAction.new("move-to-group", GLib.VariantType.new("s"))
        move_to_group_action.connect("activate", self._on_context_move_to_group)
        server_group.add_action(move_to_group_action)

        self._change_password_action = Gio.SimpleAction.new("change-password", None)
        self._change_password_action.connect("activate", self._on_context_change_password)
        server_group.add_action(self._change_password_action)

        toggle_pin_action = Gio.SimpleAction.new("toggle-pin", None)
        toggle_pin_action.connect("activate", self._on_context_toggle_pin)
        server_group.add_action(toggle_pin_action)

        # Action group on self — reachable from any child ListBox up the hierarchy
        self.insert_action_group("server", server_group)

        self._context_row = None

    def _on_right_click(self, gesture, n_press, x, y, list_box):
        row = list_box.get_row_at_y(int(y))
        if row is None:
            return

        child = row.get_child()
        if not isinstance(child, ServerRow):
            return

        self._context_row = row
        self._populate_move_submenu(child.server_info.folder_id)
        protocol = getattr(child.server_info, "protocol", "sftp")
        self._change_password_action.set_enabled(
            protocol in ("ftp", "ftps") or child.server_info.auth_method != "key"
        )

        # Update pin label dynamically (pin section is at flat index 3)
        pin_label = "Unpin" if ConfigService.is_server_pinned(child.server_info.id) else "Pin"
        pin_section = Gio.Menu()
        pin_section.append(pin_label, "server.toggle-pin")
        self._server_menu_model.remove(3)
        self._server_menu_model.insert_section(3, None, pin_section)

        # Parent the popover to self (ServerPanel) to avoid the ScrolledWindow
        # scrolling to keep the popover anchor visible.
        pt = Graphene.Point()
        pt.x = float(x)
        pt.y = float(y)
        ok, out_pt = list_box.compute_point(self, pt)
        if ok:
            tx, ty = int(out_pt.x), int(out_pt.y)
        else:
            tx, ty = int(x), int(y)

        self._server_menu.popdown()
        if self._server_menu.get_parent() is not None:
            self._server_menu.unparent()
        self._server_menu.set_parent(self)

        rect = Gdk.Rectangle()
        rect.x = tx
        rect.y = ty
        rect.width = 1
        rect.height = 1
        self._server_menu.set_pointing_to(rect)
        self._server_menu.popup()

    # ------------------------------------------------------------------
    # Context menu actions
    # ------------------------------------------------------------------

    def _on_context_connect(self, action, param):
        if self._context_row:
            child = self._context_row.get_child()
            if isinstance(child, ServerRow):
                self.emit("server-activated", child.server_info)

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

    def _populate_move_submenu(self, current_folder_id: str):
        """Rebuild the 'Move to Group' submenu with all folders except the current one."""
        self._move_submenu.remove_all()
        for folder in self._folders:
            if folder.id == current_folder_id:
                continue
            item = Gio.MenuItem.new(folder.name, None)
            item.set_action_and_target_value(
                "server.move-to-group", GLib.Variant.new_string(folder.id)
            )
            self._move_submenu.append_item(item)
        if current_folder_id != "":
            item = Gio.MenuItem.new("No Group", None)
            item.set_action_and_target_value(
                "server.move-to-group", GLib.Variant.new_string("")
            )
            self._move_submenu.append_item(item)

    def _on_context_move_to_group(self, action, param):
        if self._context_row:
            child = self._context_row.get_child()
            if isinstance(child, ServerRow):
                child.server_info.folder_id = param.get_string()
                ConfigService.update_server(child.server_info)
                self.reload()
                self.emit("servers-changed")

    def _on_context_change_password(self, action, param):
        if self._context_row:
            child = self._context_row.get_child()
            if isinstance(child, ServerRow):
                self._show_change_password_dialog(child.server_info)

    def _on_context_toggle_pin(self, action, param):
        if self._context_row:
            child = self._context_row.get_child()
            if isinstance(child, ServerRow):
                ConfigService.toggle_server_pin(child.server_info.id)
                self._rebuild_list()

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
    # Server add / edit / delete dialogs
    # ------------------------------------------------------------------

    def show_add_dialog(self, folder_id: str = ""):
        win = self.get_root()
        dialog = ServerEditDialog(folder_id=folder_id)
        dialog.connect("saved", self._on_server_added)
        dialog.present(win)

    def _show_edit_dialog(self, server_info):
        win = self.get_root()
        dialog = ServerEditDialog(server_info=server_info)
        dialog.connect("saved", self._on_server_edited)
        dialog.present(win)

    def _on_server_added(self, dialog, server):
        ConfigService.add_server(server)
        self.reload()
        self.emit("servers-changed")

    def _on_server_edited(self, dialog, server):
        ConfigService.update_server(server)
        self.reload()
        self.emit("servers-changed")

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
            ConfigService.delete_server(server_info.id)
            self.reload()
            self.emit("servers-changed")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_group(self, group_key: str, folders: list, servers: list):
        """Filter and rebuild the server list for the given group key."""
        self._current_group_key = group_key
        self._folders = folders
        self._servers = servers
        self._rebuild_list()

    def reload(self):
        """Reload from ConfigService and re-render the current group."""
        self._folders = ConfigService.load_folders()
        self._servers = ConfigService.load_servers()
        self._rebuild_list()

    def get_selected_server(self) -> ServerInfo | None:
        if self._active_list_box:
            row = self._active_list_box.get_selected_row()
            if row:
                child = row.get_child()
                if isinstance(child, ServerRow):
                    return child.server_info
        return None

    # ------------------------------------------------------------------
    # List building
    # ------------------------------------------------------------------

    def _clear_content(self):
        child = self._content_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._content_box.remove(child)
            child = nxt
        self._list_boxes.clear()
        self._active_list_box = None

    def _make_list_box(self) -> Gtk.ListBox:
        """Create a per-group ListBox with boxed-list style."""
        lb = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            activate_on_single_click=False,
            css_classes=["boxed-list"],
        )
        lb.connect("row-activated", self._on_row_activated)
        lb.connect("row-selected", self._on_row_selected)
        gesture = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        gesture.connect("pressed", self._on_right_click, lb)
        lb.add_controller(gesture)
        return lb

    def _partition_pinned(self, servers: list) -> tuple:
        """Split servers into (pinned, unpinned) preserving order within each."""
        pinned_ids = set(ConfigService.get_pinned_servers())
        pinned, unpinned = [], []
        for s in servers:
            if s.id in pinned_ids:
                pinned.append(s)
            else:
                unpinned.append(s)
        return pinned, unpinned

    def _make_server_row(self, server: ServerInfo, pinned: bool) -> ServerRow:
        row = ServerRow(server, pinned=pinned)
        if pinned:
            row.connect("unpin-requested", self._on_unpin_requested)
        return row

    def _on_unpin_requested(self, server_row):
        ConfigService.toggle_server_pin(server_row.server_info.id)
        self._rebuild_list()

    def _add_section(self, title: str, servers: list, is_first: bool = False):
        """Append a group header label followed by a boxed-list for its servers."""
        label = Gtk.Label(
            label=title,
            xalign=0,
            css_classes=["heading"],
            margin_top=0 if is_first else 20,
            margin_bottom=6,
        )
        self._content_box.append(label)
        lb = self._make_list_box()
        pinned, unpinned = self._partition_pinned(servers)
        for s in pinned:
            lb.append(self._make_server_row(s, True))
        for s in unpinned:
            lb.append(self._make_server_row(s, False))
        self._content_box.append(lb)
        self._list_boxes.append(lb)

    def _add_flat_list(self, servers: list):
        """Append a boxed-list without a header (used for single-group views)."""
        lb = self._make_list_box()
        pinned, unpinned = self._partition_pinned(servers)
        for s in pinned:
            lb.append(self._make_server_row(s, True))
        for s in unpinned:
            lb.append(self._make_server_row(s, False))
        self._content_box.append(lb)
        self._list_boxes.append(lb)

    def _rebuild_list(self):
        self._clear_content()

        folder_ids = {f.id for f in self._folders}
        server_count = 0

        if self._current_group_key == "__all__":
            server_count = self._rebuild_all_servers(folder_ids)
        elif self._current_group_key == "__ungrouped__":
            visible = [s for s in self._servers if not s.folder_id or s.folder_id not in folder_ids]
            if self._search_query:
                visible = [s for s in visible if self._server_matches(s)]
            visible.sort(key=lambda s: s.display_name.lower())
            if visible:
                self._add_flat_list(visible)
            server_count = len(visible)
        else:
            visible = [s for s in self._servers if s.folder_id == self._current_group_key]
            if self._search_query:
                visible = [s for s in visible if self._server_matches(s)]
            visible.sort(key=lambda s: s.display_name.lower())
            if visible:
                self._add_flat_list(visible)
            server_count = len(visible)

        self._sw.set_visible(server_count > 0)
        self._empty_page.set_visible(server_count == 0)

    def _rebuild_all_servers(self, folder_ids: set) -> int:
        """Populate content for 'All Servers', grouped by folder when folders exist."""
        if not self._folders:
            visible = list(self._servers)
            if self._search_query:
                visible = [s for s in visible if self._server_matches(s)]
            visible.sort(key=lambda s: s.display_name.lower())
            if visible:
                self._add_flat_list(visible)
            return len(visible)

        folder_groups = {f.id: [] for f in self._folders}
        ungrouped = []
        for s in self._servers:
            if s.folder_id and s.folder_id in folder_ids:
                folder_groups[s.folder_id].append(s)
            else:
                ungrouped.append(s)

        if self._search_query:
            for fid in folder_groups:
                folder_groups[fid] = [s for s in folder_groups[fid] if self._server_matches(s)]
            ungrouped = [s for s in ungrouped if self._server_matches(s)]

        total = 0
        is_first = True
        for folder in self._folders:
            members = sorted(folder_groups.get(folder.id, []), key=lambda s: s.display_name.lower())
            if not members:
                continue
            self._add_section(folder.name, members, is_first)
            is_first = False
            total += len(members)

        if ungrouped:
            ungrouped.sort(key=lambda s: s.display_name.lower())
            self._add_section("Without Group", ungrouped, is_first)
            total += len(ungrouped)

        return total

    # ------------------------------------------------------------------
    # Row signals
    # ------------------------------------------------------------------

    def _on_row_activated(self, list_box, row):
        child = row.get_child()
        if isinstance(child, ServerRow):
            self.emit("server-activated", child.server_info)

    def _on_row_selected(self, list_box, row):
        if self._selection_updating:
            return
        if row is not None:
            # Clear selection in all other groups
            self._selection_updating = True
            for lb in self._list_boxes:
                if lb is not list_box:
                    lb.select_row(None)
            self._selection_updating = False
            self._active_list_box = list_box
        is_server = row is not None and isinstance(row.get_child(), ServerRow)
        self.emit("selection-changed", is_server)
