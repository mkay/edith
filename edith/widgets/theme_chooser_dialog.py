import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")

from gi.repository import Adw, Gtk, GObject, GtkSource

from edith.services.config import ConfigService


PREVIEW_TEXT = '''\
def fibonacci(n):
    """Return the nth Fibonacci number."""
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b  # swap and sum
    return b

# Print first 10 numbers
for i in range(10):
    print(f"fib({i}) = {fibonacci(i)}")
'''


class ThemeChooserDialog(Adw.Dialog):
    """Dialog for choosing a GtkSourceView syntax highlighting theme."""

    __gsignals__ = {
        "scheme-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__(
            title="Syntax Theme",
            content_width=700,
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

        # Horizontal split: scheme list on left, preview on right
        paned = Gtk.Paned(
            orientation=Gtk.Orientation.HORIZONTAL,
            position=240,
            shrink_start_child=False,
            shrink_end_child=False,
        )

        # --- Left: scheme list ---
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        sw_list = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            min_content_width=200,
        )
        self._list_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            css_classes=["navigation-sidebar"],
        )
        self._list_box.connect("row-selected", self._on_row_selected)
        sw_list.set_child(self._list_box)
        left_box.append(sw_list)

        paned.set_start_child(left_box)

        # --- Right: preview ---
        self._preview_buffer = GtkSource.Buffer()
        lm = GtkSource.LanguageManager.get_default()
        lang = lm.get_language("python")
        if lang:
            self._preview_buffer.set_language(lang)
        self._preview_buffer.set_highlight_syntax(True)
        self._preview_buffer.set_text(PREVIEW_TEXT)

        self._preview_view = GtkSource.View(
            buffer=self._preview_buffer,
            monospace=True,
            show_line_numbers=True,
            editable=False,
            cursor_visible=False,
            top_margin=4,
            bottom_margin=4,
            left_margin=4,
            right_margin=4,
            vexpand=True,
            hexpand=True,
        )

        sw_preview = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        sw_preview.set_child(self._preview_view)
        paned.set_end_child(sw_preview)

        toolbar_view.set_content(paned)
        self.set_child(toolbar_view)

        # Populate scheme list
        self._populate_schemes()

    def _populate_schemes(self):
        sm = GtkSource.StyleSchemeManager.get_default()
        scheme_ids = sorted(sm.get_scheme_ids(), key=str.lower)
        current_id = ConfigService.get_preference("syntax_scheme", "")

        select_row = None
        for scheme_id in scheme_ids:
            scheme = sm.get_scheme(scheme_id)
            if not scheme:
                continue

            row_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=2,
                margin_start=8, margin_end=8, margin_top=4, margin_bottom=4,
            )
            name_label = Gtk.Label(
                label=scheme.get_name(),
                xalign=0,
                ellipsize=3,
            )
            row_box.append(name_label)

            desc = scheme.get_description()
            if desc:
                desc_label = Gtk.Label(
                    label=desc,
                    xalign=0,
                    ellipsize=3,
                    css_classes=["dim-label", "caption"],
                )
                row_box.append(desc_label)

            row_box._scheme_id = scheme_id
            self._list_box.append(row_box)

            if scheme_id == current_id:
                # Find the ListBoxRow wrapping this box
                listbox_row = row_box.get_parent()
                select_row = listbox_row

        # Select current scheme or first row
        if select_row:
            self._list_box.select_row(select_row)
        else:
            first = self._list_box.get_row_at_index(0)
            if first:
                self._list_box.select_row(first)

    def _on_row_selected(self, list_box, row):
        if not row:
            return
        child = row.get_child()
        scheme_id = getattr(child, "_scheme_id", None)
        if not scheme_id:
            return

        sm = GtkSource.StyleSchemeManager.get_default()
        scheme = sm.get_scheme(scheme_id)
        if scheme:
            self._preview_buffer.set_style_scheme(scheme)

        # Save and notify
        ConfigService.set_preference("syntax_scheme", scheme_id)
        self.emit("scheme-changed", scheme_id)
