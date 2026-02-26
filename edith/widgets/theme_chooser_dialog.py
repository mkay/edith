import gi
import json

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")

from pathlib import Path

from gi.repository import Adw, Gtk, GObject, WebKit

from edith.monaco_languages import MONACO_THEMES
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
    """Dialog for choosing a Monaco editor theme with live preview."""

    __gsignals__ = {
        "scheme-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__(
            title="Syntax Theme",
            content_width=700,
            content_height=520,
        )
        self._preview_ready = False
        self._pending_theme = None
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

        # Horizontal split: theme list on left, preview on right
        paned = Gtk.Paned(
            orientation=Gtk.Orientation.HORIZONTAL,
            position=240,
            shrink_start_child=False,
            shrink_end_child=False,
        )

        # --- Left: theme list ---
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

        # --- Right: Monaco preview in WebView ---
        self._preview_webview = WebKit.WebView()
        self._preview_webview.set_vexpand(True)
        self._preview_webview.set_hexpand(True)

        settings = self._preview_webview.get_settings()
        settings.set_allow_file_access_from_file_urls(True)
        settings.set_allow_universal_access_from_file_urls(True)

        # Listen for ready signal from preview
        ucm = self._preview_webview.get_user_content_manager()
        ucm.register_script_message_handler("edith")
        ucm.connect("script-message-received::edith", self._on_preview_message)

        paned.set_end_child(self._preview_webview)

        toolbar_view.set_content(paned)
        self.set_child(toolbar_view)

        # Load the editor HTML for preview
        monaco_dir = Path(__file__).parent.parent / "data" / "monaco"
        editor_uri = (monaco_dir / "editor.html").as_uri()
        self._preview_webview.load_uri(editor_uri)

        # Populate theme list
        self._populate_themes()

    def _populate_themes(self):
        current_id = ConfigService.get_preference("syntax_scheme", "")

        select_row = None
        for theme_id, display_name in MONACO_THEMES:
            row_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=2,
                margin_start=8, margin_end=8, margin_top=4, margin_bottom=4,
            )
            name_label = Gtk.Label(
                label=display_name,
                xalign=0,
                ellipsize=3,
            )
            row_box.append(name_label)

            row_box._theme_id = theme_id
            self._list_box.append(row_box)

            if theme_id == current_id:
                listbox_row = row_box.get_parent()
                select_row = listbox_row

        # Select current theme or first row
        if select_row:
            self._list_box.select_row(select_row)
        else:
            first = self._list_box.get_row_at_index(0)
            if first:
                self._list_box.select_row(first)

    def _on_preview_message(self, ucm, js_result):
        try:
            raw = js_result.to_string()
            msg = json.loads(raw)
        except Exception:
            return

        if msg.get("type") == "ready":
            self._preview_ready = True
            # Init preview with sample content
            content_js = json.dumps(PREVIEW_TEXT)
            self._preview_webview.evaluate_javascript(
                f"EdithBridge.init({content_js}, 'python', null, null, null, false)",
                -1, None, None, None, None, None,
            )
            # Apply pending theme if a row was selected before ready
            if self._pending_theme:
                self._apply_preview_theme(self._pending_theme)
                self._pending_theme = None

    def _apply_preview_theme(self, theme_id):
        if not self._preview_ready:
            self._pending_theme = theme_id
            return
        self._preview_webview.evaluate_javascript(
            f"EdithBridge.setTheme({json.dumps(theme_id)})",
            -1, None, None, None, None, None,
        )

    def _on_row_selected(self, list_box, row):
        if not row:
            return
        child = row.get_child()
        theme_id = getattr(child, "_theme_id", None)
        if not theme_id:
            return

        self._apply_preview_theme(theme_id)

        # Save and notify
        ConfigService.set_preference("syntax_scheme", theme_id)
        self.emit("scheme-changed", theme_id)
