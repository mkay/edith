import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from pathlib import Path

from gi.repository import Adw, Gdk, Gio, Gtk

from edith import APP_ID, APP_NAME, VERSION
from edith.window import EdithWindow


class EdithApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self._setup_actions()

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]  # skip argv[0]
        if args and not self.props.active_window:
            from edith.services.config import ConfigService
            servers_path = args[0]
            # Resolve relative paths against the caller's cwd
            if not Path(servers_path).is_absolute():
                cwd = command_line.get_cwd()
                if cwd:
                    servers_path = str(Path(cwd) / servers_path)
            ConfigService.set_servers_file(servers_path)
        self.activate()
        return 0

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

/* Alternating row shading */
columnview > listview > row:nth-child(even) {
    background-color: rgba(128, 128, 128, 0.04);
}

/* Column view header — flat, no pill */
columnview > header > button {
    border-radius: 0;
    background: none;
    box-shadow: none;
}
columnview > header > button:hover {
    background-color: rgba(128, 128, 128, 0.12);
}
columnview > header > button:active,
columnview > header > button:checked {
    background-color: rgba(128, 128, 128, 0.18);
}

/* Protocol badges */
.protocol-badge {
    border-radius: 3px;
    padding: 0px 5px;
    font-weight: 600;
    font-size: 0.7em;
}
.badge-ssh {
    background-color: alpha(@success_color, 0.15);
    color: @success_color;
}
.badge-tls {
    background-color: alpha(#3584e4, 0.1);
    color: alpha(#3584e4, 0.7);
}
.badge-insecure {
    background-color: alpha(@warning_color, 0.15);
    color: @warning_color;
}

/* Test connection status */
label.success { color: @success_color; }
label.error { color: @error_color; }
""")
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER
            )

            # Migrate old GtkSourceView scheme IDs to Monaco theme IDs
            self._migrate_config()

            win = EdithWindow(application=self)
        win.present()

    def _migrate_config(self):
        """One-time migration from GtkSourceView config to Monaco."""
        from edith.services.config import ConfigService

        saved_scheme = ConfigService.get_preference("syntax_scheme", "")
        if not saved_scheme:
            return

        # Map old GtkSourceView scheme IDs to Monaco theme equivalents
        scheme_migration = {
            "Adwaita": "vs",
            "Adwaita-dark": "vs-dark",
            "classic": "vs",
            "classic-dark": "vs-dark",
            "cobalt": "vs-dark",
            "kate": "vs",
            "kate-dark": "vs-dark",
            "oblivion": "vs-dark",
            "solarized-light": "solarized-light",
            "solarized-dark": "solarized-dark",
            "tango": "vs",
            "railscasts": "monokai",
        }

        if saved_scheme in scheme_migration:
            ConfigService.set_preference("syntax_scheme", scheme_migration[saved_scheme])

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

        prefs_action = Gio.SimpleAction.new("preferences", None)
        prefs_action.connect("activate", self._on_preferences)
        self.add_action(prefs_action)
        self.set_accels_for_action("app.preferences", ["<Control>comma"])

        new_window_action = Gio.SimpleAction.new("new-window", None)
        new_window_action.connect("activate", self._on_new_window)
        self.add_action(new_window_action)
        self.set_accels_for_action("app.new-window", ["<Control><Shift>n"])

    def _on_about(self, action, param):
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon="de.singular.edith-symbolic",
            version=VERSION,
            developer_name="Kreuder <mk@singular.de>",
            website="https://github.com/mkay/edith",
            license_type=Gtk.License.MIT_X11,
        )
        about.present(self.props.active_window)

    def _on_new_window(self, action, param):
        win = EdithWindow(application=self)
        win.present()

    def _on_preferences(self, action, param):
        win = self.props.active_window
        if not win:
            return
        from edith.widgets.preferences_dialog import PreferencesDialog
        PreferencesDialog(win).present(win)

    def _on_shortcuts(self, action, param):
        win = self.props.active_window
        if not win:
            return

        # Build shortcuts window programmatically
        shortcuts = [
            ("General", [
                ("<Control>q", "Quit"),
                ("<Control><Shift>n", "New Window"),
                ("<Control>comma", "Preferences"),
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
