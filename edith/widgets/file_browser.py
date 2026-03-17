import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk, GObject, Gdk, Pango

from edith.models.remote_file import RemoteFileInfo, RemoteFileItem
from edith.widgets.file_dialogs import NameDialog, ChmodDialog, FileInfoDialog, DirectoryChooserDialog, ArchiveDialog, InformationDialog

DEFAULT_TOOLS_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "edith" / "tools"


def _get_tools_dir() -> Path:
    from edith.services.config import ConfigService
    custom = ConfigService.get_preference("tools_folder")
    if custom:
        return Path(custom)
    return DEFAULT_TOOLS_DIR



class FileBrowser(Gtk.Box):
    """Remote directory tree browser sidebar widget."""

    __gsignals__ = {
        "file-activated":  (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "path-changed":    (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "pin-requested":   (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._window = None
        self._current_path = "/"
        self._pending_reveal = None
        self._history = []
        self._history_pos = -1
        self._show_hidden = False
        self._items: list[RemoteFileItem] = []
        self._context_item: RemoteFileItem | None = None
        self._cur_dir_writable: bool = False

        # ── Path bar ────────────────────────────────────────────────────
        self._path_bar = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_start=8,
            margin_end=8,
            margin_top=4,
            margin_bottom=4,
        )

        _btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        upload_btn = Gtk.Button(
            icon_name="edith-upload-symbolic",
            tooltip_text="Upload Files",
            css_classes=["flat", "circular"],
        )
        upload_btn.connect("clicked", self._on_upload_clicked)
        _btn_row.append(upload_btn)

        refresh_btn = Gtk.Button(
            icon_name="edith-refresh-symbolic",
            tooltip_text="Refresh",
            css_classes=["flat", "circular"],
        )
        refresh_btn.connect("clicked", lambda _: self.load_directory(self._current_path))
        _btn_row.append(refresh_btn)

        self._hidden_btn = Gtk.ToggleButton(
            icon_name="edith-show-hidden-symbolic",
            tooltip_text="Show Hidden Files",
            css_classes=["flat", "circular"],
        )
        self._hidden_btn.connect("toggled", self._on_show_hidden_toggled)
        _btn_row.append(self._hidden_btn)

        # Spacer to push the following buttons to the right
        spacer = Gtk.Box(hexpand=True)
        _btn_row.append(spacer)

        self._detail_btn = Gtk.ToggleButton(
            icon_name="edith-file-details-symbolic",
            tooltip_text="Show File Details",
            css_classes=["flat", "circular"],
        )
        self._detail_btn.connect("toggled", self._on_detail_mode_toggled)
        _btn_row.append(self._detail_btn)

        self._path_bar.append(_btn_row)
        self.append(self._path_bar)
        self.append(Gtk.Separator())

        # ── Filter entry ────────────────────────────────────────────────
        self._filter_entry = Gtk.SearchEntry(
            placeholder_text="Filter files\u2026",
            margin_start=8,
            margin_end=8,
            margin_top=4,
            margin_bottom=4,
        )
        self._filter_entry.connect("search-changed", self._on_filter_changed)
        self.append(self._filter_entry)

        self._path_store = Gtk.StringList()
        self._path_parts: list[str] = ["/"]

        # Factory for the button: show only the directory name
        button_factory = Gtk.SignalListItemFactory()
        button_factory.connect("setup", self._on_path_button_setup)
        button_factory.connect("bind", self._on_path_button_bind)

        self._path_dropdown = Gtk.DropDown(
            model=self._path_store,
            factory=button_factory,
            margin_start=8,
            margin_end=8,
            margin_bottom=4,
            tooltip_text="/",
        )
        self._path_dropdown.add_css_class("path-dropdown")
        self._path_dropdown_handler = self._path_dropdown.connect(
            "notify::selected", self._on_path_dropdown_changed,
        )
        self.append(self._path_dropdown)

        # ── Column view model chain ──────────────────────────────────────
        self._store = Gio.ListStore(item_type=RemoteFileItem)

        # Column view — created before its sorter is retrieved
        self._column_view = Gtk.ColumnView(
            single_click_activate=False,
            show_row_separators=False,
            show_column_separators=False,
            css_classes=["file-list", "data-table"],
            vexpand=True,
            hexpand=True,
        )
        self._column_view.connect("activate", self._on_cv_activated)

        # Use the ColumnView's own sorter directly so column-header clicks work.
        # Dirs-first is baked into each column's custom sorter (see _setup_columns).
        self._sort_model = Gtk.SortListModel(
            model=self._store, sorter=self._column_view.get_sorter()
        )

        # No filter initially (= match all); set dynamically in _on_filter_changed
        self._filter_model = Gtk.FilterListModel(model=self._sort_model)

        self._selection = Gtk.MultiSelection(model=self._filter_model)
        self._column_view.set_model(self._selection)

        self._setup_columns()

        # ── Stack: column view ↔ status label ───────────────────────────
        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        sw.set_child(self._column_view)

        self._stack = Gtk.Stack(vexpand=True)
        self._stack.add_named(sw, "list")

        self._status_label = Gtk.Label(
            label="",
            css_classes=["dim-label"],
            margin_top=24,
            wrap=True,
        )
        self._stack.add_named(self._status_label, "status")
        self.append(self._stack)

        # ── Loading spinner ──────────────────────────────────────────────
        self._spinner = Gtk.Spinner(spinning=False, visible=False, margin_top=16, margin_bottom=16)
        self.append(self._spinner)

        # ── Path bar drop target ─────────────────────────────────────────
        self._setup_pathbar_drop_target()

        # ── Upload drop target (external files from file manager) ────────
        self._setup_upload_drop_target()

        # ── Context menu ─────────────────────────────────────────────────
        self._setup_context_menu()

        # ── CSS ──────────────────────────────────────────────────────────
        _css = Gtk.CssProvider()
        _css.load_from_string("""
            columnview.file-list > listview > row:selected {
                background-color: alpha(@accent_bg_color, 0.25);
            }
            columnview.file-list > listview > row:selected:hover {
                background-color: alpha(@accent_bg_color, 0.35);
            }
            dropdown.path-dropdown > button {
                border-radius: 12px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            _css,
            Gtk.STYLE_PROVIDER_PRIORITY_USER,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Column setup
    # ──────────────────────────────────────────────────────────────────────

    def _setup_columns(self):
        # Name column
        name_factory = Gtk.SignalListItemFactory()
        name_factory.connect("setup", self._setup_name_cell)
        name_factory.connect("bind", self._bind_name_cell)
        name_factory.connect("unbind", self._unbind_name_cell)
        name_col = Gtk.ColumnViewColumn(title="Name", factory=name_factory, expand=True, resizable=True)
        name_col.set_sorter(Gtk.CustomSorter.new(
            lambda a, b, _: (
                (0 if a.file_info.is_parent_dir == b.file_info.is_parent_dir else (-1 if a.file_info.is_parent_dir else 1))
                or (0 if a.file_info.is_dir == b.file_info.is_dir else (-1 if a.file_info.is_dir else 1))
                or (a.file_info.name.lower() > b.file_info.name.lower()) - (a.file_info.name.lower() < b.file_info.name.lower())
            )
        ))
        self._column_view.append_column(name_col)

        # Size column
        size_factory = Gtk.SignalListItemFactory()
        size_factory.connect("setup", lambda f, li: self._setup_text_cell(
            li, halign=Gtk.Align.END, css=["numeric", "dim-label"]))
        size_factory.connect("bind", self._bind_size_cell)
        size_factory.connect("unbind", self._unbind_text_cell)
        self._size_col = Gtk.ColumnViewColumn(title="Size", factory=size_factory, resizable=True)
        self._size_col.set_fixed_width(92)
        self._size_col.set_visible(False)
        self._size_col.set_sorter(Gtk.CustomSorter.new(
            lambda a, b, _: (
                (0 if a.file_info.is_parent_dir == b.file_info.is_parent_dir else (-1 if a.file_info.is_parent_dir else 1))
                or (0 if a.file_info.is_dir == b.file_info.is_dir else (-1 if a.file_info.is_dir else 1))
                or (a.file_info.size > b.file_info.size) - (a.file_info.size < b.file_info.size)
            )
        ))
        self._column_view.append_column(self._size_col)

        # Permissions column
        perm_factory = Gtk.SignalListItemFactory()
        perm_factory.connect("setup", lambda f, li: self._setup_text_cell(
            li, css=["monospace", "dim-label"]))
        perm_factory.connect("bind", self._bind_perm_cell)
        perm_factory.connect("unbind", self._unbind_text_cell)
        self._perm_col = Gtk.ColumnViewColumn(title="Permissions", factory=perm_factory)
        self._perm_col.set_fixed_width(132)
        self._perm_col.set_visible(False)
        self._perm_col.set_sorter(Gtk.CustomSorter.new(
            lambda a, b, _: (
                (0 if a.file_info.is_parent_dir == b.file_info.is_parent_dir else (-1 if a.file_info.is_parent_dir else 1))
                or (0 if a.file_info.is_dir == b.file_info.is_dir else (-1 if a.file_info.is_dir else 1))
                or (a.file_info.permissions > b.file_info.permissions) - (a.file_info.permissions < b.file_info.permissions)
            )
        ))
        self._column_view.append_column(self._perm_col)

        # Modified column
        mtime_factory = Gtk.SignalListItemFactory()
        mtime_factory.connect("setup", lambda f, li: self._setup_text_cell(li, css=["dim-label"]))
        mtime_factory.connect("bind", self._bind_mtime_cell)
        mtime_factory.connect("unbind", self._unbind_text_cell)
        self._mtime_col = Gtk.ColumnViewColumn(title="Modified", factory=mtime_factory, resizable=True)
        self._mtime_col.set_fixed_width(130)
        self._mtime_col.set_visible(False)
        self._mtime_col.set_sorter(Gtk.CustomSorter.new(
            lambda a, b, _: (
                (0 if a.file_info.is_parent_dir == b.file_info.is_parent_dir else (-1 if a.file_info.is_parent_dir else 1))
                or (0 if a.file_info.is_dir == b.file_info.is_dir else (-1 if a.file_info.is_dir else 1))
                or (a.file_info.mtime > b.file_info.mtime) - (a.file_info.mtime < b.file_info.mtime)
            )
        ))
        self._column_view.append_column(self._mtime_col)

    # ── Name column factory ──────────────────────────────────────────────

    def _setup_name_cell(self, factory, list_item):
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            margin_start=6, margin_end=8,
            margin_top=3, margin_bottom=3,
        )
        icon = Gtk.Image(pixel_size=16)
        label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        box.append(icon)
        box.append(label)

        item_ref = [None]
        is_dir_ref = [False]

        # Drag source
        drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)

        def on_drag_prepare(d, x, y):
            if not item_ref[0] or item_ref[0].file_info.is_parent_dir:
                return None
            paths = self._get_selected_paths_for_drag(item_ref[0])
            return Gdk.ContentProvider.new_for_value(
                GObject.Value(GObject.TYPE_STRING, "\n".join(paths))
            )

        def on_drag_begin(d, gdk_drag):
            if not item_ref[0]:
                return
            paths = self._get_selected_paths_for_drag(item_ref[0])
            drag_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            if len(paths) == 1:
                fi = item_ref[0].file_info
                drag_box.append(Gtk.Image(icon_name=fi.icon_name, pixel_size=16))
                drag_box.append(Gtk.Label(label=fi.name))
            else:
                drag_box.append(Gtk.Image(icon_name="edith-select-items-symbolic", pixel_size=16))
                drag_box.append(Gtk.Label(label=f"{len(paths)} items"))
            Gtk.DragIcon.get_for_drag(gdk_drag).set_child(drag_box)

        drag.connect("prepare", on_drag_prepare)
        drag.connect("drag-begin", on_drag_begin)
        box.add_controller(drag)

        # Drop target (directories only)
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)

        def on_drop_accept(d, drop_val):
            return is_dir_ref[0]

        def on_drop_enter(d, x, y):
            if is_dir_ref[0]:
                box.add_css_class("drop-target")
            return Gdk.DragAction.MOVE if is_dir_ref[0] else Gdk.DragAction(0)

        def on_drop_leave(d):
            box.remove_css_class("drop-target")

        def on_drop(d, value, x, y):
            if is_dir_ref[0] and item_ref[0]:
                box.remove_css_class("drop-target")
                fi = item_ref[0].file_info
                if fi.is_parent_dir:
                    dest = "/".join(self._current_path.rstrip("/").split("/")[:-1]) or "/"
                else:
                    dest = fi.path
                self._perform_drag_move(value, dest)
                return True
            return False

        drop.connect("accept", on_drop_accept)
        drop.connect("enter", on_drop_enter)
        drop.connect("leave", on_drop_leave)
        drop.connect("drop", on_drop)
        box.add_controller(drop)

        list_item._name_item_ref = item_ref
        list_item._name_is_dir_ref = is_dir_ref
        list_item.set_child(box)

    def _bind_name_cell(self, factory, list_item):
        item = list_item.get_item()
        fi = item.file_info
        list_item._name_item_ref[0] = item
        list_item._name_is_dir_ref[0] = fi.is_dir

        box = list_item.get_child()
        box._cv_item = item

        icon = box.get_first_child()
        label = icon.get_next_sibling()

        if fi.is_parent_dir:
            icon.set_visible(True)
            icon.set_from_icon_name("edith-parent-dir-symbolic")
            label.set_text("")
            box.set_tooltip_text("Parent directory")
        else:
            icon.set_visible(True)
            icon.set_from_icon_name(fi.icon_name)
            label.set_text(fi.name)
            box.set_tooltip_text(fi.path)

    def _unbind_name_cell(self, factory, list_item):
        if hasattr(list_item, "_name_item_ref"):
            list_item._name_item_ref[0] = None
        if hasattr(list_item, "_name_is_dir_ref"):
            list_item._name_is_dir_ref[0] = False
        box = list_item.get_child()
        if box:
            box._cv_item = None

    # ── Generic text column helpers ──────────────────────────────────────

    def _setup_text_cell(self, list_item, halign=Gtk.Align.START, css=None):
        label = Gtk.Label(
            halign=halign,
            hexpand=True,
            css_classes=css or [],
            margin_start=6, margin_end=6,
            margin_top=3, margin_bottom=3,
            ellipsize=Pango.EllipsizeMode.END,
        )
        list_item.set_child(label)

    def _bind_size_cell(self, factory, list_item):
        item = list_item.get_item()
        label = list_item.get_child()
        label._cv_item = item
        label.set_text(item.file_info.human_size())

    def _bind_perm_cell(self, factory, list_item):
        item = list_item.get_item()
        label = list_item.get_child()
        label._cv_item = item
        label.set_text(item.file_info.permissions_str())

    def _bind_mtime_cell(self, factory, list_item):
        item = list_item.get_item()
        label = list_item.get_child()
        label._cv_item = item
        label.set_text(item.file_info.mtime_str())

    def _unbind_text_cell(self, factory, list_item):
        label = list_item.get_child()
        if label:
            label._cv_item = None

    # ──────────────────────────────────────────────────────────────────────
    # Context menu
    # ──────────────────────────────────────────────────────────────────────

    def _setup_context_menu(self):
        menu = Gio.Menu()

        # New File / New Folder
        section_new = Gio.Menu()
        section_new.append("New File", "file.new-file")
        section_new.append("New Folder", "file.new-folder")
        menu.append_section(None, section_new)

        # Actions, Upload Tool, Rename, Delete
        actions_submenu = Gio.Menu()
        actions_submenu.append("Move to", "file.move-to")
        actions_submenu.append("Copy to", "file.copy-to")
        actions_submenu.append("Duplicate", "file.duplicate")

        self._tools_submenu = Gio.Menu()

        section_ops = Gio.Menu()
        section_ops.append_submenu("Actions", actions_submenu)
        section_ops.append_submenu("Upload Tool", self._tools_submenu)
        section_ops.append("Rename", "file.rename")
        section_ops.append("Delete", "file.delete")
        menu.append_section(None, section_ops)

        # Download, Copy Path, Open Locally
        section_transfer = Gio.Menu()
        section_transfer.append("Download", "file.download")
        section_transfer.append("Copy Path", "file.copy-path")
        section_transfer.append("Open Locally", "file.open-locally")
        menu.append_section(None, section_transfer)

        # Pin, Information, Refresh
        section_misc = Gio.Menu()
        section_misc.append("Pin", "file.pin")
        section_misc.append("Information", "file.information")
        section_misc.append("Refresh", "file.refresh")
        menu.append_section(None, section_misc)

        self._context_menu = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
        self._context_menu.set_parent(self._column_view)

        # Actions
        group = Gio.SimpleActionGroup()

        new_file_action = Gio.SimpleAction.new("new-file", None)
        new_file_action.connect("activate", self._on_new_file)
        group.add_action(new_file_action)

        new_folder_action = Gio.SimpleAction.new("new-folder", None)
        new_folder_action.connect("activate", self._on_new_folder)
        group.add_action(new_folder_action)

        self._rename_action = Gio.SimpleAction.new("rename", None)
        self._rename_action.connect("activate", self._on_rename)
        group.add_action(self._rename_action)

        self._chmod_action = Gio.SimpleAction.new("chmod", None)
        self._chmod_action.connect("activate", self._on_chmod)
        group.add_action(self._chmod_action)

        self._info_action = Gio.SimpleAction.new("info", None)
        self._info_action.connect("activate", self._on_info)
        group.add_action(self._info_action)

        self._information_action = Gio.SimpleAction.new("information", None)
        self._information_action.connect("activate", self._on_information)
        self._information_action.set_enabled(False)
        group.add_action(self._information_action)

        self._delete_action = Gio.SimpleAction.new("delete", None)
        self._delete_action.connect("activate", self._on_delete)
        group.add_action(self._delete_action)

        self._duplicate_action = Gio.SimpleAction.new("duplicate", None)
        self._duplicate_action.connect("activate", self._on_duplicate)
        group.add_action(self._duplicate_action)

        self._copy_path_action = Gio.SimpleAction.new("copy-path", None)
        self._copy_path_action.connect("activate", self._on_copy_path)
        group.add_action(self._copy_path_action)

        self._move_to_action = Gio.SimpleAction.new("move-to", None)
        self._move_to_action.connect("activate", self._on_move_to)
        group.add_action(self._move_to_action)

        self._copy_to_action = Gio.SimpleAction.new("copy-to", None)
        self._copy_to_action.connect("activate", self._on_copy_to)
        group.add_action(self._copy_to_action)

        self._download_action = Gio.SimpleAction.new("download", None)
        self._download_action.connect("activate", self._on_download)
        self._download_action.set_enabled(False)
        group.add_action(self._download_action)

        self._open_locally_action = Gio.SimpleAction.new("open-locally", None)
        self._open_locally_action.connect("activate", self._on_open_locally)
        self._open_locally_action.set_enabled(False)
        group.add_action(self._open_locally_action)

        self._pin_action = Gio.SimpleAction.new("pin", None)
        self._pin_action.connect("activate", self._on_pin)
        self._pin_action.set_enabled(False)
        group.add_action(self._pin_action)

        self._archive_action = Gio.SimpleAction.new("create-archive", None)
        self._archive_action.connect("activate", self._on_create_archive)
        self._archive_action.set_enabled(False)
        group.add_action(self._archive_action)

        refresh_action = Gio.SimpleAction.new("refresh", None)
        refresh_action.connect("activate", lambda *_: self.load_directory(self._current_path))
        group.add_action(refresh_action)

        upload_tool_action = Gio.SimpleAction.new("upload-tool", GLib.VariantType.new("s"))
        upload_tool_action.connect("activate", self._on_upload_tool)
        group.add_action(upload_tool_action)

        self.insert_action_group("file", group)

        # Right-click: CAPTURE-phase on the column view so we always intercept
        rclick = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        rclick.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        rclick.connect("pressed", self._on_right_click)
        self._column_view.add_controller(rclick)

        # Key handler for F2 / Delete / F5 / Backspace
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self._column_view.add_controller(key_ctrl)

        # Capture Ctrl+A to select all items except ".."
        select_all_ctrl = Gtk.EventControllerKey()
        select_all_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        select_all_ctrl.connect("key-pressed", self._on_select_all_key)
        self._column_view.add_controller(select_all_ctrl)

    def _on_right_click(self, gesture, n_press, x, y):
        # Walk from the deepest picked widget up to find a cell with _cv_item
        widget = self._column_view.pick(x, y, Gtk.PickFlags.DEFAULT)
        item = None
        w = widget
        while w and w is not self._column_view:
            cv_item = getattr(w, "_cv_item", None)
            if cv_item is not None:
                item = cv_item
                break
            w = w.get_parent()

        # Treat the .. row the same as empty space for context menu purposes
        if item is not None and item.file_info.is_parent_dir:
            item = None
        self._context_item = item
        has_item = item is not None

        # Check if multiple items are selected (right-clicked item in selection)
        multi = False
        if has_item:
            bitset = self._selection.get_selection()
            if bitset.get_size() > 1:
                for i in range(bitset.get_size()):
                    pos = bitset.get_nth(i)
                    sel_item = self._filter_model.get_item(pos)
                    if sel_item is item:
                        multi = True
                        break

        # Multi-capable actions
        self._delete_action.set_enabled(has_item)
        self._duplicate_action.set_enabled(has_item)
        self._copy_path_action.set_enabled(has_item)
        self._move_to_action.set_enabled(has_item)
        self._copy_to_action.set_enabled(has_item)
        self._download_action.set_enabled(has_item)

        # Single-item-only actions — disabled when multiple selected
        fi = item.file_info if item else None
        self._information_action.set_enabled(has_item and not multi)
        self._rename_action.set_enabled(has_item and not multi)
        self._pin_action.set_enabled(has_item and not multi)
        self._open_locally_action.set_enabled(
            has_item and not multi and fi is not None and not fi.is_dir)

        # Archive: only for SFTP connections, when item is readable and
        # current directory is writable.
        archive_ok = False
        if has_item and not multi and fi and self._window:
            from edith.services.sftp_client import SftpClient
            client = self._window.sftp_client
            if isinstance(client, SftpClient):
                item_readable = bool(fi.permissions & 0o444)
                cur_dir_writable = self._cur_dir_writable
                archive_ok = item_readable and cur_dir_writable
                if fi.is_dir and not getattr(client, "can_exec", False):
                    archive_ok = False
        self._archive_action.set_enabled(archive_ok)

        # Populate Upload Tool submenu
        self._tools_submenu.remove_all()
        tools_dir = _get_tools_dir()
        tools = sorted(tools_dir.iterdir()) if tools_dir.is_dir() else []
        tools = [t for t in tools if t.is_file()]
        if tools:
            for tool_path in tools:
                item_menu = Gio.MenuItem.new(tool_path.name, None)
                item_menu.set_action_and_target_value(
                    "file.upload-tool", GLib.Variant.new_string(str(tool_path)))
                self._tools_submenu.append_item(item_menu)
        else:
            empty = Gio.MenuItem.new("(empty)", None)
            empty.set_action_and_target_value("file.upload-tool", GLib.Variant.new_string(""))
            self._tools_submenu.append_item(empty)

        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self._context_menu.set_pointing_to(rect)
        self._context_menu.popup()

    def _get_selected_paths_for_drag(self, dragged_item):
        """Return paths to move on drag. If the dragged item is part of the
        GTK multi-selection, return all selected paths; otherwise just the
        dragged item's path."""
        bitset = self._selection.get_selection()
        paths = []
        dragged_in_sel = False
        for i in range(bitset.get_size()):
            pos = bitset.get_nth(i)
            item = self._filter_model.get_item(pos)
            if item and not item.file_info.is_parent_dir:
                paths.append(item.file_info.path)
                if item is dragged_item:
                    dragged_in_sel = True
        if not dragged_in_sel or not paths:
            return [dragged_item.file_info.path]
        return paths

    def _get_focused_item(self):
        """Return the first item in the GTK selection (highlight), or None."""
        bitset = self._selection.get_selection()
        if bitset.get_size() == 0:
            return None
        return self._filter_model.get_item(bitset.get_nth(0))

    def _on_key_pressed(self, ctrl, keyval, keycode, state):
        selected = self._get_focused_item()

        if keyval == Gdk.KEY_F2:
            if selected:
                self._context_item = selected
                self._on_rename(None, None)
                return True

        elif keyval == Gdk.KEY_Delete:
            if selected:
                self._context_item = selected
                self._on_delete(None, None)
                return True

        elif keyval == Gdk.KEY_F5:
            self.load_directory(self._current_path)
            return True

        elif keyval == Gdk.KEY_BackSpace:
            self._on_go_up(None)
            return True

        return False

    def _on_select_all_key(self, ctrl, keyval, keycode, state):
        if keyval == Gdk.KEY_a and state & Gdk.ModifierType.CONTROL_MASK:
            self._selection.unselect_all()
            for i in range(self._filter_model.get_n_items()):
                item = self._filter_model.get_item(i)
                if item and not item.file_info.is_parent_dir:
                    self._selection.select_item(i, False)
            return True
        return False

    def _get_context_file_info(self) -> RemoteFileInfo | None:
        if self._context_item:
            return self._context_item.file_info
        return None

    def _get_context_file_infos(self) -> list[RemoteFileInfo]:
        """Return all file infos relevant to the current context action.

        If the GTK multi-selection contains the right-clicked item and has
        more than one entry, return all GTK-selected items.  Falls back to
        just the single right-clicked item.
        """
        ci = self._context_item
        if not ci:
            return []

        bitset = self._selection.get_selection()
        if bitset.get_size() > 1:
            gtk_items = []
            context_in_sel = False
            for i in range(bitset.get_size()):
                pos = bitset.get_nth(i)
                item = self._filter_model.get_item(pos)
                if item and not item.file_info.is_parent_dir:
                    gtk_items.append(item.file_info)
                    if item is ci:
                        context_in_sel = True
            if context_in_sel and gtk_items:
                return gtk_items

        return [ci.file_info]

    # ──────────────────────────────────────────────────────────────────────
    # File operation handlers (unchanged logic)
    # ──────────────────────────────────────────────────────────────────────

    def _on_new_file(self, action, param):
        dialog = NameDialog("New File", "Filename")
        dialog.connect("submitted", self._do_new_file)
        dialog.present(self.get_root())

    def _do_new_file(self, dialog, name):
        if not self._window or not self._window.sftp_client:
            return
        client = self._window.sftp_client
        path = self._current_path
        full_path = f"{path.rstrip('/')}/{name}"
        from edith.services.async_worker import run_async
        run_async(
            lambda: client.create_file(full_path),
            lambda _: self.load_directory(self._current_path),
            lambda e: self._show_op_error(str(e)),
        )

    def _on_new_folder(self, action, param):
        dialog = NameDialog("New Folder", "Folder name")
        dialog.connect("submitted", self._do_new_folder)
        dialog.present(self.get_root())

    def _do_new_folder(self, dialog, name):
        if not self._window or not self._window.sftp_client:
            return
        client = self._window.sftp_client
        path = self._current_path
        full_path = f"{path.rstrip('/')}/{name}"
        from edith.services.async_worker import run_async
        run_async(
            lambda: client.mkdir(full_path),
            lambda _: self.load_directory(self._current_path),
            lambda e: self._show_op_error(str(e)),
        )

    def _on_rename(self, action, param):
        fi = self._get_context_file_info()
        if not fi:
            return
        dialog = NameDialog("Rename", "New name", initial_text=fi.name)
        dialog.connect("submitted", self._do_rename, fi)
        dialog.present(self.get_root())

    def _do_rename(self, dialog, new_name, fi):
        if not self._window or not self._window.sftp_client:
            return
        client = self._window.sftp_client
        old_path = fi.path
        parent = "/".join(old_path.rstrip("/").split("/")[:-1]) or "/"
        new_path = f"{parent.rstrip('/')}/{new_name}"
        from edith.services.async_worker import run_async
        run_async(
            lambda: client.rename(old_path, new_path),
            lambda _: self.load_directory(self._current_path),
            lambda e: self._show_op_error(str(e)),
        )

    def _on_chmod(self, action, param):
        fi = self._get_context_file_info()
        if not fi:
            return
        dialog = ChmodDialog(fi.permissions)
        dialog.connect("applied", self._do_chmod, fi)
        dialog.present(self.get_root())

    def _do_chmod(self, dialog, mode, fi):
        if not self._window or not self._window.sftp_client:
            return
        client = self._window.sftp_client
        from edith.services.async_worker import run_async
        run_async(
            lambda: client.chmod(fi.path, mode),
            lambda _: self.load_directory(self._current_path),
            lambda e: self._show_op_error(str(e)),
        )

    def _on_info(self, action, param):
        fi = self._get_context_file_info()
        if not fi:
            return
        dialog = FileInfoDialog(fi)
        dialog.present(self.get_root())

    def _on_information(self, action, param):
        fi = self._get_context_file_info()
        if not fi:
            return
        dialog = InformationDialog(fi)
        dialog.connect("chmod-applied", self._do_chmod, fi)
        dialog.present(self.get_root())

    def _on_delete(self, action, param):
        infos = self._get_context_file_infos()
        if not infos:
            return
        if len(infos) > 1:
            n = len(infos)
            dlg = Adw.AlertDialog(
                heading=f"Delete {n} items?",
                body="This will permanently delete the selected files and folders.",
            )
            dlg.add_response("cancel", "Cancel")
            dlg.add_response("delete", f"Delete {n} Items")
            dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.connect("response", self._do_bulk_delete, infos)
            dlg.present(self.get_root())
            return
        fi = infos[0]
        kind = "folder" if fi.is_dir else "file"
        dlg = Adw.AlertDialog(
            heading=f"Delete {kind}?",
            body=f"Permanently delete \u201c{fi.name}\u201d?",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", self._do_delete, fi)
        dlg.present(self.get_root())

    def _do_delete(self, dialog, response, fi):
        if response != "delete":
            return
        if not self._window or not self._window.sftp_client:
            return
        client = self._window.sftp_client
        name = fi.name
        from edith.services.async_worker import run_async

        def on_deleted(_):
            self.load_directory(self._current_path)
            if self._window:
                self._window.show_toast(f"Deleted \u201c{name}\u201d", "success")

        if fi.is_dir:
            run_async(lambda: client.rmdir_recursive(fi.path), on_deleted,
                      lambda e: self._show_op_error(str(e)))
        else:
            run_async(lambda: client.remove(fi.path), on_deleted,
                      lambda e: self._show_op_error(str(e)))

    def _on_duplicate(self, action, param):
        infos = self._get_context_file_infos()
        if not infos or not self._window or not self._window.sftp_client:
            return
        client = self._window.sftp_client
        from edith.services.async_worker import run_async
        cur = self._current_path.rstrip("/")

        pairs = []
        for fi in infos:
            base = fi.name
            if not fi.is_dir and "." in base:
                stem, ext = base.rsplit(".", 1)
                dst_name = f"{stem} (copy).{ext}"
            else:
                dst_name = f"{base} (copy)"
            pairs.append((fi.path, f"{cur}/{dst_name}", fi.is_dir))

        def do_dups():
            for src, dst, is_dir in pairs:
                if is_dir:
                    client.copy_remote_recursive(src, dst)
                else:
                    client.copy_remote(src, dst)

        run_async(do_dups,
                  lambda _: self.load_directory(self._current_path),
                  lambda e: self._show_op_error(str(e)))

    def _on_copy_path(self, action, param):
        infos = self._get_context_file_infos()
        if not infos:
            return
        text = "\n".join(fi.path for fi in infos)
        self.get_display().get_clipboard().set(text)

    def _on_move_to(self, action, param):
        infos = self._get_context_file_infos()
        if not infos or not self._window or not self._window.sftp_client:
            return
        if len(infos) > 1:
            dialog = DirectoryChooserDialog(
                self._window.sftp_client,
                title=f"Move {len(infos)} items to\u2026",
                start_path=self._current_path,
            )
            dialog.connect("chosen", self._do_bulk_move_to, infos)
            dialog.present(self.get_root())
            return
        fi = infos[0]
        dialog = DirectoryChooserDialog(
            self._window.sftp_client,
            title=f"Move \u201c{fi.name}\u201d to\u2026",
            start_path=self._current_path,
        )
        dialog.connect("chosen", self._do_move_to, fi)
        dialog.present(self.get_root())

    def _do_move_to(self, dialog, dest_dir, fi):
        if not self._window or not self._window.sftp_client:
            return
        client = self._window.sftp_client
        src = fi.path
        name = fi.name
        dst = f"{dest_dir.rstrip('/')}/{name}"
        if src == dst:
            return
        from edith.services.async_worker import run_async
        dest_name = dest_dir.rstrip("/").rsplit("/", 1)[-1] or dest_dir

        def on_moved(_):
            self.load_directory(self._current_path)
            if self._window:
                self._window.show_toast(f"Moved \u201c{name}\u201d to \u201c{dest_name}\u201d", "success")

        run_async(lambda: client.rename(src, dst), on_moved,
                  lambda e: self._show_op_error(str(e)))

    def _on_copy_to(self, action, param):
        infos = self._get_context_file_infos()
        if not infos or not self._window or not self._window.sftp_client:
            return
        if len(infos) > 1:
            dialog = DirectoryChooserDialog(
                self._window.sftp_client,
                title=f"Copy {len(infos)} items to\u2026",
                start_path=self._current_path,
            )
            dialog.connect("chosen", self._do_bulk_copy_to, infos)
            dialog.present(self.get_root())
            return
        fi = infos[0]
        dialog = DirectoryChooserDialog(
            self._window.sftp_client,
            title=f"Copy \u201c{fi.name}\u201d to\u2026",
            start_path=self._current_path,
        )
        dialog.connect("chosen", self._do_copy_to, fi)
        dialog.present(self.get_root())

    def _do_copy_to(self, dialog, dest_dir, fi):
        if not self._window or not self._window.sftp_client:
            return
        client = self._window.sftp_client
        src = fi.path
        name = fi.name
        dst = f"{dest_dir.rstrip('/')}/{name}"
        if src == dst:
            return
        from edith.services.async_worker import run_async
        if fi.is_dir:
            run_async(lambda: client.copy_remote_recursive(src, dst),
                      lambda _: self.load_directory(self._current_path),
                      lambda e: self._show_op_error(str(e)))
        else:
            run_async(lambda: client.copy_remote(src, dst),
                      lambda _: self.load_directory(self._current_path),
                      lambda e: self._show_op_error(str(e)))

    def _on_pin(self, action, param):
        fi = self._get_context_file_info()
        if fi:
            self.emit("pin-requested", fi.path, fi.is_dir)

    def _on_create_archive(self, action, param):
        fi = self._get_context_file_info()
        if not fi or not self._window or not self._window.sftp_client:
            return
        dialog = ArchiveDialog(fi.name)
        dialog.connect("submitted", self._do_create_archive, fi)
        dialog.present(self.get_root())

    def _do_create_archive(self, dialog, archive_name, fi):
        if not self._window or not self._window.sftp_client:
            return
        client = self._window.sftp_client
        import shlex

        cur_dir = self._current_path
        remote_src = fi.path
        is_dir = fi.is_dir

        # Normalize archive name / format
        if archive_name.endswith(".zip"):
            archive_fmt = "zip"
            archive_mode = "zip"
        elif archive_name.endswith((".tar.gz", ".tgz")):
            archive_fmt = "tar.gz"
            archive_mode = "w:gz"
        elif archive_name.endswith((".tar.bz2", ".tbz2")):
            archive_fmt = "tar.bz2"
            archive_mode = "w:bz2"
        elif archive_name.endswith((".tar.xz", ".txz")):
            archive_fmt = "tar.xz"
            archive_mode = "w:xz"
        elif archive_name.endswith(".tar"):
            archive_fmt = "tar"
            archive_mode = "w:"
        else:
            archive_fmt = "tar.gz"
            archive_mode = "w:gz"
            archive_name += ".tar.gz"

        remote_archive = f"{cur_dir.rstrip('/')}/{archive_name}"
        final_name = archive_name

        queue = self._window._transfer_queue
        if not queue:
            return

        def _try_remote_exec(progress_cb):
            """Try creating archive server-side via SSH exec."""
            src_name = shlex.quote(fi.name)
            parent_dir = shlex.quote(cur_dir)
            dst = shlex.quote(remote_archive)

            if archive_fmt == "zip":
                cmd = f"cd {parent_dir} && zip -qr {dst} {src_name}"
            else:
                tar_flags = {
                    "tar.gz": "czf", "tar.bz2": "cjf",
                    "tar.xz": "cJf", "tar": "cf",
                }
                cmd = f"tar {tar_flags[archive_fmt]} {dst} -C {parent_dir} {src_name}"

            exit_code, _stdout, stderr = client.exec_command(cmd, timeout=600)
            if exit_code != 0:
                raise RuntimeError(stderr.strip() or f"exit code {exit_code}")
            return final_name

        def _fallback_single_file(progress_cb):
            """Archive a single file: download, compress locally, upload."""
            import tarfile
            import zipfile
            import tempfile
            import shutil

            tmp_dir = tempfile.mkdtemp(prefix="edith-archive-")
            try:
                local_src = os.path.join(tmp_dir, fi.name)
                client.download(remote_src, local_src,
                                progress_cb=lambda d, t: progress_cb(0, 0))

                archive_local = os.path.join(tmp_dir, final_name)
                if archive_mode == "zip":
                    with zipfile.ZipFile(archive_local, "w", zipfile.ZIP_DEFLATED) as zf:
                        zf.write(local_src, fi.name)
                else:
                    with tarfile.open(archive_local, archive_mode) as tf:
                        tf.add(local_src, arcname=fi.name)

                archive_size = os.path.getsize(archive_local)
                client.upload(archive_local, remote_archive, overwrite=False,
                              progress_cb=lambda done, _t: progress_cb(done, archive_size))

                return final_name
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        def do_archive(progress_cb):
            # Check if archive already exists
            try:
                client.stat(remote_archive)
                raise FileExistsError(f"'{final_name}' already exists on the server")
            except FileNotFoundError:
                pass

            # Directories always use server-side exec (menu is hidden
            # when exec is unavailable).  Single files try exec first,
            # then fall back to download+compress+upload.
            if is_dir:
                return _try_remote_exec(progress_cb)

            try:
                return _try_remote_exec(progress_cb)
            except Exception:
                # Clean up partial archive if exec left one behind
                try:
                    client.stat(remote_archive)
                    client.remove(remote_archive)
                except (FileNotFoundError, OSError):
                    pass

            return _fallback_single_file(progress_cb)

        def on_success(name):
            self.load_directory(cur_dir)
            if self._window:
                self._window.show_toast(f"Created \u201c{name}\u201d", "success")

        queue.enqueue(
            f"Archive {fi.name}",
            do_archive,
            on_success,
            lambda e: self._show_op_error(str(e)),
        )

    def reveal_file(self, remote_path: str):
        """Navigate to the parent directory of remote_path and select its row."""
        parent = "/".join(remote_path.rstrip("/").split("/")[:-1]) or "/"
        filename = remote_path.rstrip("/").rsplit("/", 1)[-1]
        self._pending_reveal = filename
        self.load_directory(parent)

    def set_window(self, window):
        self._window = window

    @property
    def can_go_back(self) -> bool:
        return self._history_pos > 0

    @property
    def can_go_forward(self) -> bool:
        return self._history_pos < len(self._history) - 1

    def go_back(self):
        if self.can_go_back:
            self._history_pos -= 1
            self.load_directory(self._history[self._history_pos], add_to_history=False)

    def go_forward(self):
        if self.can_go_forward:
            self._history_pos += 1
            self.load_directory(self._history[self._history_pos], add_to_history=False)

    def reset_history(self):
        self._history = []
        self._history_pos = -1

    # ──────────────────────────────────────────────────────────────────────
    # Path dropdown
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _on_path_button_setup(_factory, list_item):
        label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        list_item.set_child(label)

    @staticmethod
    def _on_path_button_bind(_factory, list_item):
        label = list_item.get_child()
        path = list_item.get_item().get_string()
        name = path.rsplit("/", 1)[-1] or "/"
        label.set_label(name)

    def _update_path_dropdown(self, path: str):
        """Rebuild the dropdown with ancestor directories, deepest first."""
        parts = [p for p in path.split("/") if p]
        ancestors: list[str] = []
        for i in range(len(parts), 0, -1):
            ancestors.append("/" + "/".join(parts[:i]))
        ancestors.append("/")
        self._path_parts = ancestors

        self._path_dropdown.handler_block(self._path_dropdown_handler)
        self._path_store.splice(0, self._path_store.get_n_items(), ancestors)
        self._path_dropdown.set_selected(0)
        self._path_dropdown.set_tooltip_text(path)
        self._path_dropdown.handler_unblock(self._path_dropdown_handler)

    def _on_path_dropdown_changed(self, dropdown, _pspec):
        idx = dropdown.get_selected()
        if idx < len(self._path_parts):
            target = self._path_parts[idx]
            if target != self._current_path:
                self.load_directory(target)

    # ──────────────────────────────────────────────────────────────────────
    # Directory loading
    # ──────────────────────────────────────────────────────────────────────

    def load_directory(self, path: str, add_to_history: bool = True):
        if not self._window or not self._window.sftp_client:
            return

        if add_to_history:
            if not self._history or path != self._history[self._history_pos]:
                self._history = self._history[:self._history_pos + 1]
                self._history.append(path)
                self._history_pos += 1

        self._current_path = path
        self._update_path_dropdown(path)
        self.emit("path-changed", path)

        self._spinner.set_visible(True)
        self._spinner.set_spinning(True)

        client = self._window.sftp_client
        from edith.services.async_worker import run_async
        show_hidden = self._show_hidden

        def do_list():
            entries = client.listdir_attr(path)
            files = []
            for attr in entries:
                if not show_hidden and attr.filename.startswith("."):
                    continue
                files.append(RemoteFileInfo.from_sftp_attr(attr, path))
            files.sort(key=lambda f: (not f.is_dir, f.name.lower()))
            # Check if current directory is writable (for archive feature)
            dir_writable = False
            try:
                st = client.stat(path)
                dir_writable = bool(st.st_mode & 0o222)
            except OSError:
                pass
            return files, dir_writable

        def on_success(result):
            files, dir_writable = result
            self._cur_dir_writable = dir_writable
            self._spinner.set_visible(False)
            self._spinner.set_spinning(False)
            self._populate(files)

        def on_error(error):
            self._spinner.set_visible(False)
            self._spinner.set_spinning(False)
            self._show_listing_error(str(error))

        run_async(do_list, on_success, on_error)

    def _populate(self, files: list[RemoteFileInfo]):
        self._items.clear()
        self._store.remove_all()
        self._context_item = None

        self._stack.set_visible_child_name("list")

        # Parent directory shortcut (not tracked in _items — excluded from multi-select)
        if self._current_path != "/":
            parent_path = "/".join(self._current_path.rstrip("/").split("/")[:-1]) or "/"
            parent_fi = RemoteFileInfo(
                name="..", path=parent_path, is_dir=True, is_parent_dir=True,
            )
            self._store.append(RemoteFileItem(parent_fi))

        if not files:
            if self._current_path == "/":
                self._status_label.set_text("Empty directory")
                self._status_label.remove_css_class("error")
                self._stack.set_visible_child_name("status")
            return

        for fi in files:
            item = RemoteFileItem(fi)
            self._items.append(item)
            self._store.append(item)

        if self._pending_reveal:
            target = self._pending_reveal
            self._pending_reveal = None
            for i in range(self._filter_model.get_n_items()):
                item = self._filter_model.get_item(i)
                if item.file_info.name == target:
                    self._selection.select_item(i, True)
                    break

    def _show_listing_error(self, message: str):
        self._items.clear()
        self._store.remove_all()
        self._status_label.set_text(f"Error: {message}")
        self._status_label.add_css_class("error")
        self._stack.set_visible_child_name("status")

    def _show_op_error(self, message: str):
        if self._window:
            self._window.show_toast(f"Error: {message}", "error")
        else:
            self._show_listing_error(message)

    def _on_cv_activated(self, column_view, position):
        item = self._filter_model.get_item(position)
        if item is None:
            return
        if item.file_info.is_dir:
            self.load_directory(item.file_info.path)
        else:
            self.emit("file-activated", item.file_info.path)

    def _on_filter_changed(self, entry):
        query = entry.get_text().strip().lower()
        if query:
            f = Gtk.CustomFilter.new(
                lambda item, _: item.file_info.is_parent_dir or query in item.file_info.name.lower()
            )
            self._filter_model.set_filter(f)
        else:
            self._filter_model.set_filter(None)

    def _on_show_hidden_toggled(self, btn):
        self._show_hidden = btn.get_active()
        btn.set_icon_name(
            "edith-show-hidden-active-symbolic" if self._show_hidden
            else "edith-show-hidden-symbolic"
        )
        self.load_directory(self._current_path)

    def _on_go_up(self, btn):
        if self._current_path == "/":
            return
        parent = "/".join(self._current_path.rstrip("/").split("/")[:-1])
        if not parent:
            parent = "/"
        self.load_directory(parent)

    def _on_detail_mode_toggled(self, btn):
        show = btn.get_active()
        btn.set_icon_name(
            "edith-file-details-active-symbolic" if show
            else "edith-file-details-symbolic"
        )
        self._size_col.set_visible(show)
        self._perm_col.set_visible(show)
        self._mtime_col.set_visible(show)
        if self._window:
            self._window.adjust_sidebar_width(550 if show else 280)

    def _do_bulk_delete(self, dialog, response, infos):
        if response != "delete":
            return
        if not self._window or not self._window.sftp_client:
            return
        client = self._window.sftp_client
        from edith.services.async_worker import run_async
        items = [(fi.path, fi.is_dir) for fi in infos]
        n = len(items)

        def do_delete():
            for path, is_dir in items:
                if is_dir:
                    client.rmdir_recursive(path)
                else:
                    client.remove(path)

        def on_bulk_deleted(_):
            self.load_directory(self._current_path)
            if self._window:
                self._window.show_toast(f"Deleted {n} item{'s' if n != 1 else ''}", "success")

        run_async(do_delete, on_bulk_deleted, lambda e: self._show_op_error(str(e)))

    def _do_bulk_move_to(self, dialog, dest_dir, infos):
        if not self._window or not self._window.sftp_client:
            return
        client = self._window.sftp_client
        from edith.services.async_worker import run_async
        moves = [(fi.path, f"{dest_dir.rstrip('/')}/{fi.name}") for fi in infos]
        n = len(moves)
        dest_label = dest_dir.rstrip("/").rsplit("/", 1)[-1] or dest_dir

        def do_moves():
            for src, dst in moves:
                if src != dst:
                    client.rename(src, dst)

        def on_bulk_moved(_):
            self.load_directory(self._current_path)
            if self._window:
                self._window.show_toast(
                    f"Moved {n} item{'s' if n != 1 else ''} to \u201c{dest_label}\u201d", "success"
                )

        run_async(do_moves, on_bulk_moved, lambda e: self._show_op_error(str(e)))

    def _on_bulk_download_folder(self, dialog, result, infos):
        try:
            folder = dialog.select_folder_finish(result)
        except Exception:
            return
        local_dir = folder.get_path()
        if not local_dir or not self._window:
            return
        self._window.enqueue_bulk_download(
            [(fi.path, os.path.join(local_dir, fi.name)) for fi in infos]
        )

    def _do_bulk_copy_to(self, dialog, dest_dir, infos):
        if not self._window or not self._window.sftp_client:
            return
        client = self._window.sftp_client
        from edith.services.async_worker import run_async
        copies = [(fi.path, f"{dest_dir.rstrip('/')}/{fi.name}", fi.is_dir) for fi in infos]
        n = len(copies)
        dest_label = dest_dir.rstrip("/").rsplit("/", 1)[-1] or dest_dir

        def do_copies():
            for src, dst, is_dir in copies:
                if src != dst:
                    if is_dir:
                        client.copy_remote_recursive(src, dst)
                    else:
                        client.copy_remote(src, dst)

        def on_bulk_copied(_):
            self.load_directory(self._current_path)
            if self._window:
                self._window.show_toast(
                    f"Copied {n} item{'s' if n != 1 else ''} to \u201c{dest_label}\u201d", "success"
                )

        run_async(do_copies, on_bulk_copied, lambda e: self._show_op_error(str(e)))

    # ──────────────────────────────────────────────────────────────────────
    # Upload Tool
    # ──────────────────────────────────────────────────────────────────────

    def _on_upload_tool(self, action, param):
        path = param.get_string()
        if not path:
            return
        self._do_upload_paths([path])

    # ──────────────────────────────────────────────────────────────────────
    # Upload
    # ──────────────────────────────────────────────────────────────────────

    def _on_upload_clicked(self, btn):
        if not self._window or not self._window.sftp_client:
            return
        dialog = Gtk.FileDialog(title="Upload Files")
        dialog.open_multiple(self.get_root(), None, self._on_upload_files_selected)

    def _on_upload_files_selected(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
        except Exception:
            return
        if not files or not self._window or not self._window.sftp_client:
            return
        paths = []
        for i in range(files.get_n_items()):
            gfile = files.get_item(i)
            local_path = gfile.get_path()
            if local_path:
                paths.append(local_path)
        if paths:
            self._do_upload_paths(paths)

    def _do_upload_paths(self, local_paths):
        if not self._window or not self._window.sftp_client:
            return
        dest_dir = self._current_path
        client = self._window.sftp_client
        from edith.services.async_worker import run_async

        def check_conflicts():
            existing = []
            for local_path in local_paths:
                name = os.path.basename(local_path)
                remote_path = f"{dest_dir.rstrip('/')}/{name}"
                try:
                    client.stat(remote_path)
                    existing.append(name)
                except OSError:
                    pass  # file does not exist on remote
            return existing

        def on_checked(existing):
            new_paths = [p for p in local_paths
                         if os.path.basename(p) not in existing]
            conflict_paths = [p for p in local_paths
                              if os.path.basename(p) in existing]

            # Enqueue non-conflicting uploads immediately
            for local_path in new_paths:
                name = os.path.basename(local_path)
                remote_path = f"{dest_dir.rstrip('/')}/{name}"
                self._window.enqueue_upload(
                    local_path, remote_path,
                    on_done=lambda _: self.load_directory(dest_dir),
                )

            # Ask about conflicts
            if conflict_paths:
                self._ask_overwrite(conflict_paths, existing, dest_dir)

        run_async(check_conflicts, on_checked, lambda e: self._show_op_error(str(e)))

    def _ask_overwrite(self, conflict_paths, existing_names, dest_dir):
        n = len(existing_names)
        if n == 1:
            is_dir = os.path.isdir(conflict_paths[0])
            kind = "Folder" if is_dir else "File"
            heading = f"{kind} already exists"
            body = f"\u201c{existing_names[0]}\u201d already exists in the current directory. Do you want to replace it?"
        else:
            listing = ", ".join(f"\u201c{name}\u201d" for name in existing_names[:5])
            if n > 5:
                listing += f" and {n - 5} more"
            heading = f"{n} items already exist"
            body = f"{listing} already exist in the current directory. Do you want to replace them?"

        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("replace", "Replace")
        dlg.set_response_appearance("replace", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        dlg.connect("response", self._on_overwrite_response, conflict_paths, dest_dir)
        dlg.present(self.get_root())

    def _on_overwrite_response(self, dlg, response, conflict_paths, dest_dir):
        if response != "replace":
            return
        for local_path in conflict_paths:
            name = os.path.basename(local_path)
            remote_path = f"{dest_dir.rstrip('/')}/{name}"
            self._window.enqueue_upload(
                local_path, remote_path,
                on_done=lambda _: self.load_directory(dest_dir),
                overwrite=True,
            )

    def _on_download(self, action, param):
        infos = self._get_context_file_infos()
        if not infos:
            return
        if len(infos) > 1:
            dialog = Gtk.FileDialog(title=f"Download {len(infos)} items to\u2026")
            dialog.select_folder(self.get_root(), None,
                                 lambda d, r: self._on_bulk_download_folder(d, r, infos))
            return
        info = infos[0]
        if info.is_dir:
            dialog = Gtk.FileDialog(title=f"Download \u201c{info.name}\u201d to")
            dialog.select_folder(self.get_root(), None, self._on_download_folder_chosen)
        else:
            dialog = Gtk.FileDialog(title="Save File", initial_name=info.name)
            dialog.save(self.get_root(), None, self._on_download_save_chosen)

    def _on_download_folder_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except Exception:
            return
        local_dir = folder.get_path()
        if not local_dir:
            return
        info = self._get_context_file_info()
        if not info or not self._window:
            return
        local_path = os.path.join(local_dir, info.name)
        self._window.enqueue_download(info.path, local_path)

    def _on_download_save_chosen(self, dialog, result):
        try:
            file = dialog.save_finish(result)
        except Exception:
            return
        local_path = file.get_path()
        if not local_path:
            return
        info = self._get_context_file_info()
        if not info or not self._window:
            return
        self._window.enqueue_download(info.path, local_path)

    def _on_open_locally(self, action, param):
        import tempfile
        fi = self._get_context_file_info()
        if not fi or not self._window or not self._window.sftp_client:
            return
        client = self._window.sftp_client
        tmp_dir = tempfile.mkdtemp(prefix="edith-")
        local_path = os.path.join(tmp_dir, fi.name)
        from edith.services.async_worker import run_async

        def do_download():
            client.download_recursive(fi.path, local_path)

        def on_done(_):
            launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(local_path))
            launcher.launch(self.get_root(), None, self._on_launch_finish)

        run_async(do_download, on_done, lambda e: self._show_op_error(str(e)))

    def _on_launch_finish(self, launcher, result):
        try:
            launcher.launch_finish(result)
        except (OSError, GLib.Error):
            pass

    # ──────────────────────────────────────────────────────────────────────
    # Drag and drop
    # ──────────────────────────────────────────────────────────────────────

    def _setup_pathbar_drop_target(self):
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("drop", self._on_pathbar_drop)
        target.connect("enter", self._on_pathbar_drag_enter)
        target.connect("leave", self._on_pathbar_drag_leave)
        self._path_bar.add_controller(target)

    def _on_pathbar_drag_enter(self, target, x, y):
        self._path_bar.add_css_class("drop-target")
        return Gdk.DragAction.MOVE

    def _on_pathbar_drag_leave(self, target):
        self._path_bar.remove_css_class("drop-target")

    def _on_pathbar_drop(self, target, value, x, y):
        self._perform_drag_move(value, self._current_path)
        return True

    def _perform_drag_move(self, value, dest_dir):
        if not self._window or not self._window.sftp_client:
            return
        src_paths = [p for p in value.split("\n") if p]
        if not src_paths:
            return
        client = self._window.sftp_client
        from edith.services.async_worker import run_async
        dest_stripped = dest_dir.rstrip("/")
        dest_label = dest_stripped.rsplit("/", 1)[-1] or dest_dir

        # Filter out no-ops
        valid = []
        for sp in src_paths:
            src_parent = "/".join(sp.rstrip("/").split("/")[:-1]) or "/"
            if src_parent.rstrip("/") == dest_stripped:
                continue
            if sp.rstrip("/") == dest_stripped:
                continue
            if dest_stripped.startswith(sp.rstrip("/") + "/"):
                continue
            valid.append(sp)
        if not valid:
            return

        def do_moves():
            for sp in valid:
                name = sp.rstrip("/").rsplit("/", 1)[-1]
                client.rename(sp, f"{dest_stripped}/{name}")

        n = len(valid)
        def on_done(_):
            self.load_directory(self._current_path)
            if self._window:
                if n == 1:
                    name = valid[0].rstrip("/").rsplit("/", 1)[-1]
                    self._window.show_toast(f"Moved \u201c{name}\u201d to \u201c{dest_label}\u201d", "success")
                else:
                    self._window.show_toast(f"Moved {n} items to \u201c{dest_label}\u201d", "success")

        run_async(do_moves, on_done, lambda e: self._show_op_error(str(e)))

    # ──────────────────────────────────────────────────────────────────────
    # Upload drop target (external files/folders from file manager)
    # ──────────────────────────────────────────────────────────────────────

    def _setup_upload_drop_target(self):
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        target.connect("drop", self._on_upload_drop)
        target.connect("enter", self._on_upload_drag_enter)
        target.connect("leave", self._on_upload_drag_leave)
        self._stack.add_controller(target)

    def _on_upload_drag_enter(self, target, x, y):
        if not self._window or not self._window.sftp_client:
            return 0
        self._stack.add_css_class("drop-target")
        return Gdk.DragAction.COPY

    def _on_upload_drag_leave(self, target):
        self._stack.remove_css_class("drop-target")

    def _on_upload_drop(self, target, value, x, y):
        self._stack.remove_css_class("drop-target")
        if not self._window or not self._window.sftp_client:
            return False
        files = value.get_files()
        if not files:
            return False
        paths = []
        for gfile in files:
            path = gfile.get_path()
            if path:
                paths.append(path)
        if paths:
            self._do_upload_paths(paths)
        return True
