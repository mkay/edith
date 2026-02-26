import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GObject, Gtk

from edith.services.config import ConfigService


class EditorSettingsDialog(Adw.PreferencesDialog):
    """Global editor settings: minimap, whitespace, sticky scroll, etc."""

    __gsignals__ = {
        "settings-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__(title="Editor Settings", search_enabled=False)
        self._building = True
        self._build_ui()
        self._building = False

    def _build_ui(self):
        page = Adw.PreferencesPage()

        # ── Appearance ────────────────────────────────────────────────── #
        appearance = Adw.PreferencesGroup(title="Appearance")

        self._line_numbers_row = Adw.ComboRow(title="Line Numbers")
        line_numbers_model = Gtk.StringList.new(["On", "Off", "Relative"])
        self._line_numbers_row.set_model(line_numbers_model)
        saved_ln = ConfigService.get_preference("editor_line_numbers", "on")
        self._line_numbers_row.set_selected({"on": 0, "off": 1, "relative": 2}.get(saved_ln, 0))
        self._line_numbers_row.connect("notify::selected", self._on_setting_changed)
        appearance.add(self._line_numbers_row)

        self._minimap_row = Adw.SwitchRow(
            title="Minimap",
            subtitle="Show code overview on the right edge",
        )
        self._minimap_row.set_active(
            ConfigService.get_preference("editor_minimap", False)
        )
        self._minimap_row.connect("notify::active", self._on_setting_changed)
        appearance.add(self._minimap_row)

        self._render_ws_row = Adw.ComboRow(
            title="Render Whitespace",
            subtitle="When to draw spaces and tabs",
        )
        ws_model = Gtk.StringList.new(["None", "Boundary", "Selection", "All"])
        self._render_ws_row.set_model(ws_model)
        saved_ws = ConfigService.get_preference("editor_render_whitespace", "selection")
        self._render_ws_row.set_selected(
            {"none": 0, "boundary": 1, "selection": 2, "all": 3}.get(saved_ws, 2)
        )
        self._render_ws_row.connect("notify::selected", self._on_setting_changed)
        appearance.add(self._render_ws_row)

        page.add(appearance)

        # ── Editing ───────────────────────────────────────────────────── #
        editing = Adw.PreferencesGroup(title="Editing")

        self._sticky_row = Adw.SwitchRow(
            title="Sticky Scroll",
            subtitle="Pin current scope (function / class) at top",
        )
        self._sticky_row.set_active(
            ConfigService.get_preference("editor_sticky_scroll", False)
        )
        self._sticky_row.connect("notify::active", self._on_setting_changed)
        editing.add(self._sticky_row)

        self._ligatures_row = Adw.SwitchRow(
            title="Font Ligatures",
            subtitle="Requires a ligature font (Fira Code, JetBrains Mono…)",
        )
        self._ligatures_row.set_active(
            ConfigService.get_preference("editor_font_ligatures", False)
        )
        self._ligatures_row.connect("notify::active", self._on_setting_changed)
        editing.add(self._ligatures_row)

        page.add(editing)

        # ── Save ──────────────────────────────────────────────────────── #
        save_group = Adw.PreferencesGroup(title="Save")

        self._format_row = Adw.SwitchRow(
            title="Format on Save",
            subtitle="Auto-format HTML, CSS, JSON and JS/TS before saving",
        )
        self._format_row.set_active(
            ConfigService.get_preference("editor_format_on_save", False)
        )
        self._format_row.connect("notify::active", self._on_setting_changed)
        save_group.add(self._format_row)

        page.add(save_group)
        self.add(page)

    def _on_setting_changed(self, row, pspec):
        if self._building:
            return

        # Line numbers
        ln_map = {0: "on", 1: "off", 2: "relative"}
        ConfigService.set_preference(
            "editor_line_numbers",
            ln_map[self._line_numbers_row.get_selected()],
        )
        # Minimap
        ConfigService.set_preference("editor_minimap", self._minimap_row.get_active())
        # Render whitespace
        ws_map = {0: "none", 1: "boundary", 2: "selection", 3: "all"}
        ConfigService.set_preference(
            "editor_render_whitespace",
            ws_map[self._render_ws_row.get_selected()],
        )
        # Sticky scroll
        ConfigService.set_preference(
            "editor_sticky_scroll", self._sticky_row.get_active()
        )
        # Font ligatures
        ConfigService.set_preference(
            "editor_font_ligatures", self._ligatures_row.get_active()
        )
        # Format on save
        ConfigService.set_preference(
            "editor_format_on_save", self._format_row.get_active()
        )

        self.emit("settings-changed")
