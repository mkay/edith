import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")

from gi.repository import Gtk, GtkSource, GObject, Adw, Pango

from edith.models.open_file import OpenFile
from edith.services.config import ConfigService


class EditorPage(Gtk.Box):
    """Single editor tab containing a GtkSourceView."""

    __gsignals__ = {
        "modified-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "save-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, open_file: OpenFile):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.open_file = open_file
        self._build_ui()
        self._load_file()

    def _build_ui(self):
        # Source view
        self._buffer = GtkSource.Buffer()
        self._view = GtkSource.View(
            buffer=self._buffer,
            monospace=True,
            show_line_numbers=True,
            highlight_current_line=True,
            auto_indent=True,
            indent_on_tab=True,
            tab_width=4,
            insert_spaces_instead_of_tabs=True,
            smart_backspace=True,
            show_line_marks=True,
            vexpand=True,
            hexpand=True,
            top_margin=4,
            bottom_margin=4,
            left_margin=4,
            right_margin=4,
        )

        # Enable word wrap
        self._view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)

        # Set up language / syntax highlighting
        self._setup_language()
        self._setup_scheme()
        self._setup_font()

        # Track modifications
        self._buffer.connect("modified-changed", self._on_modified_changed)

        # Scrolled window
        sw = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        sw.set_child(self._view)
        self.append(sw)

    def _setup_language(self):
        """Detect and set syntax highlighting language."""
        lm = GtkSource.LanguageManager.get_default()
        lang = lm.guess_language(self.open_file.filename, None)
        if lang:
            self._buffer.set_language(lang)
        self._buffer.set_highlight_syntax(True)

    def _setup_scheme(self):
        """Set color scheme from saved preference, falling back to system theme."""
        sm = GtkSource.StyleSchemeManager.get_default()
        saved = ConfigService.get_preference("syntax_scheme", "")

        if saved:
            scheme = sm.get_scheme(saved)
            if scheme:
                self._buffer.set_style_scheme(scheme)
                return

        # Fall back to Adwaita light/dark
        style_manager = Adw.StyleManager.get_default()
        if style_manager.get_dark():
            scheme = sm.get_scheme("Adwaita-dark")
        else:
            scheme = sm.get_scheme("Adwaita")

        if scheme:
            self._buffer.set_style_scheme(scheme)

        # Listen for system theme changes (only used when no explicit preference)
        style_manager.connect("notify::dark", self._on_theme_changed)

    def _on_theme_changed(self, style_manager, pspec):
        # Only auto-switch if no explicit preference is set
        saved = ConfigService.get_preference("syntax_scheme", "")
        if saved:
            return
        sm = GtkSource.StyleSchemeManager.get_default()
        if style_manager.get_dark():
            scheme = sm.get_scheme("Adwaita-dark")
        else:
            scheme = sm.get_scheme("Adwaita")
        if scheme:
            self._buffer.set_style_scheme(scheme)

    def apply_scheme(self, scheme_id: str):
        """Apply a style scheme by ID (called when user changes theme)."""
        sm = GtkSource.StyleSchemeManager.get_default()
        scheme = sm.get_scheme(scheme_id)
        if scheme:
            self._buffer.set_style_scheme(scheme)

    def _setup_font(self):
        """Set editor font from saved preference."""
        font_family = ConfigService.get_preference("editor_font", "")
        font_size = ConfigService.get_preference("editor_font_size", 0)
        if font_family or font_size:
            self._apply_font_desc(font_family, font_size)

    def apply_font(self, font_family: str, font_size: int):
        """Apply a font family and size."""
        self._apply_font_desc(font_family, font_size)

    def _apply_font_desc(self, font_family: str, font_size: int):
        parts = []
        if font_family:
            parts.append(f"font-family: '{font_family}';")
        if font_size > 0:
            parts.append(f"font-size: {font_size}pt;")
        if not parts:
            return
        css_str = "textview { " + " ".join(parts) + " }"
        if not hasattr(self, "_font_css"):
            self._font_css = Gtk.CssProvider()
            self._view.get_style_context().add_provider(
                self._font_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self._font_css.load_from_string(css_str)

    def _load_file(self):
        """Load file content into the buffer."""
        try:
            with open(self.open_file.local_path, "r", errors="replace") as f:
                content = f.read()
            self._buffer.set_text(content)
            self._buffer.set_modified(False)
            # Place cursor at start
            self._buffer.place_cursor(self._buffer.get_start_iter())
        except Exception as e:
            self._buffer.set_text(f"Error loading file: {e}")

    def _on_modified_changed(self, buffer):
        modified = buffer.get_modified()
        self.open_file.is_modified = modified
        self.emit("modified-changed", modified)

    def save_to_disk(self):
        """Write buffer contents to the local temp file."""
        start = self._buffer.get_start_iter()
        end = self._buffer.get_end_iter()
        text = self._buffer.get_text(start, end, True)

        with open(self.open_file.local_path, "w") as f:
            f.write(text)

        self._buffer.set_modified(False)

    def get_view(self) -> GtkSource.View:
        return self._view
