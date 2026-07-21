import json

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, GObject, Gtk

from edith.services.config import ConfigService


class PreferencesDialog(Adw.PreferencesDialog):
    """Unified preferences window, split into Editor / Files / General pages."""

    __gsignals__ = {
        "editor-settings-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "navigation-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, window=None):
        super().__init__(title="Preferences", search_enabled=True)
        self._window = window
        self._building = True
        self._build_editor_page()
        self._build_files_page()
        self._build_general_page()
        self._building = False

    # ── Editor ────────────────────────────────────────────────────────── #

    def _build_editor_page(self):
        page = Adw.PreferencesPage(
            title="Editor",
            icon_name="document-edit-symbolic",
        )

        appearance = Adw.PreferencesGroup(title="Appearance")

        self._theme_row = Adw.ActionRow(
            title="Syntax Theme",
            subtitle=self._theme_summary(),
            activatable=True,
        )
        self._theme_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self._theme_row.connect("activated", self._on_theme_activated)
        appearance.add(self._theme_row)

        self._font_row = Adw.ActionRow(
            title="Editor Font",
            subtitle=self._font_summary(),
            activatable=True,
        )
        self._font_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self._font_row.connect("activated", self._on_font_activated)
        appearance.add(self._font_row)

        self._line_numbers_row = Adw.ComboRow(title="Line Numbers")
        self._line_numbers_row.set_model(Gtk.StringList.new(["On", "Off", "Relative"]))
        saved_ln = ConfigService.get_preference("editor_line_numbers", "on")
        self._line_numbers_row.set_selected({"on": 0, "off": 1, "relative": 2}.get(saved_ln, 0))
        self._line_numbers_row.connect("notify::selected", self._on_editor_setting_changed)
        appearance.add(self._line_numbers_row)

        self._minimap_row = Adw.SwitchRow(
            title="Minimap",
            subtitle="Show code overview on the right edge",
        )
        self._minimap_row.set_active(ConfigService.get_preference("editor_minimap", False))
        self._minimap_row.connect("notify::active", self._on_editor_setting_changed)
        appearance.add(self._minimap_row)

        self._render_ws_row = Adw.ComboRow(
            title="Render Whitespace",
            subtitle="When to draw spaces and tabs",
        )
        self._render_ws_row.set_model(
            Gtk.StringList.new(["None", "Boundary", "Selection", "All"])
        )
        saved_ws = ConfigService.get_preference("editor_render_whitespace", "selection")
        self._render_ws_row.set_selected(
            {"none": 0, "boundary": 1, "selection": 2, "all": 3}.get(saved_ws, 2)
        )
        self._render_ws_row.connect("notify::selected", self._on_editor_setting_changed)
        appearance.add(self._render_ws_row)

        page.add(appearance)

        editing = Adw.PreferencesGroup(title="Editing")

        self._sticky_row = Adw.SwitchRow(
            title="Sticky Scroll",
            subtitle="Pin current scope (function / class) at top",
        )
        self._sticky_row.set_active(
            ConfigService.get_preference("editor_sticky_scroll", False)
        )
        self._sticky_row.connect("notify::active", self._on_editor_setting_changed)
        editing.add(self._sticky_row)

        self._ligatures_row = Adw.SwitchRow(
            title="Font Ligatures",
            subtitle="Requires a ligature font (Fira Code, JetBrains Mono…)",
        )
        self._ligatures_row.set_active(
            ConfigService.get_preference("editor_font_ligatures", False)
        )
        self._ligatures_row.connect("notify::active", self._on_editor_setting_changed)
        editing.add(self._ligatures_row)

        page.add(editing)

        save_group = Adw.PreferencesGroup(title="Save")

        self._format_row = Adw.SwitchRow(
            title="Format on Save",
            subtitle="Auto-format HTML, CSS, JSON and JS/TS before saving",
        )
        self._format_row.set_active(
            ConfigService.get_preference("editor_format_on_save", False)
        )
        self._format_row.connect("notify::active", self._on_editor_setting_changed)
        save_group.add(self._format_row)

        page.add(save_group)

        advanced = Adw.PreferencesGroup(
            title="Advanced",
            description=(
                "Raw Monaco editor options as JSON. These are merged on top of "
                "all other settings. See the Monaco Editor API docs for available options."
            ),
        )

        assoc_row = Adw.ActionRow(
            title="Syntax Associations",
            subtitle="Map file extensions to a language",
            activatable=True,
        )
        assoc_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        assoc_row.connect("activated", self._on_associations_activated)
        advanced.add(assoc_row)

        overrides_row = Adw.ActionRow(
            title="Editor Overrides",
            subtitle="Edit raw Monaco options (JSON)",
            activatable=True,
        )
        overrides_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        overrides_row.connect("activated", self._on_overrides_activated)
        advanced.add(overrides_row)

        page.add(advanced)
        self.add(page)

    # ── Files ─────────────────────────────────────────────────────────── #

    def _build_files_page(self):
        page = Adw.PreferencesPage(
            title="Files",
            icon_name="folder-symbolic",
        )

        # ── File associations ──────────────────────────────────────────
        self._assoc_selected_app = None

        add_group = Adw.PreferencesGroup(
            title="Open With",
            description=(
                "Choose which local application opens a file type when you use "
                "“Open with…” in the file browser. Without an entry here, the "
                "system default is used."
            ),
        )

        add_btn = Gtk.Button(
            label="Add",
            css_classes=["suggested-action"],
            valign=Gtk.Align.CENTER,
        )
        add_btn.connect("clicked", self._on_assoc_add)
        add_group.set_header_suffix(add_btn)

        self._assoc_ext_row = Adw.EntryRow(
            title="Extensions (comma-separated, e.g. txt, md, html)"
        )
        add_group.add(self._assoc_ext_row)

        self._assoc_app_btn = Gtk.MenuButton(
            label="Select application…",
            popover=self._build_app_popover(),
            css_classes=["flat"],
            valign=Gtk.Align.CENTER,
        )
        app_row = Adw.ActionRow(title="Application")
        app_row.add_suffix(self._assoc_app_btn)
        app_row.set_activatable_widget(self._assoc_app_btn)
        add_group.add(app_row)

        page.add(add_group)

        self._assoc_group = Adw.PreferencesGroup(title="Current Associations")
        page.add(self._assoc_group)
        self._rebuild_assoc_list()

        from edith.widgets.file_browser import DEFAULT_TOOLS_DIR

        tools = Adw.PreferencesGroup(
            title="Upload Tools",
            description=(
                f"Scripts in this folder appear in the file browser's "
                f"“Upload Tool” menu. Leave empty to use the default "
                f"({DEFAULT_TOOLS_DIR})."
            ),
        )

        self._tools_row = Adw.EntryRow(
            title="Tools folder",
            text=ConfigService.get_preference("tools_folder", ""),
            show_apply_button=True,
        )
        browse_btn = Gtk.Button(
            icon_name="folder-open-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
            tooltip_text="Browse…",
        )
        browse_btn.connect("clicked", self._on_tools_browse)
        self._tools_row.add_suffix(browse_btn)
        self._tools_row.connect("apply", self._on_tools_applied)
        tools.add(self._tools_row)

        page.add(tools)
        self.add(page)

    # ── File associations ─────────────────────────────────────────────── #

    def _build_app_popover(self):
        from edith.services import file_associations as fa

        self._app_search_entry = Gtk.SearchEntry(
            placeholder_text="Filter…",
            margin_start=8,
            margin_end=8,
            margin_top=8,
            margin_bottom=4,
        )

        self._app_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=["navigation-sidebar"],
        )
        self._app_list.set_filter_func(self._filter_app_row)
        self._app_list.connect("row-activated", self._on_app_row_activated)

        for app in fa.all_installed_apps():
            app_id = app.get_id()
            if not app_id:
                continue
            row = Gtk.ListBoxRow()
            row.app_id = app_id
            row.app_name = app.get_display_name() or app_id

            box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
                margin_start=8,
                margin_end=8,
                margin_top=4,
                margin_bottom=4,
            )
            icon = Gtk.Image(pixel_size=16)
            gicon = app.get_icon()
            if gicon is not None:
                icon.set_from_gicon(gicon)
            else:
                icon.set_from_icon_name("application-x-executable-symbolic")
            box.append(icon)
            box.append(Gtk.Label(label=row.app_name, xalign=0))
            row.set_child(box)
            self._app_list.append(row)

        sw = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            propagate_natural_height=True,
            max_content_height=300,
        )
        sw.set_child(self._app_list)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_size_request(260, -1)
        box.append(self._app_search_entry)
        box.append(sw)

        self._app_search_entry.connect(
            "search-changed", lambda _: self._app_list.invalidate_filter()
        )

        popover = Gtk.Popover()
        popover.set_child(box)
        return popover

    def _filter_app_row(self, row):
        query = self._app_search_entry.get_text().lower().strip()
        return not query or query in row.app_name.lower()

    def _on_app_row_activated(self, list_box, row):
        self._assoc_selected_app = row.app_id
        self._assoc_app_btn.set_label(row.app_name)
        self._assoc_app_btn.get_popover().popdown()

    def _on_assoc_add(self, btn):
        from edith.services import file_associations as fa

        text = self._assoc_ext_row.get_text()
        if not fa.parse_extensions(text) or not self._assoc_selected_app:
            return
        fa.set_association(text, self._assoc_selected_app)
        self._assoc_ext_row.set_text("")
        self._assoc_selected_app = None
        self._assoc_app_btn.set_label("Select application…")
        self._rebuild_assoc_list()

    def _rebuild_assoc_list(self):
        from edith.services import file_associations as fa

        for row in getattr(self, "_assoc_rows", []):
            self._assoc_group.remove(row)
        self._assoc_rows = []

        grouped = fa.get_associations_by_app()
        if not grouped:
            row = Adw.ActionRow(
                title="No associations",
                subtitle="Files open with the system default application",
                sensitive=False,
            )
            self._assoc_group.add(row)
            self._assoc_rows.append(row)
            return

        def sort_key(item):
            desktop_id, _exts = item
            app = fa.app_for_desktop_id(desktop_id)
            return ((app.get_display_name() if app else desktop_id) or "").lower()

        # One row per application, listing every extension mapped to it.
        for desktop_id, exts in sorted(grouped.items(), key=sort_key):
            app = fa.app_for_desktop_id(desktop_id)
            name = app.get_display_name() if app else f"{desktop_id} (not installed)"

            row = Adw.ExpanderRow(
                title=name,
                subtitle=", ".join(f".{e}" for e in exts),
            )
            if app is not None and app.get_icon() is not None:
                icon = Gtk.Image(pixel_size=24)
                icon.set_from_gicon(app.get_icon())
                row.add_prefix(icon)

            add_ext_btn = Gtk.Button(
                icon_name="list-add-symbolic",
                valign=Gtk.Align.CENTER,
                css_classes=["flat"],
                tooltip_text=f"Add an extension for {name}",
            )
            add_ext_btn.connect("clicked", self._on_assoc_expand_to_add, row)
            row.add_suffix(add_ext_btn)

            remove_all_btn = Gtk.Button(
                icon_name="user-trash-symbolic",
                valign=Gtk.Align.CENTER,
                css_classes=["flat"],
                tooltip_text=f"Remove all {len(exts)} associations",
            )
            remove_all_btn.connect("clicked", self._on_assoc_remove, list(exts))
            row.add_suffix(remove_all_btn)

            # Expand to add or drop individual extensions.
            for ext in exts:
                child = Adw.ActionRow(title=f".{ext}")
                ext_btn = Gtk.Button(
                    icon_name="user-trash-symbolic",
                    valign=Gtk.Align.CENTER,
                    css_classes=["flat"],
                    tooltip_text=f"Remove .{ext}",
                )
                ext_btn.connect("clicked", self._on_assoc_remove, ext)
                child.add_suffix(ext_btn)
                row.add_row(child)

            add_row = Adw.EntryRow(
                title="Add extensions…",
                show_apply_button=True,
            )
            add_row.connect("apply", self._on_assoc_add_to_app, desktop_id)
            row.add_row(add_row)

            self._assoc_group.add(row)
            self._assoc_rows.append(row)

    def _on_assoc_add_to_app(self, entry_row, desktop_id):
        """Add extensions to an application that already has associations."""
        from edith.services import file_associations as fa

        text = entry_row.get_text()
        if not fa.parse_extensions(text):
            return
        fa.set_association(text, desktop_id)
        entry_row.set_text("")
        self._queue_assoc_rebuild()

    def _on_assoc_expand_to_add(self, btn, expander_row):
        """Reveal the inline “Add extensions…” field for a group."""
        expander_row.set_expanded(True)

    def _on_assoc_remove(self, btn, ext):
        from edith.services import file_associations as fa
        fa.remove_association(ext)
        self._queue_assoc_rebuild()

    def _queue_assoc_rebuild(self):
        """Rebuild the list once the current signal emission has finished.

        The buttons and entries live inside the rows being replaced, so tearing
        them down from their own handler is asking for trouble.
        """
        GLib.idle_add(self._rebuild_assoc_list_idle)

    def _rebuild_assoc_list_idle(self):
        self._rebuild_assoc_list()
        return GLib.SOURCE_REMOVE

    # ── General ───────────────────────────────────────────────────────── #

    def _build_general_page(self):
        page = Adw.PreferencesPage(
            title="General",
            icon_name="preferences-system-symbolic",
        )

        navigation = Adw.PreferencesGroup(title="Navigation")

        self._click_row = Adw.ComboRow(
            title="Open Items With",
            subtitle="How files, folders and servers are opened",
        )
        self._click_row.set_model(Gtk.StringList.new(["Double Click", "Single Click"]))
        self._click_row.set_selected(
            1 if ConfigService.get_preference("single_click_open", False) else 0
        )
        self._click_row.connect("notify::selected", self._on_navigation_changed)
        navigation.add(self._click_row)

        page.add(navigation)

        window_group = Adw.PreferencesGroup(
            title="Window",
            description="Size used for newly opened windows.",
        )

        self._width_row = Adw.SpinRow(
            title="Width",
            adjustment=Gtk.Adjustment(
                value=ConfigService.get_preference("window_width", 1100),
                lower=800, upper=3840, step_increment=10,
            ),
        )
        self._width_row.connect("notify::value", self._on_window_size_changed)
        window_group.add(self._width_row)

        self._height_row = Adw.SpinRow(
            title="Height",
            adjustment=Gtk.Adjustment(
                value=ConfigService.get_preference("window_height", 700),
                lower=600, upper=2160, step_increment=10,
            ),
        )
        self._height_row.connect("notify::value", self._on_window_size_changed)
        window_group.add(self._height_row)

        page.add(window_group)
        self.add(page)

    # ── Handlers ──────────────────────────────────────────────────────── #

    def _theme_summary(self):
        from edith.monaco_languages import get_theme_name
        scheme = ConfigService.get_preference("syntax_scheme", "")
        return get_theme_name(scheme) if scheme else "Default"

    def _font_summary(self):
        family = ConfigService.get_preference("editor_font", "") or "Monospace (default)"
        size = ConfigService.get_preference("editor_font_size", 11)
        return f"{family} · {size}pt"

    def _on_theme_activated(self, row):
        from edith.widgets.theme_chooser_dialog import ThemeChooserDialog
        dialog = ThemeChooserDialog()
        dialog.connect("scheme-changed", self._on_scheme_changed)
        dialog.present(self)

    def _on_scheme_changed(self, dialog, scheme_id):
        self._theme_row.set_subtitle(self._theme_summary())
        if self._window:
            self._window.apply_syntax_scheme(scheme_id)

    def _on_font_activated(self, row):
        from edith.widgets.font_chooser_dialog import FontChooserDialog
        dialog = FontChooserDialog()
        dialog.connect("font-changed", self._on_font_changed)
        dialog.present(self)

    def _on_font_changed(self, dialog, font_family, font_size):
        self._font_row.set_subtitle(self._font_summary())
        if self._window:
            self._window.apply_editor_font(font_family, font_size)

    def _on_associations_activated(self, row):
        from edith.widgets.syntax_associations_dialog import SyntaxAssociationsDialog
        SyntaxAssociationsDialog().present(self)

    def _on_editor_setting_changed(self, row, pspec):
        if self._building:
            return

        ln_map = {0: "on", 1: "off", 2: "relative"}
        ConfigService.set_preference(
            "editor_line_numbers", ln_map[self._line_numbers_row.get_selected()]
        )
        ConfigService.set_preference("editor_minimap", self._minimap_row.get_active())
        ws_map = {0: "none", 1: "boundary", 2: "selection", 3: "all"}
        ConfigService.set_preference(
            "editor_render_whitespace", ws_map[self._render_ws_row.get_selected()]
        )
        ConfigService.set_preference(
            "editor_sticky_scroll", self._sticky_row.get_active()
        )
        ConfigService.set_preference(
            "editor_font_ligatures", self._ligatures_row.get_active()
        )
        ConfigService.set_preference(
            "editor_format_on_save", self._format_row.get_active()
        )

        self._apply_editor_settings()

    def _apply_editor_settings(self):
        if self._window:
            self._window.apply_editor_settings()
        self.emit("editor-settings-changed")

    def _on_navigation_changed(self, row, pspec):
        if self._building:
            return
        ConfigService.set_preference(
            "single_click_open", self._click_row.get_selected() == 1
        )
        if self._window:
            self._window.apply_navigation_settings()
        self.emit("navigation-changed")

    def _on_window_size_changed(self, row, pspec):
        if self._building:
            return
        ConfigService.set_preference("window_width", int(self._width_row.get_value()))
        ConfigService.set_preference("window_height", int(self._height_row.get_value()))

    def _on_tools_applied(self, row):
        ConfigService.set_preference("tools_folder", row.get_text().strip())

    def _on_tools_browse(self, btn):
        fd = Gtk.FileDialog(title="Choose Tools Folder")

        def _chosen(dialog, result):
            try:
                folder = dialog.select_folder_finish(result)
            except Exception:
                return
            path = folder.get_path()
            if path:
                self._tools_row.set_text(path)
                ConfigService.set_preference("tools_folder", path)

        parent = self._window or self.get_root()
        fd.select_folder(parent, None, _chosen)

    # ── Editor overrides sub-dialog ───────────────────────────────────── #

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
                '  // "smoothScrolling": true,\n'
                '\n'
                '  // Language-service (formatter) options are also supported\n'
                '  // for html / css / scss / less / json. Either dotted keys:\n'
                '  // "html.format.wrapLineLength": 0,\n'
                '  // "html.format.wrapAttributes": "auto",\n'
                '  // "html.format.preserveNewLines": true,\n'
                '  // or a nested object:\n'
                '  // "html": { "format": { "wrapLineLength": 0 } }\n'
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
                if line.lstrip().startswith("//"):
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
            self._apply_editor_settings()

        apply_btn.connect("clicked", on_apply)
        dialog.present(self)
