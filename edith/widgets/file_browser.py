import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk, GObject, Gdk

from edith.models.remote_file import RemoteFileInfo
from edith.widgets.file_row import FileRow
from edith.widgets.file_dialogs import NameDialog, ChmodDialog, FileInfoDialog, DirectoryChooserDialog


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
        "file-activated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "path-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._window = None
        self._current_path = "/"
        self._expanded = set()  # set of expanded directory paths
        self._pending_reveal = None  # filename to select after loading
        self._history = []
        self._history_pos = -1
        self._show_hidden = False

        # Path bar — vertical: button row on top, path label below
        self._path_bar = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_start=8,
            margin_end=8,
            margin_top=4,
            margin_bottom=4,
        )

        _btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        self._up_btn = Gtk.Button(
            icon_name="edith-parent-dir-symbolic",
            tooltip_text="Parent Directory",
            css_classes=["flat", "circular"],
            sensitive=False,
        )
        self._up_btn.connect("clicked", self._on_go_up)
        _btn_row.append(self._up_btn)

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
            icon_name="view-reveal-symbolic",
            tooltip_text="Show Hidden Files",
            css_classes=["flat", "circular"],
        )
        self._hidden_btn.connect("toggled", self._on_show_hidden_toggled)
        _btn_row.append(self._hidden_btn)

        self._path_bar.append(_btn_row)

        self._path_label = Gtk.Label(
            label="/",
            xalign=0,
            hexpand=True,
            ellipsize=3,
            css_classes=["dim-label", "caption"],
            margin_bottom=2,
            tooltip_text="/",
        )
        self._path_bar.append(self._path_label)

        self.append(self._path_bar)
        self.append(Gtk.Separator())

        # File list
        sw = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )

        self._list_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            css_classes=["navigation-sidebar"],
            activate_on_single_click=False,
        )
        self._list_box.connect("row-activated", self._on_row_activated)

        sw.set_child(self._list_box)
        self.append(sw)

        # Loading spinner
        self._spinner = Gtk.Spinner(spinning=False, visible=False, margin_top=16, margin_bottom=16)
        self.append(self._spinner)

        # Drop target on path bar — drop here moves/copies into current directory
        self._setup_pathbar_drop_target()

        # Context menu
        self._setup_context_menu()

    def _setup_context_menu(self):
        """Right-click context menu for file rows and empty area."""
        menu = Gio.Menu()

        new_submenu = Gio.Menu()
        new_submenu.append("File\u2026", "file.new-file")
        new_submenu.append("Folder\u2026", "file.new-folder")

        upload_submenu = Gio.Menu()
        upload_submenu.append("File\u2026", "file.upload-files")
        upload_submenu.append("Folder\u2026", "file.upload-folder")

        section_new = Gio.Menu()
        section_new.append_submenu("New", new_submenu)
        section_new.append_submenu("Upload", upload_submenu)
        section_new.append("Download", "file.download")
        menu.append_section(None, section_new)

        section_actions = Gio.Menu()
        section_actions.append("Duplicate", "file.duplicate")
        section_actions.append("Move to\u2026", "file.move-to")
        section_actions.append("Copy to\u2026", "file.copy-to")
        section_actions.append("Rename\u2026", "file.rename")
        section_actions.append("Copy Path", "file.copy-path")
        section_actions.append("Change Permissions\u2026", "file.chmod")
        section_actions.append("Properties", "file.info")
        menu.append_section(None, section_actions)

        section_danger = Gio.Menu()
        section_danger.append("Delete", "file.delete")
        menu.append_section(None, section_danger)

        section_misc = Gio.Menu()
        section_misc.append("Refresh", "file.refresh")
        menu.append_section(None, section_misc)

        self._context_menu = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
        self._context_menu.set_parent(self._list_box)

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

        refresh_action = Gio.SimpleAction.new("refresh", None)
        refresh_action.connect("activate", lambda *_: self.load_directory(self._current_path))
        group.add_action(refresh_action)

        self._list_box.insert_action_group("file", group)

        # Gesture for right-click
        gesture = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        gesture.connect("pressed", self._on_right_click)
        self._list_box.add_controller(gesture)

        # Keyboard shortcuts
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self._list_box.add_controller(key_ctrl)

        self._context_row = None

    def _on_right_click(self, gesture, n_press, x, y):
        row = self._list_box.get_row_at_y(int(y))
        has_row = row is not None and isinstance(row.get_child(), FileRow)

        self._context_row = row if has_row else None

        self._rename_action.set_enabled(has_row)
        self._chmod_action.set_enabled(has_row)
        self._info_action.set_enabled(has_row)
        self._delete_action.set_enabled(has_row)
        self._duplicate_action.set_enabled(has_row)
        self._copy_path_action.set_enabled(has_row)
        self._move_to_action.set_enabled(has_row)
        self._copy_to_action.set_enabled(has_row)

        file_info = self._get_context_file_info()
        self._download_action.set_enabled(
            file_info is not None and not file_info.is_dir
        )

        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self._context_menu.set_pointing_to(rect)
        self._context_menu.popup()

    def _on_key_pressed(self, ctrl, keyval, keycode, state):
        selected = self._list_box.get_selected_row()

        if keyval == Gdk.KEY_F2:
            if selected and isinstance(selected.get_child(), FileRow):
                self._context_row = selected
                self._on_rename(None, None)
                return True

        elif keyval == Gdk.KEY_Delete:
            if selected and isinstance(selected.get_child(), FileRow):
                self._context_row = selected
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
        if self._context_row:
            child = self._context_row.get_child()
            if isinstance(child, FileRow):
                return child.file_info
        return None

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
            lambda e: self._show_error(str(e)),
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
            lambda e: self._show_error(str(e)),
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
            lambda e: self._show_error(str(e)),
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
        path = fi.path

        from edith.services.async_worker import run_async

        run_async(
            lambda: client.chmod(path, mode),
            lambda _: self.load_directory(self._current_path),
            lambda e: self._show_error(str(e)),
        )

    def _on_info(self, action, param):
        fi = self._get_context_file_info()
        if not fi:
            return

        dialog = FileInfoDialog(fi)
        dialog.present(self.get_root())

    def _on_delete(self, action, param):
        fi = self._get_context_file_info()
        if not fi:
            return

        kind = "folder" if fi.is_dir else "file"
        win = self.get_root()
        dlg = Adw.AlertDialog(
            heading=f"Delete {kind}?",
            body=f"Permanently delete \u201c{fi.name}\u201d?",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", self._do_delete, fi)
        dlg.present(win)

    def _do_delete(self, dialog, response, fi):
        if response != "delete":
            return
        if not self._window or not self._window.sftp_client:
            return

        client = self._window.sftp_client
        path = fi.path

        from edith.services.async_worker import run_async

        if fi.is_dir:
            run_async(
                lambda: client.rmdir_recursive(path),
                lambda _: self.load_directory(self._current_path),
                lambda e: self._show_error(str(e)),
            )
        else:
            run_async(
                lambda: client.remove(path),
                lambda _: self.load_directory(self._current_path),
                lambda e: self._show_error(str(e)),
            )

    def _on_duplicate(self, action, param):
        fi = self._get_context_file_info()
        if not fi:
            return
        if not self._window or not self._window.sftp_client:
            return

        client = self._window.sftp_client
        src = fi.path
        # Build "name (copy).ext" from the original name
        base = fi.name
        if not fi.is_dir and "." in base:
            stem, ext = base.rsplit(".", 1)
            dst_name = f"{stem} (copy).{ext}"
        else:
            dst_name = f"{base} (copy)"
        dst = f"{self._current_path.rstrip('/')}/{dst_name}"

        from edith.services.async_worker import run_async

        if fi.is_dir:
            run_async(
                lambda: client.copy_remote_recursive(src, dst),
                lambda _: self.load_directory(self._current_path),
                lambda e: self._show_error(str(e)),
            )
        else:
            run_async(
                lambda: client.copy_remote(src, dst),
                lambda _: self.load_directory(self._current_path),
                lambda e: self._show_error(str(e)),
            )

    def _on_copy_path(self, action, param):
        fi = self._get_context_file_info()
        if not fi:
            return

        clipboard = self.get_display().get_clipboard()
        clipboard.set(fi.path)

    def _on_move_to(self, action, param):
        fi = self._get_context_file_info()
        if not fi:
            return
        if not self._window or not self._window.sftp_client:
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

        run_async(
            lambda: client.rename(src, dst),
            lambda _: self.load_directory(self._current_path),
            lambda e: self._show_error(str(e)),
        )

    def _on_copy_to(self, action, param):
        fi = self._get_context_file_info()
        if not fi:
            return
        if not self._window or not self._window.sftp_client:
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
            run_async(
                lambda: client.copy_remote_recursive(src, dst),
                lambda _: self.load_directory(self._current_path),
                lambda e: self._show_error(str(e)),
            )
        else:
            run_async(
                lambda: client.copy_remote(src, dst),
                lambda _: self.load_directory(self._current_path),
                lambda e: self._show_error(str(e)),
            )

    def reveal_file(self, remote_path: str):
        """Navigate to the parent directory of remote_path and select its row."""
        parent = "/".join(remote_path.rstrip("/").split("/")[:-1]) or "/"
        filename = remote_path.rstrip("/").rsplit("/", 1)[-1]
        self._pending_reveal = filename
        self.load_directory(parent)

    def set_window(self, window):
        """Set reference to main window for SFTP access."""
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

    def load_directory(self, path: str, add_to_history: bool = True):
        """Load directory contents asynchronously."""
        if not self._window or not self._window.sftp_client:
            return

        if add_to_history:
            if not self._history or path != self._history[self._history_pos]:
                self._history = self._history[:self._history_pos + 1]
                self._history.append(path)
                self._history_pos += 1

        self._current_path = path
        self._path_label.set_label(_path_display(path))
        self._path_label.set_tooltip_text(path)
        self._up_btn.set_sensitive(path != "/")
        self.emit("path-changed", path)

        # Show loading state
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
            # Sort: directories first, then alphabetical
            files.sort(key=lambda f: (not f.is_dir, f.name.lower()))
            return files

        def on_success(files):
            self._spinner.set_visible(False)
            self._spinner.set_spinning(False)
            self._populate(files)

        def on_error(error):
            self._spinner.set_visible(False)
            self._spinner.set_spinning(False)
            self._show_error(str(error))

        run_async(do_list, on_success, on_error)

    def _populate(self, files: list[RemoteFileInfo]):
        """Populate the list with file entries."""
        # Clear existing
        while True:
            row = self._list_box.get_row_at_index(0)
            if row is None:
                break
            self._list_box.remove(row)

        # Parent directory shortcut
        if self._current_path != "/":
            parent_row = Gtk.ListBoxRow()
            parent_row.is_parent_dir = True
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                          margin_start=8, margin_end=8, margin_top=2, margin_bottom=2)
            box.append(Gtk.Label(label="..", xalign=0))
            parent_row.set_child(box)
            self._list_box.append(parent_row)

        if not files:
            empty = Gtk.Label(
                label="Empty directory",
                css_classes=["dim-label"],
                margin_top=24,
            )
            self._list_box.append(empty)
            return

        for file_info in files:
            row = FileRow(file_info)
            self._list_box.append(row)

            # Drag source on every file row
            listbox_row = row.get_parent()
            if listbox_row:
                self._attach_drag_source(listbox_row, file_info)
                # Drop target on directory rows
                if file_info.is_dir:
                    self._attach_dir_drop_target(listbox_row, file_info)

        # Select a row if reveal was requested
        if self._pending_reveal:
            target = self._pending_reveal
            self._pending_reveal = None
            idx = 0
            while True:
                row = self._list_box.get_row_at_index(idx)
                if row is None:
                    break
                child = row.get_child()
                if isinstance(child, FileRow) and child.file_info.name == target:
                    self._list_box.select_row(row)
                    break
                idx += 1

    def _show_error(self, message: str):
        while True:
            row = self._list_box.get_row_at_index(0)
            if row is None:
                break
            self._list_box.remove(row)

        error_label = Gtk.Label(
            label=f"Error: {message}",
            css_classes=["dim-label", "error"],
            margin_top=24,
            wrap=True,
        )
        self._list_box.append(error_label)

    def _on_row_activated(self, list_box, row):
        self._activate_row(row)

    def _activate_row(self, row):
        if getattr(row, "is_parent_dir", False):
            self._on_go_up(None)
            return
        child = row.get_child()
        if isinstance(child, FileRow):
            if child.file_info.is_dir:
                self.load_directory(child.file_info.path)
            else:
                self.emit("file-activated", child.file_info.path)

    def _on_show_hidden_toggled(self, btn):
        self._show_hidden = btn.get_active()
        self.load_directory(self._current_path)

    def _on_go_up(self, btn):
        if self._current_path == "/":
            return
        parent = "/".join(self._current_path.rstrip("/").split("/")[:-1])
        if not parent:
            parent = "/"
        self.load_directory(parent)

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def _on_upload_clicked(self, btn):
        if not self._window or not self._window.sftp_client:
            return

        dialog = Gtk.FileDialog(title="Upload Files")
        dialog.open_multiple(self.get_root(), None, self._on_upload_files_selected)

    def _on_upload_files_selected(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
        except Exception:
            return  # user cancelled

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
            return  # user cancelled

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
        if not info or info.is_dir:
            return
        dialog = Gtk.FileDialog(title="Save File", initial_name=info.name)
        dialog.save(self.get_root(), None, self._on_download_save_chosen)

    def _on_download_save_chosen(self, dialog, result):
        try:
            file = dialog.save_finish(result)
        except Exception:
            return  # user cancelled

        local_path = file.get_path()
        if not local_path:
            return

        info = self._get_context_file_info()
        if not info or not self._window:
            return

        self._window.enqueue_download(info.path, local_path)

    # ------------------------------------------------------------------
    # Drag and drop — move (default) / copy (Ctrl held)
    # ------------------------------------------------------------------

    def _attach_drag_source(self, listbox_row, file_info):
        """Attach a DragSource to a file row for move."""
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_drag_prepare, file_info)
        source.connect("drag-begin", self._on_drag_begin, file_info)
        listbox_row.add_controller(source)

    def _on_drag_prepare(self, source, x, y, file_info):
        value = GObject.Value(GObject.TYPE_STRING, file_info.path)
        return Gdk.ContentProvider.new_for_value(value)

    def _on_drag_begin(self, source, drag, file_info):
        icon_name = file_info.icon_name
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(Gtk.Image(icon_name=icon_name))
        box.append(Gtk.Label(label=file_info.name))
        drag_icon = Gtk.DragIcon.get_for_drag(drag)
        drag_icon.set_child(box)

    def _attach_dir_drop_target(self, listbox_row, dir_info):
        """Attach a DropTarget to a directory row to receive files."""
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("drop", self._on_dir_drop, dir_info)
        target.connect("enter", self._on_dir_drag_enter, listbox_row)
        target.connect("leave", self._on_dir_drag_leave, listbox_row)
        listbox_row.add_controller(target)

    def _on_dir_drag_enter(self, target, x, y, listbox_row):
        listbox_row.add_css_class("drop-target")
        return Gdk.DragAction.MOVE

    def _on_dir_drag_leave(self, target, listbox_row):
        listbox_row.remove_css_class("drop-target")

    def _on_dir_drop(self, target, value, x, y, dir_info):
        src_path = value
        dest_dir = dir_info.path
        self._perform_drag_move(src_path, dest_dir)
        return True

    def _setup_pathbar_drop_target(self):
        """Drop target on path bar to move into the current directory."""
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
        src_path = value
        dest_dir = self._current_path
        self._perform_drag_move(src_path, dest_dir)
        return True

    def _perform_drag_move(self, src_path, dest_dir):
        """Move src_path into dest_dir via rename."""
        if not self._window or not self._window.sftp_client:
            return

        name = src_path.rstrip("/").rsplit("/", 1)[-1]
        dst_path = f"{dest_dir.rstrip('/')}/{name}"

        # Don't drop onto own parent (no-op) or onto itself
        src_parent = "/".join(src_path.rstrip("/").split("/")[:-1]) or "/"
        if src_parent == dest_dir.rstrip("/") or src_parent == dest_dir:
            return
        if src_path.rstrip("/") == dest_dir.rstrip("/"):
            return
        # Don't drop a folder into its own subtree
        if dest_dir.rstrip("/").startswith(src_path.rstrip("/") + "/"):
            return

        client = self._window.sftp_client

        from edith.services.async_worker import run_async

        run_async(
            lambda: client.rename(src_path, dst_path),
            lambda _: self.load_directory(self._current_path),
            lambda e: self._show_error(str(e)),
        )
