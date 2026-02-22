import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")

from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, GtkSource

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
            # Load application-wide CSS
            css = Gtk.CssProvider()
            css.load_from_string(".drop-target { background-color: alpha(@accent_color, 0.15); }")
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

    def _on_about(self, action, param):
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon="network-server-symbolic",
            version=VERSION,
            developer_name="Edith Contributors",
            comments="GTK4 native SFTP client for live remote file editing",
            license_type=Gtk.License.GPL_3_0,
        )
        about.present(self.props.active_window)

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

    def _on_shortcuts(self, action, param):
        win = self.props.active_window
        if not win:
            return

        builder = Gio.ListStore.new(Gio.ListModel)

        shortcuts_window = Gio.Application.get_default().props.active_window
        # Build shortcuts window programmatically
        shortcuts = [
            ("General", [
                ("<Control>q", "Quit"),
                ("F9", "Toggle sidebar"),
                ("<Control>n", "New server"),
            ]),
            ("Editing", [
                ("<Control>s", "Save file"),
                ("<Control>w", "Close tab"),
            ]),
            ("Connection", [
                ("<Control>d", "Disconnect"),
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

        from gi.repository import Gtk

        builder = Gtk.Builder.new_from_string(xml, -1)
        shortcuts_win = builder.get_object("shortcuts")
        shortcuts_win.set_transient_for(win)
        shortcuts_win.present()
