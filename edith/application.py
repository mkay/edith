import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")

from pathlib import Path

from gi.repository import Adw, Gdk, Gio, Gtk, GtkSource

from edith import APP_ID, APP_NAME, VERSION
from edith.window import EdithWindow


class EdithApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._setup_actions()

    def do_activate(self):
        win = self.props.active_window
        if not win:
            # Load bundled icon resources and register with the icon theme
            gresource = Path(__file__).parent / "data" / "de.singular.edith.gresource"
            Gio.resources_register(Gio.resource_load(str(gresource)))
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_resource_path(
                "/de/singular/edith/icons/hicolor"
            )

            # Load application-wide CSS
            css = Gtk.CssProvider()
            css.load_from_string("""
.drop-target { background-color: alpha(@accent_color, 0.15); }

/* Slightly darker sidebar pane */
.app-sidebar { background-color: shade(@window_bg_color, 0.97); }


""")
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

            # Register bundled style schemes
            styles_dir = str(Path(__file__).parent / "data" / "styles")
            sm = GtkSource.StyleSchemeManager.get_default()
            search_path = list(sm.get_search_path())
            if styles_dir not in search_path:
                search_path.insert(0, styles_dir)
                sm.set_search_path(search_path)

            win = EdithWindow(application=self)
        win.present()

    def _setup_actions(self):
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

        shortcuts_action = Gio.SimpleAction.new("shortcuts", None)
        shortcuts_action.connect("activate", self._on_shortcuts)
        self.add_action(shortcuts_action)

        theme_action = Gio.SimpleAction.new("syntax-theme", None)
        theme_action.connect("activate", self._on_syntax_theme)
        self.add_action(theme_action)

        font_action = Gio.SimpleAction.new("editor-font", None)
        font_action.connect("activate", self._on_editor_font)
        self.add_action(font_action)

        window_size_action = Gio.SimpleAction.new("window-size", None)
        window_size_action.connect("activate", self._on_window_size)
        self.add_action(window_size_action)

        syntax_assoc_action = Gio.SimpleAction.new("syntax-associations", None)
        syntax_assoc_action.connect("activate", self._on_syntax_associations)
        self.add_action(syntax_assoc_action)

        new_window_action = Gio.SimpleAction.new("new-window", None)
        new_window_action.connect("activate", self._on_new_window)
        self.add_action(new_window_action)
        self.set_accels_for_action("app.new-window", ["<Control><Shift>n"])

    def _on_about(self, action, param):
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon="de.singular.edith-symbolic",
            version=VERSION,
            developer_name="Edith Contributors",
            website="https://github.com/mkay/edith",
            comments=(
                "GTK4 native SFTP client for live remote file editing.\n\n"
                "Edith is alpha software.\n\n"
                "Features\n"
                "• Server management — saved connections with password/key auth, "
                "organized into collapsible folder groups; change passwords after initial setup\n"
                "• Server search — Ctrl+F to filter servers by name\n"
                "• File browser — navigate remote directories with drag-and-drop move, "
                "upload, copy, rename, delete\n"
                "• Path bar — clickable breadcrumb navigation with back/forward history\n"
                "• Tabbed editor — GtkSourceView 5 with syntax highlighting, "
                "customizable themes and fonts\n"
                "• Secure credentials — passwords stored via GNOME Keyring / libsecret\n"
                "• Live editing — files downloaded to temp, edited locally, uploaded on save\n"
                "• Home directory support — use ~ as initial directory\n"
                "• Resizable sidebar"
            ),
            license_type=Gtk.License.GPL_3_0,
        )
        about.present(self.props.active_window)

    def _on_new_window(self, action, param):
        win = EdithWindow(application=self)
        win.present()

    def _on_syntax_theme(self, action, param):
        win = self.props.active_window
        if not win:
            return
        from edith.widgets.theme_chooser_dialog import ThemeChooserDialog
        dialog = ThemeChooserDialog()
        dialog.connect("scheme-changed", self._on_scheme_changed)
        dialog.present(win)

    def _on_scheme_changed(self, dialog, scheme_id):
        """Apply new scheme to all open editor pages."""
        win = self.props.active_window
        if not win:
            return
        win.apply_syntax_scheme(scheme_id)

    def _on_editor_font(self, action, param):
        win = self.props.active_window
        if not win:
            return
        from edith.widgets.font_chooser_dialog import FontChooserDialog
        dialog = FontChooserDialog()
        dialog.connect("font-changed", self._on_font_changed)
        dialog.present(win)

    def _on_font_changed(self, dialog, font_family, font_size):
        """Apply new font to all open editor pages."""
        win = self.props.active_window
        if not win:
            return
        win.apply_editor_font(font_family, font_size)

    def _on_window_size(self, action, param):
        win = self.props.active_window
        if not win:
            return

        from edith.services.config import ConfigService

        current_w = ConfigService.get_preference("window_width", 1100)
        current_h = ConfigService.get_preference("window_height", 700)

        dialog = Adw.Dialog(title="Window Size", content_width=320, content_height=220)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _: dialog.close())
        header.pack_start(cancel_btn)

        save_btn = Gtk.Button(label="Save", css_classes=["suggested-action"])
        header.pack_end(save_btn)
        toolbar_view.add_top_bar(header)

        clamp = Adw.Clamp(maximum_size=320, margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        group = Adw.PreferencesGroup()

        width_row = Adw.SpinRow(
            title="Width",
            adjustment=Gtk.Adjustment(value=current_w, lower=800, upper=3840, step_increment=10),
        )
        group.add(width_row)

        height_row = Adw.SpinRow(
            title="Height",
            adjustment=Gtk.Adjustment(value=current_h, lower=600, upper=2160, step_increment=10),
        )
        group.add(height_row)

        clamp.set_child(group)
        toolbar_view.set_content(clamp)
        dialog.set_child(toolbar_view)

        def on_save(_):
            w = int(width_row.get_value())
            h = int(height_row.get_value())
            ConfigService.set_preference("window_width", w)
            ConfigService.set_preference("window_height", h)
            dialog.close()

        save_btn.connect("clicked", on_save)
        dialog.present(win)

    def _on_syntax_associations(self, action, param):
        win = self.props.active_window
        if not win:
            return
        from edith.widgets.syntax_associations_dialog import SyntaxAssociationsDialog
        dialog = SyntaxAssociationsDialog()
        dialog.present(win)

    def _on_shortcuts(self, action, param):
        win = self.props.active_window
        if not win:
            return

        # Build shortcuts window programmatically
        shortcuts = [
            ("General", [
                ("<Control>q", "Quit"),
                ("<Control><Shift>n", "New Window"),
                ("F9", "Toggle sidebar"),
                ("<Control>n", "New server"),
            ]),
            ("Editing", [
                ("<Control>s", "Save file"),
                ("<Control>w", "Close tab"),
                ("<Control>f", "Find"),
                ("<Control><Shift>f", "Find and Replace"),
                ("<Control>g", "Go to Line"),
                ("<Control>z", "Undo"),
                ("<Control><Shift>z", "Redo"),
            ]),
            ("Connection", [
                ("<Control>d", "Disconnect"),
                ("<Control>f", "Search servers"),
            ]),
            ("File Browser", [
                ("F2", "Rename"),
                ("Delete", "Delete"),
                ("F5", "Refresh"),
                ("BackSpace", "Parent directory"),
            ]),
        ]

        sections_xml = ""
        for group_title, items in shortcuts:
            items_xml = ""
            for accel, title in items:
                accel_escaped = accel.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                items_xml += (
                    f'<child><object class="GtkShortcutsShortcut">'
                    f'<property name="accelerator">{accel_escaped}</property>'
                    f'<property name="title">{title}</property>'
                    f"</object></child>"
                )
            sections_xml += (
                f'<child><object class="GtkShortcutsGroup">'
                f'<property name="title">{group_title}</property>'
                f"{items_xml}</object></child>"
            )

        xml = (
            f'<interface><object class="GtkShortcutsWindow" id="shortcuts">'
            f'<child><object class="GtkShortcutsSection">'
            f'<property name="section-name">shortcuts</property>'
            f"{sections_xml}</object></child>"
            f"</object></interface>"
        )

        builder = Gtk.Builder.new_from_string(xml, -1)
        shortcuts_win = builder.get_object("shortcuts")
        shortcuts_win.set_transient_for(win)
        shortcuts_win.present()
