import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk, GObject, Pango, PangoCairo

from edith.services.config import ConfigService


PREVIEW_TEXT = 'The quick brown fox jumps over the lazy dog\n0123456789 (){}[] !@#$%^&* += <> :; "\' `~ ,./?\\|'


class FontChooserDialog(Adw.Dialog):
    """Dialog for choosing the editor font family and size."""

    __gsignals__ = {
        "font-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, int)),
    }

    def __init__(self):
        super().__init__(
            title="Editor Font",
            content_width=480,
            content_height=520,
        )
        self._build_ui()

    def _build_ui(self):
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar(
            show_start_title_buttons=False, show_end_title_buttons=False,
        )
        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda _: self.close())
        header.pack_end(close_btn)
        toolbar_view.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          margin_start=12, margin_end=12, margin_top=12, margin_bottom=12)

        # --- Size spinner ---
        saved_size = ConfigService.get_preference("editor_font_size", 11)
        size_group = Adw.PreferencesGroup(title="Size")
        self._size_adj = Gtk.Adjustment(value=saved_size, lower=6, upper=72, step_increment=1)
        self._size_row = Adw.SpinRow(title="Font size (pt)", adjustment=self._size_adj)
        self._size_row.connect("notify::value", self._on_changed)
        size_group.add(self._size_row)
        content.append(size_group)

        # --- Font list ---
        saved_font = ConfigService.get_preference("editor_font", "")

        font_group = Adw.PreferencesGroup(title="Font Family")
        content.append(font_group)

        # Search entry
        self._search_entry = Gtk.SearchEntry(
            placeholder_text="Search fonts\u2026",
            hexpand=True,
        )
        self._search_entry.connect("search-changed", self._on_search_changed)
        font_group.add(self._search_entry)

        sw = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            min_content_height=200,
        )
        self._list_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            css_classes=["navigation-sidebar"],
        )
        self._list_box.set_filter_func(self._filter_func)
        self._list_box.connect("row-selected", self._on_row_selected)
        sw.set_child(self._list_box)
        content.append(sw)

        # --- Preview ---
        preview_group = Adw.PreferencesGroup(title="Preview")
        self._preview_label = Gtk.Label(
            label=PREVIEW_TEXT,
            xalign=0,
            wrap=True,
            margin_start=8, margin_end=8, margin_top=8, margin_bottom=8,
        )
        preview_frame = Gtk.Frame()
        preview_frame.set_child(self._preview_label)
        preview_group.add(preview_frame)
        content.append(preview_group)

        toolbar_view.set_content(content)
        self.set_child(toolbar_view)

        # Populate font list
        self._populate_fonts(saved_font)

    def _populate_fonts(self, saved_font: str):
        mono_families = self._get_monospace_families()
        select_row = None

        for family_name in mono_families:
            label = Gtk.Label(
                label=family_name,
                xalign=0,
                margin_start=8, margin_end=8, margin_top=4, margin_bottom=4,
            )
            label._font_family = family_name
            self._list_box.append(label)

            if family_name == saved_font:
                select_row = label.get_parent()

        if select_row:
            self._list_box.select_row(select_row)
        else:
            first = self._list_box.get_row_at_index(0)
            if first:
                self._list_box.select_row(first)

    def _get_monospace_families(self) -> list[str]:
        """Get sorted list of monospace font family names."""
        font_map = PangoCairo.FontMap.get_default()
        families = font_map.list_families()
        mono = []
        for fam in families:
            if fam.is_monospace():
                mono.append(fam.get_name())
        mono.sort(key=str.lower)
        return mono

    def _filter_func(self, row):
        text = self._search_entry.get_text().strip().lower()
        if not text:
            return True
        child = row.get_child()
        family = getattr(child, "_font_family", "")
        return text in family.lower()

    def _on_search_changed(self, entry):
        self._list_box.invalidate_filter()

    def _on_row_selected(self, list_box, row):
        if not row:
            return
        self._on_changed()

    def _on_changed(self, *args):
        row = self._list_box.get_selected_row()
        if not row:
            return
        child = row.get_child()
        font_family = getattr(child, "_font_family", "")
        font_size = int(self._size_row.get_value())

        # Update preview
        css_str = f"label {{ font-family: '{font_family}'; font-size: {font_size}pt; }}"
        if not hasattr(self, "_preview_css"):
            self._preview_css = Gtk.CssProvider()
            self._preview_label.get_style_context().add_provider(
                self._preview_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self._preview_css.load_from_string(css_str)

        # Save and emit
        ConfigService.set_preference("editor_font", font_family)
        ConfigService.set_preference("editor_font_size", font_size)
        self.emit("font-changed", font_family, font_size)
