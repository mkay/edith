import gi
import os

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk, GObject

from edith.models.open_file import OpenFile
from edith.widgets.editor_page import EditorPage


class EditorPanel(Gtk.Box):
    """Tabbed editor panel using Adw.TabView."""

    __gsignals__ = {
        "page-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._window = None
        self._tabs = {}  # remote_path -> Adw.TabPage

        # Tab bar
        self._tab_view = Adw.TabView()
        self._tab_view.connect("close-page", self._on_close_page)
        self._tab_view.connect("notify::selected-page", lambda *_: self.emit("page-changed"))

        # Tab context menu
        self._setup_tab_menu()

        tab_bar = Adw.TabBar(view=self._tab_view, autohide=False)

        self.append(tab_bar)
        self.append(self._tab_view)

    def _setup_tab_menu(self):
        """Context menu for tab right-click, using TabView's built-in support."""
        menu = Gio.Menu()
        menu.append("Show in Sidebar", "tab.show-in-sidebar")

        self._tab_view.set_menu_model(menu)
        self._tab_view.connect("setup-menu", self._on_tab_setup_menu)

        group = Gio.SimpleActionGroup()

        show_action = Gio.SimpleAction.new("show-in-sidebar", None)
        show_action.connect("activate", self._on_show_in_sidebar)
        group.add_action(show_action)

        self.insert_action_group("tab", group)

        self._menu_page = None

    def _on_tab_setup_menu(self, tab_view, page):
        self._menu_page = page

    def _on_show_in_sidebar(self, action, param):
        page = self._menu_page
        if not page:
            return

        editor = page.get_child()
        if isinstance(editor, EditorPage) and self._window:
            self._window.reveal_in_sidebar(editor.open_file.remote_path)

    def set_window(self, window):
        self._window = window

    def open_file(self, remote_path: str, local_path: str):
        """Open a file in a new (or existing) editor tab."""
        if remote_path in self._tabs:
            self.focus_tab(remote_path)
            return

        open_file = OpenFile(remote_path=remote_path, local_path=local_path)
        editor = EditorPage(open_file)
        editor.connect("modified-changed", self._on_modified_changed)

        page = self._tab_view.append(editor)
        filename = os.path.basename(remote_path)
        page.set_title(filename)
        page.set_icon(None)
        page.set_tooltip(remote_path)

        self._tabs[remote_path] = page
        self._tab_view.set_selected_page(page)

    def find_tab(self, remote_path: str):
        """Return remote_path if tab exists, else None."""
        if remote_path in self._tabs:
            return remote_path
        return None

    def focus_tab(self, remote_path: str):
        """Switch to an existing tab."""
        page = self._tabs.get(remote_path)
        if page:
            self._tab_view.set_selected_page(page)

    def save_current(self):
        """Save the currently active tab."""
        page = self._tab_view.get_selected_page()
        if not page:
            return

        editor = page.get_child()
        if not isinstance(editor, EditorPage):
            return

        editor.save_to_disk()

        if self._window:
            self._window.save_remote_file(
                editor.open_file.remote_path,
                editor.open_file.local_path,
            )

    def close_current(self):
        """Close the currently active tab."""
        page = self._tab_view.get_selected_page()
        if page:
            self._tab_view.close_page(page)

    def close_all(self):
        """Close all tabs."""
        paths = list(self._tabs.keys())
        for path in paths:
            page = self._tabs.get(path)
            if page:
                self._tab_view.close_page_finish(page, True)
        self._tabs.clear()

    def _on_close_page(self, tab_view, page):
        editor = page.get_child()
        if isinstance(editor, EditorPage):
            remote_path = editor.open_file.remote_path

            if editor.open_file.is_modified:
                self._confirm_close(page, editor)
                return Gdk.EVENT_STOP if hasattr(Gdk, "EVENT_STOP") else True

            self._tabs.pop(remote_path, None)

        tab_view.close_page_finish(page, True)
        return True  # Inhibit default close, we handle it

    def _confirm_close(self, page, editor):
        """Ask user to save before closing a modified file."""
        win = self.get_root()

        dialog = Adw.AlertDialog(
            heading="Save Changes?",
            body=f"\u201c{editor.open_file.filename}\u201d has unsaved changes.",
        )
        dialog.add_response("discard", "Discard")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        dialog.connect("response", self._on_close_response, page, editor)
        dialog.present(win)

    def _on_close_response(self, dialog, response, page, editor):
        if response == "save":
            editor.save_to_disk()
            if self._window:
                self._window.save_remote_file(
                    editor.open_file.remote_path,
                    editor.open_file.local_path,
                )

        if response != "cancel":
            self._tabs.pop(editor.open_file.remote_path, None)
            self._tab_view.close_page_finish(page, True)
        else:
            self._tab_view.close_page_finish(page, False)

    def _on_modified_changed(self, editor, modified):
        remote_path = editor.open_file.remote_path
        page = self._tabs.get(remote_path)
        if page:
            filename = os.path.basename(remote_path)
            if modified:
                page.set_title(f"• {filename}")
            else:
                page.set_title(filename)

    def apply_syntax_scheme(self, scheme_id: str):
        """Apply a style scheme to all open editor tabs."""
        for i in range(self._tab_view.get_n_pages()):
            page = self._tab_view.get_nth_page(i)
            editor = page.get_child()
            if isinstance(editor, EditorPage):
                editor.apply_scheme(scheme_id)

    def apply_font(self, font_family: str, font_size: int):
        """Apply a font to all open editor tabs."""
        for i in range(self._tab_view.get_n_pages()):
            page = self._tab_view.get_nth_page(i)
            editor = page.get_child()
            if isinstance(editor, EditorPage):
                editor.apply_font(font_family, font_size)

    def has_unsaved(self) -> bool:
        """Return True if any open tab has unsaved changes."""
        for i in range(self._tab_view.get_n_pages()):
            page = self._tab_view.get_nth_page(i)
            editor = page.get_child()
            if isinstance(editor, EditorPage) and editor.open_file.is_modified:
                return True
        return False

    def unsaved_filenames(self) -> list[str]:
        """Return list of filenames with unsaved changes."""
        names = []
        for i in range(self._tab_view.get_n_pages()):
            page = self._tab_view.get_nth_page(i)
            editor = page.get_child()
            if isinstance(editor, EditorPage) and editor.open_file.is_modified:
                names.append(editor.open_file.filename)
        return names

    def get_current_editor(self):
        """Return the active EditorPage, or None."""
        page = self._tab_view.get_selected_page()
        if page:
            editor = page.get_child()
            if isinstance(editor, EditorPage):
                return editor
        return None

    @property
    def has_tabs(self) -> bool:
        return self._tab_view.get_n_pages() > 0
