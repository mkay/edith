import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk, GObject, Gdk

from edith.models.server import FolderInfo
from edith.services.config import ConfigService
from edith.widgets.folder_row import FolderRow
from edith.widgets.file_dialogs import NameDialog


class ServerList(Gtk.Box):
    """Sidebar widget showing server groups for navigation."""

    __gsignals__ = {
        "group-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._servers = []
        self._folders = []
        self._row_keys = {}   # Gtk.ListBoxRow → group key str
        self._folder_rows = {}

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

        sw.set_child(self._list_box)
        self.append(sw)

        self._setup_context_menus()

        self.load_servers()

    # ------------------------------------------------------------------
    # Context menus
    # ------------------------------------------------------------------

    def _setup_context_menus(self):
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
            self._empty_menu.set_pointing_to(rect)
            self._empty_menu.popup()
            return

        self._context_row = row
        child = row.get_child()

        if isinstance(child, FolderRow):
            self._folder_menu.set_pointing_to(rect)
            self._folder_menu.popup()
        # Special rows (All Servers, Without Group): no context menu

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
        current_key = self._get_selected_key()
        folder.name = new_name
        self._folders = ConfigService.update_folder(folder)
        self._rebuild_list()
        self.select_group(current_key or "__all__")

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
            current_key = self._get_selected_key()
            # Ungroup member servers
            for s in self._servers:
                if s.folder_id == folder.id:
                    s.folder_id = ""
            ConfigService.save_servers(self._servers)
            self._folders = ConfigService.delete_folder(folder.id)
            self._servers = ConfigService.load_servers()
            self._rebuild_list()
            # If the deleted folder was selected, fall back to All Servers
            if current_key == folder.id:
                self.select_group("__all__")
            else:
                self.select_group(current_key or "__all__")

    # ------------------------------------------------------------------
    # New folder dialog
    # ------------------------------------------------------------------

    def show_new_folder_dialog(self):
        win = self.get_root()
        dialog = NameDialog("New Server Group", "Group name", "")
        dialog.connect("submitted", self._on_new_folder_submitted)
        dialog.present(win)

    def _on_new_folder_submitted(self, dialog, name):
        current_key = self._get_selected_key()
        folder = FolderInfo(name=name)
        self._folders = ConfigService.add_folder(folder)
        self._rebuild_list()
        self.select_group(current_key or "__all__")

    # ------------------------------------------------------------------
    # Loading and rebuilding the list
    # ------------------------------------------------------------------

    def load_servers(self):
        self._servers = ConfigService.load_servers()
        self._folders = ConfigService.load_folders()
        self._rebuild_list()

    def _rebuild_list(self):
        # Remove all rows
        while True:
            row = self._list_box.get_row_at_index(0)
            if row is None:
                break
            self._list_box.remove(row)
        self._row_keys.clear()
        self._folder_rows.clear()

        # Compute per-folder and ungrouped counts
        folder_ids = {f.id for f in self._folders}
        folder_counts = {f.id: 0 for f in self._folders}
        ungrouped_count = 0
        for s in self._servers:
            if s.folder_id and s.folder_id in folder_ids:
                folder_counts[s.folder_id] += 1
            else:
                ungrouped_count += 1

        # "All Servers" row
        all_widget = self._make_nav_row("edith-server-symbolic", "All Servers", len(self._servers))
        self._list_box.append(all_widget)
        lbr = all_widget.get_parent()
        if lbr:
            self._row_keys[lbr] = "__all__"

        # "Without Group" row — only when ungrouped servers exist
        if ungrouped_count > 0:
            ug_widget = self._make_nav_row("edith-folder-symbolic", "Without Group", ungrouped_count)
            self._list_box.append(ug_widget)
            lbr = ug_widget.get_parent()
            if lbr:
                self._row_keys[lbr] = "__ungrouped__"

        # One FolderRow per folder, sorted alphabetically
        for folder in sorted(self._folders, key=lambda f: f.name.lower()):
            folder_row = FolderRow(folder)
            folder_row.set_count(folder_counts.get(folder.id, 0))
            self._list_box.append(folder_row)
            lbr = folder_row.get_parent()
            if lbr:
                self._row_keys[lbr] = folder.id
            self._folder_rows[folder.id] = folder_row

    def _make_nav_row(self, icon_name: str, label: str, count: int) -> "Gtk.Box":
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_start=8,
            margin_end=8,
            margin_top=8,
            margin_bottom=8,
        )
        icon = Gtk.Image(icon_name=icon_name)
        box.append(icon)
        name_label = Gtk.Label(
            label=label,
            xalign=0,
            hexpand=True,
            css_classes=["heading"],
        )
        box.append(name_label)
        count_label = Gtk.Label(
            label=str(count),
            css_classes=["dim-label", "caption"],
            valign=Gtk.Align.CENTER,
        )
        box.append(count_label)
        return box

    # ------------------------------------------------------------------
    # Row activation / selection
    # ------------------------------------------------------------------

    def _on_row_activated(self, list_box, row):
        key = self._row_keys.get(row)
        if key is not None:
            self.emit("group-selected", key)

    def _on_row_selected(self, list_box, row):
        self.emit("selection-changed", row is not None)

    # ------------------------------------------------------------------
    # Programmatic selection
    # ------------------------------------------------------------------

    def _get_selected_key(self) -> str | None:
        row = self._list_box.get_selected_row()
        if row:
            return self._row_keys.get(row)
        return None

    def select_group(self, key: str):
        """Programmatically select a group row by key and emit group-selected."""
        for lbr, k in self._row_keys.items():
            if k == key:
                self._list_box.select_row(lbr)
                self.emit("group-selected", key)
                return
        # Fallback: select first row
        first = self._list_box.get_row_at_index(0)
        if first:
            self._list_box.select_row(first)
            k = self._row_keys.get(first)
            if k:
                self.emit("group-selected", k)
