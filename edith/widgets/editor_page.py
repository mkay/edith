import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")

from gi.repository import Gdk, Gtk, GtkSource, GObject, Adw

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
        self._search_context = None
        self._search_settings = None
        self._build_ui()
        self._load_file()

    def _build_ui(self):
        # Source view
        self._buffer = GtkSource.Buffer()
        self._buffer.set_highlight_matching_brackets(True)
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

        # Search bar (hidden by default, slides in from top)
        self.append(self._build_search_bar())

        # Scrolled window
        sw = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        sw.set_child(self._view)
        self.append(sw)

    # ------------------------------------------------------------------ #
    #  Search bar                                                          #
    # ------------------------------------------------------------------ #

    def _build_search_bar(self):
        self._search_settings = GtkSource.SearchSettings(wrap_around=True)
        self._search_context = GtkSource.SearchContext(
            buffer=self._buffer,
            settings=self._search_settings,
        )
        self._search_context.connect(
            "notify::occurrences-count", lambda *_: self._update_match_label()
        )

        self._search_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=150,
            reveal_child=False,
        )

        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # ── Row 1: find ──────────────────────────────────────────────── #
        find_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            margin_start=6,
            margin_end=6,
            margin_top=4,
            margin_bottom=2,
        )

        close_btn = Gtk.Button(
            icon_name="window-close-symbolic",
            has_frame=False,
            tooltip_text="Close (Escape)",
            focusable=False,
        )
        close_btn.connect("clicked", lambda _: self.hide_search())
        find_row.append(close_btn)

        self._search_entry = Gtk.SearchEntry(
            placeholder_text="Find…",
            hexpand=True,
        )
        self._search_entry.connect("search-changed", lambda _: self._update_search())
        self._search_entry.connect("activate", lambda _: self.find_next())
        self._search_entry.connect("next-match", lambda _: self.find_next())
        self._search_entry.connect("previous-match", lambda _: self.find_prev())
        self._search_entry.connect("stop-search", lambda _: self.hide_search())
        find_row.append(self._search_entry)

        self._case_btn = Gtk.ToggleButton(
            label="Aa",
            tooltip_text="Case Sensitive",
            has_frame=False,
            focusable=False,
        )
        self._case_btn.connect("toggled", lambda _: self._on_search_option_changed())
        find_row.append(self._case_btn)

        self._regex_btn = Gtk.ToggleButton(
            label=".*",
            tooltip_text="Regular Expression",
            has_frame=False,
            focusable=False,
        )
        self._regex_btn.connect("toggled", lambda _: self._on_search_option_changed())
        find_row.append(self._regex_btn)

        prev_btn = Gtk.Button(
            icon_name="edith-parent-dir-symbolic",
            tooltip_text="Previous Match (Shift+Enter)",
            has_frame=False,
            focusable=False,
        )
        prev_btn.connect("clicked", lambda _: self.find_prev())
        find_row.append(prev_btn)

        next_btn = Gtk.Button(
            icon_name="go-down-symbolic",
            tooltip_text="Next Match (Enter)",
            has_frame=False,
            focusable=False,
        )
        next_btn.connect("clicked", lambda _: self.find_next())
        find_row.append(next_btn)

        self._match_label = Gtk.Label(
            css_classes=["dim-label"],
            width_chars=10,
            xalign=1.0,
        )
        find_row.append(self._match_label)

        bar.append(find_row)

        # ── Row 2: replace (hidden until Ctrl+H) ─────────────────────── #
        self._replace_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=120,
            reveal_child=False,
        )

        replace_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            margin_start=6,
            margin_end=6,
            margin_top=0,
            margin_bottom=4,
        )

        # Align replace entry under the search entry (close btn is ~32px)
        replace_row.append(Gtk.Box(width_request=32 + 4))

        self._replace_entry = Gtk.Entry(
            placeholder_text="Replace with…",
            hexpand=True,
        )
        self._replace_entry.connect("activate", lambda _: self._replace_one())
        replace_key = Gtk.EventControllerKey()
        replace_key.connect(
            "key-pressed",
            lambda c, kv, kc, s: (self.hide_search(), True)[1]
            if kv == Gdk.KEY_Escape
            else False,
        )
        self._replace_entry.add_controller(replace_key)
        replace_row.append(self._replace_entry)

        replace_btn = Gtk.Button(label="Replace")
        replace_btn.connect("clicked", lambda _: self._replace_one())
        replace_row.append(replace_btn)

        replace_all_btn = Gtk.Button(label="Replace All")
        replace_all_btn.connect("clicked", lambda _: self._replace_all())
        replace_row.append(replace_all_btn)

        self._replace_revealer.set_child(replace_row)
        bar.append(self._replace_revealer)

        bar.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self._search_revealer.set_child(bar)
        return self._search_revealer

    # ── Public search API ─────────────────────────────────────────────── #

    def show_find(self):
        """Show the find bar (find-only mode)."""
        self._search_revealer.set_reveal_child(True)
        self._replace_revealer.set_reveal_child(False)
        self._prefill_search_entry()
        self._search_entry.grab_focus()
        self._search_entry.select_region(0, -1)
        self._update_search()

    def show_replace(self):
        """Show the find+replace bar."""
        self._search_revealer.set_reveal_child(True)
        self._replace_revealer.set_reveal_child(True)
        self._prefill_search_entry()
        self._search_entry.grab_focus()
        self._search_entry.select_region(0, -1)
        self._update_search()

    def hide_search(self):
        """Collapse the search bar and return focus to the editor."""
        self._search_revealer.set_reveal_child(False)
        self._search_settings.set_search_text("")
        self._match_label.set_text("")
        self._match_label.remove_css_class("error")
        self._view.grab_focus()

    def find_next(self):
        if not self._search_settings.get_search_text():
            return
        has_sel, _, sel_end = self._sel_bounds()
        cursor = sel_end if has_sel else self._buffer.get_iter_at_mark(
            self._buffer.get_insert()
        )
        found, ms, me, _ = self._search_context.forward(cursor)
        if found:
            self._buffer.select_range(ms, me)
            self._view.scroll_to_iter(ms, 0.1, True, 0.0, 0.5)
            self._update_match_label()

    def find_prev(self):
        if not self._search_settings.get_search_text():
            return
        has_sel, sel_start, _ = self._sel_bounds()
        cursor = sel_start if has_sel else self._buffer.get_iter_at_mark(
            self._buffer.get_insert()
        )
        found, ms, me, _ = self._search_context.backward(cursor)
        if found:
            self._buffer.select_range(ms, me)
            self._view.scroll_to_iter(ms, 0.1, True, 0.0, 0.5)
            self._update_match_label()

    def goto_line(self, line: int):
        """Scroll to and place cursor at the given 0-indexed line number."""
        n = self._buffer.get_line_count()
        line = max(0, min(line, n - 1))
        it = self._buffer.get_iter_at_line(line)
        self._buffer.place_cursor(it)
        self._view.scroll_to_iter(it, 0.1, True, 0.0, 0.3)
        self._view.grab_focus()

    # ── Internal helpers ──────────────────────────────────────────────── #

    def _sel_bounds(self):
        """Return (has_sel, start_iter, end_iter) safely.

        PyGObject's get_selection_bounds() returns () when nothing is
        selected instead of (False, iter, iter), so we can't unpack it
        directly as a 3-tuple.
        """
        if not self._buffer.get_has_selection():
            cursor = self._buffer.get_iter_at_mark(self._buffer.get_insert())
            return False, cursor, cursor
        bounds = self._buffer.get_selection_bounds()
        # Binding may return (start, end) or (True, start, end)
        return True, bounds[-2], bounds[-1]

    def _prefill_search_entry(self):
        """If text is selected, pre-populate the search entry with it."""
        has_sel, sel_start, sel_end = self._sel_bounds()
        if has_sel:
            text = self._buffer.get_text(sel_start, sel_end, False)
            if "\n" not in text and len(text) < 200:
                self._search_entry.set_text(text)

    def _update_search(self):
        text = self._search_entry.get_text()
        self._search_settings.set_search_text(text)
        if not text:
            self._match_label.set_text("")
            self._match_label.remove_css_class("error")
            return
        has_sel, sel_start, _ = self._sel_bounds()
        cursor = sel_start if has_sel else self._buffer.get_iter_at_mark(
            self._buffer.get_insert()
        )
        found, ms, me, _ = self._search_context.forward(cursor)
        if found:
            self._buffer.select_range(ms, me)
            self._view.scroll_to_iter(ms, 0.1, True, 0.0, 0.5)
        self._update_match_label()

    def _update_match_label(self):
        total = self._search_context.get_occurrences_count()
        text = self._search_entry.get_text()
        if not text:
            self._match_label.set_text("")
            self._match_label.remove_css_class("error")
            return
        if total < 0:
            self._match_label.set_text("…")
            self._match_label.remove_css_class("error")
            return
        if total == 0:
            self._match_label.set_text("No results")
            self._match_label.add_css_class("error")
            return
        self._match_label.remove_css_class("error")
        has_sel, ms, me = self._sel_bounds()
        if has_sel:
            pos = self._search_context.get_occurrence_position(ms, me)
            if pos > 0:
                self._match_label.set_text(f"{pos} / {total}")
                return
        self._match_label.set_text(f"{total} matches")

    def _on_search_option_changed(self):
        self._search_settings.set_case_sensitive(self._case_btn.get_active())
        self._search_settings.set_regex_enabled(self._regex_btn.get_active())
        self._update_search()

    def _replace_one(self):
        has_sel, ms, me = self._sel_bounds()
        if not has_sel:
            self.find_next()
            return
        replacement = self._replace_entry.get_text()
        try:
            self._search_context.replace(ms, me, replacement, -1)
        except Exception:
            pass
        self.find_next()

    def _replace_all(self):
        replacement = self._replace_entry.get_text()
        try:
            count = self._search_context.replace_all(replacement, -1)
            self._update_match_label()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Language / scheme / font                                            #
    # ------------------------------------------------------------------ #

    def _setup_language(self):
        """Detect and set syntax highlighting language."""
        lm = GtkSource.LanguageManager.get_default()

        # Check user-defined associations first
        ext = self.open_file.filename.rsplit(".", 1)[-1] if "." in self.open_file.filename else ""
        if ext:
            assoc = ConfigService.get_preference("syntax_associations", {})
            custom_lang = lm.get_language(assoc[ext]) if ext in assoc else None
            if custom_lang:
                self._buffer.set_language(custom_lang)
                self._buffer.set_highlight_syntax(True)
                return

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
                self._apply_scheme_background(scheme)
                return

        # Fall back to Adwaita light/dark
        style_manager = Adw.StyleManager.get_default()
        if style_manager.get_dark():
            scheme = sm.get_scheme("Adwaita-dark")
        else:
            scheme = sm.get_scheme("Adwaita")

        if scheme:
            self._buffer.set_style_scheme(scheme)
            self._apply_scheme_background(scheme)

        # Listen for system theme changes (only used when no explicit preference)
        style_manager.connect("notify::dark", self._on_theme_changed)

    def _apply_scheme_background(self, scheme):
        """Force the scheme's background/foreground onto the view via CSS.

        Needed because Adwaita's textview CSS can win the cascade over
        GtkSourceView's own scheme CSS in light mode.
        """
        style = scheme.get_style("text")
        css = ""
        if style and style.props.background_set and style.props.background:
            css += f"textview text {{ background-color: {style.props.background}; }}"
        if style and style.props.foreground_set and style.props.foreground:
            css += f" textview text {{ color: {style.props.foreground}; }}"

        if not hasattr(self, "_scheme_bg_css"):
            self._scheme_bg_css = Gtk.CssProvider()
            self._view.get_style_context().add_provider(
                self._scheme_bg_css,
                Gtk.STYLE_PROVIDER_PRIORITY_USER,
            )
        self._scheme_bg_css.load_from_string(css)

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
            self._apply_scheme_background(scheme)

    def apply_scheme(self, scheme_id: str):
        """Apply a style scheme by ID (called when user changes theme)."""
        sm = GtkSource.StyleSchemeManager.get_default()
        scheme = sm.get_scheme(scheme_id)
        if scheme:
            self._buffer.set_style_scheme(scheme)
            self._apply_scheme_background(scheme)

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

    # ------------------------------------------------------------------ #
    #  File I/O                                                            #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  Language queries                                                    #
    # ------------------------------------------------------------------ #

    def get_language_name(self) -> str:
        lang = self._buffer.get_language()
        return lang.get_name() if lang else "Plain Text"

    def get_language_id(self):
        lang = self._buffer.get_language()
        return lang.get_id() if lang else None

    def set_language(self, lang_id):
        lm = GtkSource.LanguageManager.get_default()
        if lang_id:
            lang = lm.get_language(lang_id)
            self._buffer.set_language(lang)
        else:
            self._buffer.set_language(None)
        self._buffer.set_highlight_syntax(bool(lang_id))

    def get_view(self) -> GtkSource.View:
        return self._view
