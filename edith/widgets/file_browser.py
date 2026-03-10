import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk, GObject, Gdk, Pango

from edith.models.remote_file import RemoteFileInfo, RemoteFileItem
from edith.widgets.file_dialogs import NameDialog, ChmodDialog, FileInfoDialog, DirectoryChooserDialog, ArchiveDialog, InformationDialog


def _path_display(path: str) -> str:
    """Format path for the sidebar label: last 3 segments, '…' prefix if deeper."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "/"
    if len(parts) <= 3:
        return "/ " + " / ".join(parts)
    return "… / " + " / ".join(parts[-3:])


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
        self._select_mode = False
        self._updating_select_all = False
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

        self._select_btn = Gtk.ToggleButton(
            icon_name="edith-select-items-symbolic",
            tooltip_text="Select Items",
            css_classes=["flat", "circular"],
        )
        self._select_btn.connect("toggled", self._on_select_mode_toggled)
        _btn_row.append(self._select_btn)

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

        self._path_label = Gtk.Label(
            label="/",
            xalign=0,
            hexpand=True,
            ellipsize=3,
            css_classes=["dim-label", "caption"],
            margin_start=12,
            margin_end=8,
            margin_bottom=4,
            tooltip_text="/",
        )
        self.append(self._path_label)

        # ── Multi-select action bar ──────────────────────────────────────
        self._multi_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            margin_start=8, margin_end=8, margin_top=4, margin_bottom=4,
            visible=False,
        )
        self._select_all_check = Gtk.CheckButton(
            tooltip_text="Select All / None",
            can_focus=False,
        )
        self._select_all_check.connect("toggled", self._on_select_all_toggled)
        self._multi_bar.append(self._select_all_check)

        self._multi_count_label = Gtk.Label(
            label="", hexpand=True, xalign=0,
            css_classes=["dim-label", "caption"],
        )
        self._multi_bar.append(self._multi_count_label)

        self._multi_archive_btn = Gtk.Button(label="Archive", css_classes=["flat"], sensitive=False)
        self._multi_archive_btn.connect("clicked", self._on_bulk_archive)
        self._multi_bar.append(self._multi_archive_btn)

        self._multi_download_btn = Gtk.Button(label="Download", css_classes=["flat"], sensitive=False)
        self._multi_download_btn.connect("clicked", self._on_bulk_download)
        self._multi_bar.append(self._multi_download_btn)

        self._multi_copy_btn = Gtk.Button(label="Copy", css_classes=["flat"], sensitive=False)
        self._multi_copy_btn.connect("clicked", self._on_bulk_copy_to)
        self._multi_bar.append(self._multi_copy_btn)

        self._multi_move_btn = Gtk.Button(label="Move", css_classes=["flat"], sensitive=False)
        self._multi_move_btn.connect("clicked", self._on_bulk_move_to)
        self._multi_bar.append(self._multi_move_btn)

        self._multi_delete_btn = Gtk.Button(
            label="Delete", css_classes=["flat", "destructive-action"], sensitive=False,
        )
        self._multi_delete_btn.connect("clicked", self._on_bulk_delete)
        self._multi_bar.append(self._multi_delete_btn)

        self.append(self._multi_bar)

        # ── Column view model chain ──────────────────────────────────────
        self._store = Gio.ListStore(item_type=RemoteFileItem)

        # Column view — created before its sorter is retrieved
        self._column_view = Gtk.ColumnView(
            single_click_activate=False,
            show_row_separators=False,
            show_column_separators=False,
            css_classes=["data-table"],
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

        self._selection = Gtk.SingleSelection(model=self._filter_model, autoselect=False)
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

        # ── Context menu ─────────────────────────────────────────────────
        self._setup_context_menu()

        # ── Capture-phase key handler (select-mode shortcuts) ────────────
        _capture_keys = Gtk.EventControllerKey()
        _capture_keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        _capture_keys.connect("key-pressed", self._on_select_mode_key)
        self.add_controller(_capture_keys)

        # ── CSS ──────────────────────────────────────────────────────────
        _css = Gtk.CssProvider()
        _css.load_from_string("""
            columnview.data-table > listview > row.multi-checked {
                background-color: alpha(@accent_bg_color, 0.25);
            }
            columnview.data-table > listview > row.multi-checked:hover {
                background-color: alpha(@accent_bg_color, 0.35);
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            _css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    # ──────────────────────────────────────────────────────────────────────
    # Column setup
    # ──────────────────────────────────────────────────────────────────────

    def _setup_columns(self):
        # Checkbox column — visible only in select mode
        check_factory = Gtk.SignalListItemFactory()
        check_factory.connect("setup", self._setup_check_cell)
        check_factory.connect("bind", self._bind_check_cell)
        check_factory.connect("unbind", self._unbind_check_cell)
        self._check_column = Gtk.ColumnViewColumn(title="", factory=check_factory)
        self._check_column.set_fixed_width(48)
        self._check_column.set_visible(False)
        self._column_view.append_column(self._check_column)

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

    # ── Checkbox column factory ──────────────────────────────────────────

    def _setup_check_cell(self, factory, list_item):
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            margin_start=4, margin_end=4,
        )
        check = Gtk.CheckButton(can_focus=False)
        box.append(check)
        list_item.set_child(box)

    def _bind_check_cell(self, factory, list_item):
        item = list_item.get_item()
        box = list_item.get_child()
        box._cv_item = item
        check = box.get_first_child()

        if item.file_info.is_parent_dir:
            check.set_visible(False)
            return

        check.set_visible(True)
        binding = item.bind_property(
            "selected", check, "active",
            GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL,
        )
        handler = item.connect("notify::selected", lambda *_: self._update_multi_bar())
        list_item._cv_binding = binding
        list_item._cv_handler = handler
        list_item._cv_item = item

    def _unbind_check_cell(self, factory, list_item):
        if hasattr(list_item, "_cv_binding"):
            list_item._cv_binding.unbind()
            del list_item._cv_binding
        if hasattr(list_item, "_cv_item") and hasattr(list_item, "_cv_handler"):
            list_item._cv_item.disconnect(list_item._cv_handler)
            del list_item._cv_handler
            del list_item._cv_item
        box = list_item.get_child()
        if box:
            box._cv_item = None

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

        # Left click: toggle selection in select mode; double-click navigates
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def on_left_press(g, n, x, y):
            if not self._select_mode or not item_ref[0]:
                return
            if item_ref[0].file_info.is_parent_dir:
                return  # always navigate, never select
            if n >= 2:
                fi = item_ref[0].file_info
                if fi.is_dir:
                    self.load_directory(fi.path)
                else:
                    self.emit("file-activated", fi.path)
            else:
                item_ref[0].selected = not item_ref[0].selected
            g.set_state(Gtk.EventSequenceState.CLAIMED)

        click.connect("pressed", on_left_press)
        box.add_controller(click)

        # Drag source
        drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)

        def on_drag_prepare(d, x, y):
            if item_ref[0]:
                return Gdk.ContentProvider.new_for_value(
                    GObject.Value(GObject.TYPE_STRING, item_ref[0].file_info.path)
                )
            return None

        def on_drag_begin(d, gdk_drag):
            if item_ref[0]:
                fi = item_ref[0].file_info
                drag_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                drag_box.append(Gtk.Image(icon_name=fi.icon_name, pixel_size=16))
                drag_box.append(Gtk.Label(label=fi.name))
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
                self._perform_drag_move(value, item_ref[0].file_info.path)
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
        list_item._name_is_dir_ref[0] = fi.is_dir and not fi.is_parent_dir

        box = list_item.get_child()
        box._cv_item = item

        icon = box.get_first_child()
        label = icon.get_next_sibling()

        if fi.is_parent_dir:
            icon.set_visible(False)
            label.set_text("..")
            box.set_tooltip_text(None)
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

        # Information
        section_info = Gio.Menu()
        section_info.append("Information", "file.information")
        menu.append_section(None, section_info)

        # Delete, Actions submenu, Upload submenu, Rename, etc.
        actions_submenu = Gio.Menu()
        actions_submenu.append("Move to", "file.move-to")
        actions_submenu.append("Copy to", "file.copy-to")
        actions_submenu.append("Duplicate", "file.duplicate")
        actions_submenu.append("Download", "file.download")
        actions_submenu.append("Open Locally", "file.open-locally")

        upload_submenu = Gio.Menu()
        upload_submenu.append("File", "file.upload-files")
        upload_submenu.append("Folder", "file.upload-folder")

        section_ops = Gio.Menu()
        section_ops.append("Delete", "file.delete")
        section_ops.append_submenu("Actions", actions_submenu)
        section_ops.append_submenu("Upload", upload_submenu)
        section_ops.append("Rename", "file.rename")
        section_ops.append("Copy Path", "file.copy-path")
        section_ops.append("Create Archive", "file.create-archive")
        section_ops.append("Pin", "file.pin")
        menu.append_section(None, section_ops)

        # Refresh
        section_misc = Gio.Menu()
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

        upload_files_action = Gio.SimpleAction.new("upload-files", None)
        upload_files_action.connect("activate", lambda *_: self._on_upload_clicked(None))
        group.add_action(upload_files_action)

        upload_folder_action = Gio.SimpleAction.new("upload-folder", None)
        upload_folder_action.connect("activate", lambda *_: self._on_upload_folder())
        group.add_action(upload_folder_action)

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

        self._information_action.set_enabled(has_item)
        self._rename_action.set_enabled(has_item)
        self._delete_action.set_enabled(has_item)
        self._duplicate_action.set_enabled(has_item)
        self._copy_path_action.set_enabled(has_item)
        self._move_to_action.set_enabled(has_item)
        self._copy_to_action.set_enabled(has_item)
        self._pin_action.set_enabled(has_item)

        fi = item.file_info if item else None
        self._download_action.set_enabled(fi is not None)
        self._open_locally_action.set_enabled(fi is not None and not fi.is_dir)

        # Archive: only for SFTP connections, when item is readable and
        # current directory is writable.  Directories require exec support
        # (server-side tar/zip) since SFTP-only download+reupload is not
        # viable for large trees.
        archive_ok = False
        if has_item and fi and self._window:
            from edith.services.sftp_client import SftpClient
            client = self._window.sftp_client
            if isinstance(client, SftpClient):
                item_readable = bool(fi.permissions & 0o444)
                cur_dir_writable = self._cur_dir_writable
                archive_ok = item_readable and cur_dir_writable
                if fi.is_dir and not getattr(client, "can_exec", False):
                    archive_ok = False
        self._archive_action.set_enabled(archive_ok)

        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self._context_menu.set_pointing_to(rect)
        self._context_menu.popup()

    def _on_key_pressed(self, ctrl, keyval, keycode, state):
        selected = self._selection.get_selected_item()

        if keyval == Gdk.KEY_F2:
            if selected:
                self._context_item = selected
                self._on_rename(None, None)
                return True

        elif keyval == Gdk.KEY_Delete:
            if self._select_mode:
                if self._get_selected_file_infos():
                    self._on_bulk_delete()
                return True
            else:
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

    def _get_context_file_info(self) -> RemoteFileInfo | None:
        if self._context_item:
            return self._context_item.file_info
        return None

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
        fi = self._get_context_file_info()
        if not fi:
            return
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
        fi = self._get_context_file_info()
        if not fi or not self._window or not self._window.sftp_client:
            return
        client = self._window.sftp_client
        src = fi.path
        base = fi.name
        if not fi.is_dir and "." in base:
            stem, ext = base.rsplit(".", 1)
            dst_name = f"{stem} (copy).{ext}"
        else:
            dst_name = f"{base} (copy)"
        dst = f"{self._current_path.rstrip('/')}/{dst_name}"
        from edith.services.async_worker import run_async
        if fi.is_dir:
            run_async(lambda: client.copy_remote_recursive(src, dst),
                      lambda _: self.load_directory(self._current_path),
                      lambda e: self._show_op_error(str(e)))
        else:
            run_async(lambda: client.copy_remote(src, dst),
                      lambda _: self.load_directory(self._current_path),
                      lambda e: self._show_op_error(str(e)))

    def _on_copy_path(self, action, param):
        fi = self._get_context_file_info()
        if not fi:
            return
        self.get_display().get_clipboard().set(fi.path)

    def _on_move_to(self, action, param):
        fi = self._get_context_file_info()
        if not fi or not self._window or not self._window.sftp_client:
            return
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
        fi = self._get_context_file_info()
        if not fi or not self._window or not self._window.sftp_client:
            return
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
    # Directory loading
    # ──────────────────────────────────────────────────────────────────────

    def load_directory(self, path: str, add_to_history: bool = True):
        if not self._window or not self._window.sftp_client:
            return

        if self._select_mode:
            self._exit_select_mode()

        if add_to_history:
            if not self._history or path != self._history[self._history_pos]:
                self._history = self._history[:self._history_pos + 1]
                self._history.append(path)
                self._history_pos += 1

        self._current_path = path
        self._path_label.set_label(_path_display(path))
        self._path_label.set_tooltip_text(path)
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
                    self._selection.set_selected(i)
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

    # ──────────────────────────────────────────────────────────────────────
    # Multi-select mode
    # ──────────────────────────────────────────────────────────────────────

    def _on_select_mode_key(self, ctrl, keyval, keycode, state):
        if not self._select_mode:
            return False
        if keyval == Gdk.KEY_Escape:
            self._exit_select_mode()
            return True
        if keyval == Gdk.KEY_a and state & Gdk.ModifierType.CONTROL_MASK:
            if not self._filter_entry.has_focus():
                self._select_all_rows()
                return True
        return False

    def _exit_select_mode(self):
        if self._select_mode:
            self._select_btn.set_active(False)

    def _on_select_mode_toggled(self, btn):
        self._select_mode = btn.get_active()
        btn.set_icon_name(
            "edith-select-items-active-symbolic" if self._select_mode
            else "edith-select-items-symbolic"
        )
        if self._select_mode:
            self._multi_bar.set_visible(True)
            self._multi_count_label.set_label("No items selected")
            self._multi_delete_btn.set_sensitive(False)
            self._multi_move_btn.set_sensitive(False)
            self._multi_download_btn.set_sensitive(False)
            self._check_column.set_visible(True)
        else:
            self._clear_checked_rows()
            self._check_column.set_visible(False)
            self._multi_bar.set_visible(False)

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

    def _set_checkboxes_visible(self, visible: bool):
        self._check_column.set_visible(visible)

    def _on_select_all_toggled(self, btn):
        if self._updating_select_all:
            return
        if btn.get_active():
            self._select_all_rows()
        else:
            self._clear_checked_rows()

    def _select_all_rows(self):
        for item in self._items:
            item.selected = True
        self._update_multi_bar()

    def _clear_checked_rows(self):
        for item in self._items:
            item.selected = False
        self._update_multi_bar()

    def _update_multi_bar(self):
        infos = self._get_selected_file_infos()
        n = len(infos)
        total = len([i for i in self._items if not i.file_info.is_parent_dir])
        if n == 0:
            self._multi_count_label.set_label("")
        elif n == 1:
            self._multi_count_label.set_label("1 selected")
        else:
            self._multi_count_label.set_label(f"{n} selected")
        has_sel = n > 0
        self._multi_delete_btn.set_sensitive(has_sel)
        self._multi_move_btn.set_sensitive(has_sel)
        self._multi_copy_btn.set_sensitive(has_sel)
        self._multi_download_btn.set_sensitive(has_sel)
        # Archive: exec handles anything; without exec, only files are viable
        has_exec = self._window is not None and getattr(
            self._window.sftp_client, "can_exec", False)
        all_files = has_sel and not any(fi.is_dir for fi in infos)
        can_archive = has_sel and (has_exec or all_files)
        self._multi_archive_btn.set_sensitive(can_archive)
        self._multi_archive_btn.set_visible(has_exec or all_files)

        # Sync select-all checkbox state
        self._updating_select_all = True
        if n == 0:
            self._select_all_check.set_inconsistent(False)
            self._select_all_check.set_active(False)
        elif n >= total and total > 0:
            self._select_all_check.set_inconsistent(False)
            self._select_all_check.set_active(True)
        else:
            self._select_all_check.set_inconsistent(True)
        self._updating_select_all = False

    def _get_selected_file_infos(self) -> list:
        return [item.file_info for item in self._items if item.selected]

    def _on_bulk_delete(self, btn=None):
        infos = self._get_selected_file_infos()
        if not infos:
            return
        n = len(infos)
        dlg = Adw.AlertDialog(
            heading=f"Delete {n} item{'s' if n != 1 else ''}?",
            body="This will permanently delete the selected files and folders.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", f"Delete {n} Item{'s' if n != 1 else ''}")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", self._do_bulk_delete, infos)
        dlg.present(self.get_root())

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

    def _on_bulk_move_to(self, btn=None):
        infos = self._get_selected_file_infos()
        if not infos or not self._window or not self._window.sftp_client:
            return
        dialog = DirectoryChooserDialog(
            self._window.sftp_client,
            title=f"Move {len(infos)} items to\u2026",
            start_path=self._current_path,
        )
        dialog.connect("chosen", self._do_bulk_move_to, infos)
        dialog.present(self.get_root())

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

    def _on_bulk_download(self, btn=None):
        infos = self._get_selected_file_infos()
        if not infos:
            return
        dialog = Gtk.FileDialog(title=f"Download {len(infos)} item{'s' if len(infos) != 1 else ''} to\u2026")
        dialog.select_folder(self.get_root(), None,
                             lambda d, r: self._on_bulk_download_folder(d, r, infos))

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

    def _on_bulk_copy_to(self, btn=None):
        infos = self._get_selected_file_infos()
        if not infos or not self._window or not self._window.sftp_client:
            return
        dialog = DirectoryChooserDialog(
            self._window.sftp_client,
            title=f"Copy {len(infos)} items",
            start_path=self._current_path,
        )
        dialog.connect("chosen", self._do_bulk_copy_to, infos)
        dialog.present(self.get_root())

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

    def _on_bulk_archive(self, btn=None):
        infos = self._get_selected_file_infos()
        if not infos or not self._window or not self._window.sftp_client:
            return
        dialog = ArchiveDialog("archive")
        dialog.connect("submitted", self._do_bulk_archive, infos)
        dialog.present(self.get_root())

    def _do_bulk_archive(self, dialog, archive_name, infos):
        if not self._window or not self._window.sftp_client:
            return
        client = self._window.sftp_client
        import shlex

        cur_dir = self._current_path

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

        def _exec_archive(progress_cb):
            parent_dir = shlex.quote(cur_dir)
            dst = shlex.quote(remote_archive)
            src_names = " ".join(shlex.quote(fi.name) for fi in infos)

            if archive_fmt == "zip":
                cmd = f"cd {parent_dir} && zip -qr {dst} {src_names}"
            else:
                tar_flags = {
                    "tar.gz": "czf", "tar.bz2": "cjf",
                    "tar.xz": "cJf", "tar": "cf",
                }
                cmd = f"tar {tar_flags[archive_fmt]} {dst} -C {parent_dir} {src_names}"

            exit_code, _stdout, stderr = client.exec_command(cmd, timeout=600)
            if exit_code != 0:
                raise RuntimeError(stderr.strip() or f"exit code {exit_code}")
            return final_name

        def _fallback_files(progress_cb):
            """Download files, compress locally, upload back."""
            import tarfile
            import zipfile
            import tempfile
            import shutil

            tmp_dir = tempfile.mkdtemp(prefix="edith-archive-")
            try:
                for fi in infos:
                    local_path = os.path.join(tmp_dir, fi.name)
                    client.download(fi.path, local_path,
                                    progress_cb=lambda d, t: progress_cb(0, 0))

                archive_local = os.path.join(tmp_dir, final_name)
                if archive_mode == "zip":
                    with zipfile.ZipFile(archive_local, "w", zipfile.ZIP_DEFLATED) as zf:
                        for fi in infos:
                            zf.write(os.path.join(tmp_dir, fi.name), fi.name)
                else:
                    with tarfile.open(archive_local, archive_mode) as tf:
                        for fi in infos:
                            tf.add(os.path.join(tmp_dir, fi.name), arcname=fi.name)

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

            # Try exec first, fall back to local for files-only
            try:
                return _exec_archive(progress_cb)
            except Exception:
                try:
                    client.stat(remote_archive)
                    client.remove(remote_archive)
                except (FileNotFoundError, OSError):
                    pass

            return _fallback_files(progress_cb)

        def on_success(name):
            self.load_directory(cur_dir)
            if self._window:
                self._window.show_toast(f"Created \u201c{name}\u201d", "success")

        queue.enqueue(
            f"Archive {len(infos)} items",
            do_archive,
            on_success,
            lambda e: self._show_op_error(str(e)),
        )

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

    def _on_upload_folder(self):
        if not self._window or not self._window.sftp_client:
            return
        dialog = Gtk.FileDialog(title="Upload Folder")
        dialog.select_folder(self.get_root(), None, self._on_upload_folder_selected)

    def _on_upload_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except Exception:
            return
        if not folder or not self._window or not self._window.sftp_client:
            return
        local_path = folder.get_path()
        if local_path:
            self._do_upload_paths([local_path])

    def _do_upload_paths(self, local_paths):
        dest_dir = self._current_path
        for local_path in local_paths:
            name = os.path.basename(local_path)
            remote_path = f"{dest_dir.rstrip('/')}/{name}"
            self._window.enqueue_upload(
                local_path,
                remote_path,
                on_done=lambda _: self.load_directory(dest_dir),
            )

    def _on_download(self, action, param):
        info = self._get_context_file_info()
        if not info:
            return
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

    def _perform_drag_move(self, src_path, dest_dir):
        if not self._window or not self._window.sftp_client:
            return
        name = src_path.rstrip("/").rsplit("/", 1)[-1]
        dst_path = f"{dest_dir.rstrip('/')}/{name}"
        src_parent = "/".join(src_path.rstrip("/").split("/")[:-1]) or "/"
        if src_parent == dest_dir.rstrip("/") or src_parent == dest_dir:
            return
        if src_path.rstrip("/") == dest_dir.rstrip("/"):
            return
        if dest_dir.rstrip("/").startswith(src_path.rstrip("/") + "/"):
            return
        client = self._window.sftp_client
        from edith.services.async_worker import run_async
        dest_label = dest_dir.rstrip("/").rsplit("/", 1)[-1] or dest_dir

        def on_drag_moved(_):
            self.load_directory(self._current_path)
            if self._window:
                self._window.show_toast(f"Moved \u201c{name}\u201d to \u201c{dest_label}\u201d", "success")

        run_async(lambda: client.rename(src_path, dst_path), on_drag_moved,
                  lambda e: self._show_op_error(str(e)))
