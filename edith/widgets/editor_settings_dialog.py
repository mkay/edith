import json

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

        # ── Advanced ─────────────────────────────────────────────── #
        advanced = Adw.PreferencesGroup(
            title="Advanced",
            description=(
                "Raw Monaco editor options as JSON. These are merged on top of "
                "all other settings. See the Monaco Editor API docs for available options."
            ),
        )

        overrides_row = Adw.ActionRow(
            title="Editor Overrides",
            subtitle="Edit raw Monaco options (JSON)",
            activatable=True,
        )
        overrides_row.add_suffix(
            Gtk.Image(icon_name="go-next-symbolic")
        )
        overrides_row.connect("activated", self._on_overrides_activated)
        advanced.add(overrides_row)

        page.add(advanced)
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

    def _on_overrides_activated(self, row):
        current = ConfigService.get_preference("editor_overrides", {})
        if not current:
            text = (
                '{\n'
                '  // Any valid Monaco editor option can go here.\n'
                '  // Examples:\n'
                '  // "cursorStyle": "line",\n'
                '  // "wordWrap": "on",\n'
                '  // "scrollBeyondLastLine": false,\n'
                '  // "cursorBlinking": "expand",\n'
                '  // "smoothScrolling": true\n'
                '}'
            )
        else:
            text = json.dumps(current, indent=2)

        dialog = Adw.Dialog(
            title="Editor Overrides",
            content_width=480,
            content_height=420,
        )

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar(
            show_start_title_buttons=False,
            show_end_title_buttons=False,
        )

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _: dialog.close())
        header.pack_start(cancel_btn)

        apply_btn = Gtk.Button(label="Apply", css_classes=["suggested-action"])
        header.pack_end(apply_btn)
        toolbar_view.add_top_bar(header)

        # Text editor for JSON
        text_view = Gtk.TextView(
            monospace=True,
            top_margin=12,
            bottom_margin=12,
            left_margin=12,
            right_margin=12,
        )
        text_view.get_buffer().set_text(text)

        error_label = Gtk.Label(
            label="",
            xalign=0,
            css_classes=["error"],
            margin_start=12,
            margin_end=12,
            margin_bottom=6,
            visible=False,
            wrap=True,
        )

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_child(text_view)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(scrolled)
        content_box.append(error_label)
        toolbar_view.set_content(content_box)
        dialog.set_child(toolbar_view)

        def on_apply(_):
            buf = text_view.get_buffer()
            raw = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            # Strip JS-style // comments before parsing
            lines = []
            for line in raw.split("\n"):
                stripped = line.lstrip()
                if stripped.startswith("//"):
                    continue
                lines.append(line)
            cleaned = "\n".join(lines)
            try:
                parsed = json.loads(cleaned) if cleaned.strip() else {}
            except json.JSONDecodeError as e:
                error_label.set_label(f"Invalid JSON: {e}")
                error_label.set_visible(True)
                return
            if not isinstance(parsed, dict):
                error_label.set_label("Top level must be a JSON object {}")
                error_label.set_visible(True)
                return
            ConfigService.set_preference("editor_overrides", parsed)
            dialog.close()
            self.emit("settings-changed")

        apply_btn.connect("clicked", on_apply)
        dialog.present(self)
