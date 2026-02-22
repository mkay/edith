"""Dialogs for file browser operations: name entry, chmod, file info, directory chooser."""

import stat
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk, GObject

from edith.models.remote_file import RemoteFileInfo


class NameDialog(Adw.Dialog):
    """Single text-entry dialog used for new file, new folder, and rename."""

    __gsignals__ = {
        "submitted": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, title: str, label: str, initial_text: str = ""):
        super().__init__(title=title, content_width=360, content_height=180)

        self._build_ui(label, initial_text)

    def _build_ui(self, label, initial_text):
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar(
            show_start_title_buttons=False, show_end_title_buttons=False
        )

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_btn)

        apply_btn = Gtk.Button(label="Apply", css_classes=["suggested-action"])
        apply_btn.connect("clicked", self._on_apply)
        header.pack_end(apply_btn)
        self._apply_btn = apply_btn

        toolbar_view.add_top_bar(header)

        clamp = Adw.Clamp(
            maximum_size=340,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )

        group = Adw.PreferencesGroup()
        self._entry = Adw.EntryRow(title=label)
        self._entry.set_text(initial_text)
        self._entry.connect("entry-activated", lambda _: self._on_apply(None))
        group.add(self._entry)

        clamp.set_child(group)
        toolbar_view.set_content(clamp)
        self.set_child(toolbar_view)

    def _on_apply(self, btn):
        text = self._entry.get_text().strip()
        if text:
            self.emit("submitted", text)
            self.close()


class ChmodDialog(Adw.Dialog):
    """Permission editor with a 3x3 checkbox grid and octal preview."""

    __gsignals__ = {
        "applied": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, current_mode: int):
        super().__init__(
            title="Change Permissions", content_width=360, content_height=340
        )

        self._mode = current_mode & 0o777
        self._checks = {}
        self._build_ui()

    def _build_ui(self):
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar(
            show_start_title_buttons=False, show_end_title_buttons=False
        )

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_btn)

        apply_btn = Gtk.Button(label="Apply", css_classes=["suggested-action"])
        apply_btn.connect("clicked", self._on_apply)
        header.pack_end(apply_btn)

        toolbar_view.add_top_bar(header)

        clamp = Adw.Clamp(
            maximum_size=340,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        # Permission grid
        grid = Gtk.Grid(row_spacing=8, column_spacing=12)

        # Column headers
        for col, label in enumerate(["Read", "Write", "Execute"], start=1):
            grid.attach(
                Gtk.Label(label=label, css_classes=["dim-label", "caption"]),
                col,
                0,
                1,
                1,
            )

        # Rows: Owner, Group, Other
        categories = [("Owner", 6), ("Group", 3), ("Other", 0)]
        perms = [("r", 2), ("w", 1), ("x", 0)]

        for row_idx, (cat_name, shift) in enumerate(categories, start=1):
            grid.attach(
                Gtk.Label(label=cat_name, xalign=0, hexpand=True), 0, row_idx, 1, 1
            )
            for col_idx, (perm_char, bit_offset) in enumerate(perms, start=1):
                bit = 1 << (shift + bit_offset)
                check = Gtk.CheckButton(active=bool(self._mode & bit))
                check.connect("toggled", self._on_check_toggled)
                self._checks[(cat_name, perm_char)] = (check, bit)
                grid.attach(check, col_idx, row_idx, 1, 1)

        box.append(grid)

        # Octal preview
        self._octal_label = Gtk.Label(
            label=f"{self._mode:04o}",
            css_classes=["title-3", "monospace"],
            margin_top=8,
        )
        box.append(self._octal_label)

        clamp.set_child(box)
        toolbar_view.set_content(clamp)
        self.set_child(toolbar_view)

    def _on_check_toggled(self, check):
        mode = 0
        for (cat, perm), (cb, bit) in self._checks.items():
            if cb.get_active():
                mode |= bit
        self._mode = mode
        self._octal_label.set_label(f"{self._mode:04o}")

    def _on_apply(self, btn):
        self.emit("applied", self._mode)
        self.close()


class FileInfoDialog(Adw.Dialog):
    """Read-only dialog displaying file properties."""

    def __init__(self, file_info: RemoteFileInfo):
        super().__init__(
            title="Properties", content_width=380, content_height=360
        )

        self._build_ui(file_info)

    def _build_ui(self, fi: RemoteFileInfo):
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar(
            show_start_title_buttons=False, show_end_title_buttons=False
        )

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda _: self.close())
        header.pack_end(close_btn)

        toolbar_view.add_top_bar(header)

        clamp = Adw.Clamp(
            maximum_size=360,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )

        group = Adw.PreferencesGroup()

        group.add(self._info_row("Name", fi.name))
        group.add(self._info_row("Path", fi.path))
        group.add(self._info_row("Type", "Directory" if fi.is_dir else "File"))

        if not fi.is_dir:
            group.add(self._info_row("Size", self._format_size(fi.size)))

        perm_bits = fi.permissions & 0o777
        symbolic = self._symbolic_permissions(perm_bits)
        group.add(self._info_row("Permissions", f"{symbolic}  ({perm_bits:04o})"))

        clamp.set_child(group)
        toolbar_view.set_content(clamp)
        self.set_child(toolbar_view)

    def _info_row(self, title: str, value: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=value)
        row.set_subtitle_selectable(True)
        return row

    @staticmethod
    def _format_size(size: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    @staticmethod
    def _symbolic_permissions(mode: int) -> str:
        parts = []
        for shift in (6, 3, 0):
            triplet = (mode >> shift) & 0o7
            parts.append(
                ("r" if triplet & 4 else "-")
                + ("w" if triplet & 2 else "-")
                + ("x" if triplet & 1 else "-")
            )
        return "".join(parts)


class DirectoryChooserDialog(Adw.Dialog):
    """Navigable remote directory browser for choosing a destination folder."""

    __gsignals__ = {
        "chosen": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, sftp_client, title: str = "Choose Destination", start_path: str = "/"):
        super().__init__(title=title, content_width=400, content_height=480)
        self._client = sftp_client
        self._current_path = start_path
        self._build_ui()
        self._load(start_path)

    def _build_ui(self):
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar(
            show_start_title_buttons=False, show_end_title_buttons=False,
        )

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_btn)

        self._select_btn = Gtk.Button(label="Select", css_classes=["suggested-action"])
        self._select_btn.connect("clicked", self._on_select)
        header.pack_end(self._select_btn)

        toolbar_view.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Path bar
        path_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            margin_start=8, margin_end=8, margin_top=6, margin_bottom=6,
        )
        self._path_label = Gtk.Label(
            label=self._current_path,
            xalign=0, hexpand=True, ellipsize=3,
            css_classes=["dim-label", "caption"],
        )
        path_box.append(self._path_label)

        up_btn = Gtk.Button(
            icon_name="go-up-symbolic",
            tooltip_text="Parent Directory",
            css_classes=["flat", "circular"],
        )
        up_btn.connect("clicked", self._on_go_up)
        path_box.append(up_btn)

        content.append(path_box)
        content.append(Gtk.Separator())

        # Directory list
        sw = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self._list_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            css_classes=["navigation-sidebar"],
        )
        self._list_box.connect("row-activated", self._on_row_activated)
        sw.set_child(self._list_box)
        content.append(sw)

        # Spinner
        self._spinner = Gtk.Spinner(spinning=False, visible=False, margin_top=16, margin_bottom=16)
        content.append(self._spinner)

        toolbar_view.set_content(content)
        self.set_child(toolbar_view)

    def _load(self, path: str):
        self._current_path = path
        self._path_label.set_label(path)
        self._spinner.set_visible(True)
        self._spinner.set_spinning(True)

        client = self._client

        def do_list():
            entries = client.listdir_attr(path)
            dirs = []
            for attr in entries:
                if attr.filename.startswith("."):
                    continue
                if attr.st_mode and stat.S_ISDIR(attr.st_mode):
                    dirs.append(attr.filename)
            dirs.sort(key=str.lower)
            return dirs

        def on_done(dirs):
            self._spinner.set_visible(False)
            self._spinner.set_spinning(False)
            self._populate(dirs)

        def on_error(e):
            self._spinner.set_visible(False)
            self._spinner.set_spinning(False)

        def worker():
            try:
                result = do_list()
                GLib.idle_add(on_done, result)
            except Exception as e:
                GLib.idle_add(on_error, e)

        threading.Thread(target=worker, daemon=True).start()

    def _populate(self, dir_names: list[str]):
        while True:
            row = self._list_box.get_row_at_index(0)
            if row is None:
                break
            self._list_box.remove(row)

        if not dir_names:
            empty = Gtk.Label(
                label="No subdirectories",
                css_classes=["dim-label"],
                margin_top=24,
            )
            self._list_box.append(empty)
            return

        for name in dir_names:
            row_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=6,
                margin_start=8, margin_end=8, margin_top=2, margin_bottom=2,
            )
            row_box.append(Gtk.Image(icon_name="folder-symbolic"))
            row_box.append(Gtk.Label(label=name, xalign=0, hexpand=True, ellipsize=3))
            self._list_box.append(row_box)

    def _on_row_activated(self, list_box, row):
        child = row.get_child()
        if not isinstance(child, Gtk.Box):
            return
        # Second child is the label
        label_widget = child.get_last_child()
        if not isinstance(label_widget, Gtk.Label):
            return
        name = label_widget.get_label()
        new_path = f"{self._current_path.rstrip('/')}/{name}"
        self._load(new_path)

    def _on_go_up(self, btn):
        if self._current_path == "/":
            return
        parent = "/".join(self._current_path.rstrip("/").split("/")[:-1])
        if not parent:
            parent = "/"
        self._load(parent)

    def _on_select(self, btn):
        self.emit("chosen", self._current_path)
        self.close()
