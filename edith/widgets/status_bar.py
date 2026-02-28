import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GObject, Gtk

from edith.monaco_languages import MONACO_LANGUAGES
from edith.services.config import ConfigService


class StatusBar(Gtk.Box):
    """Status bar: connection state, transfer progress, syntax, indent, line ending."""

    __gsignals__ = {
        "language-selected":   (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "indent-changed":      (GObject.SignalFlags.RUN_FIRST, None, (bool, int)),
        "line-ending-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "cursor-clicked":      (GObject.SignalFlags.RUN_FIRST, None, ()),
        "wrap-toggled":        (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            margin_start=8,
            margin_end=8,
            margin_top=4,
            margin_bottom=4,
        )
        self.add_css_class("toolbar")

        # Connection status icon + label
        self._status_icon = Gtk.Image.new_from_icon_name("edith-disconnected-symbolic")
        self._status_icon.set_pixel_size(16)
        self.append(self._status_icon)

        self._status_label = Gtk.Label(
            label="Disconnected",
            xalign=0,
            ellipsize=3,
            css_classes=["dim-label", "caption"],
        )
        self.append(self._status_label)

        # Spacer
        self._spacer = Gtk.Box(hexpand=True)
        self.append(self._spacer)

        # Transfer area (hidden by default)
        self._spinner = Gtk.Spinner(spinning=False, visible=False)
        self.append(self._spinner)

        self._transfer_label = Gtk.Label(
            label="",
            visible=False,
            ellipsize=3,
            max_width_chars=32,
            css_classes=["dim-label", "caption"],
        )
        self.append(self._transfer_label)

        self._progress_bar = Gtk.ProgressBar(
            visible=False,
            valign=Gtk.Align.CENTER,
            width_request=90,
            show_text=False,
        )
        self._progress_bar.add_css_class("osd")
        self.append(self._progress_bar)

        # ── File-info buttons (hidden when no file is open) ──────────── #
        self._cursor_btn = Gtk.Button(
            label="Ln 1, Col 1",
            css_classes=["flat"],
            visible=False,
            valign=Gtk.Align.CENTER,
            tooltip_text="Go to line",
        )
        self._cursor_btn.connect("clicked", lambda _: self.emit("cursor-clicked"))
        self.append(self._cursor_btn)

        self._wrap_btn = Gtk.Button(
            label="Wrap",
            css_classes=["flat"],
            visible=False,
            valign=Gtk.Align.CENTER,
            tooltip_text="Toggle word wrap (Ctrl+Shift+W)",
        )
        self._wrap_btn.connect("clicked", lambda _: self.emit("wrap-toggled"))
        self.append(self._wrap_btn)

        self._lang_search_entry = None  # set by _build_language_popover
        self._syntax_btn = Gtk.MenuButton(
            popover=self._build_language_popover(),
            css_classes=["flat"],
            visible=False,
            valign=Gtk.Align.CENTER,
            tooltip_text="Change syntax highlighting",
        )
        self.append(self._syntax_btn)

        self._indent_btn = Gtk.MenuButton(
            popover=self._build_indent_popover(),
            css_classes=["flat"],
            visible=False,
            valign=Gtk.Align.CENTER,
            tooltip_text="Change indentation",
        )
        self.append(self._indent_btn)

        self._eol_btn = Gtk.MenuButton(
            popover=self._build_eol_popover(),
            css_classes=["flat"],
            visible=False,
            valign=Gtk.Align.CENTER,
            tooltip_text="Change line ending",
        )
        self.append(self._eol_btn)

    # ── Language popover ─────────────────────────────────────────────── #

    def _build_language_popover(self):
        self._lang_search_entry = Gtk.SearchEntry(
            placeholder_text="Filter…",
            margin_start=8, margin_end=8, margin_top=8, margin_bottom=4,
        )

        self._lang_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=["navigation-sidebar"],
        )
        self._lang_list.set_filter_func(self._filter_lang_row)
        self._lang_list.connect("row-activated", self._on_lang_row_activated)

        self._add_lang_row("plaintext", "Plain Text")
        for lang_id, display_name in MONACO_LANGUAGES:
            if lang_id != "plaintext":
                self._add_lang_row(lang_id, display_name)

        sw = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            propagate_natural_height=True,
            max_content_height=300,
        )
        sw.set_child(self._lang_list)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_size_request(220, -1)
        box.append(self._lang_search_entry)
        box.append(sw)
        self._lang_search_entry.connect(
            "search-changed", lambda _: self._lang_list.invalidate_filter()
        )

        popover = Gtk.Popover()
        popover.set_child(box)
        popover.connect("show", self._on_lang_popover_show)
        return popover

    def _add_lang_row(self, lang_id, name):
        row = Gtk.ListBoxRow()
        row.lang_id = lang_id
        row.lang_name = name
        row.set_child(Gtk.Label(
            label=name, xalign=0,
            margin_start=8, margin_end=8, margin_top=4, margin_bottom=4,
        ))
        self._lang_list.append(row)

    def _filter_lang_row(self, row):
        q = self._lang_search_entry.get_text().lower().strip()
        return not q or q in row.lang_name.lower()

    def _on_lang_popover_show(self, popover):
        self._lang_search_entry.set_text("")
        self._lang_list.invalidate_filter()
        self._lang_search_entry.grab_focus()

    def _on_lang_row_activated(self, list_box, row):
        self._syntax_btn.get_popover().popdown()
        self.emit("language-selected", row.lang_id or "")

    # ── Indent popover ───────────────────────────────────────────────── #

    def _build_indent_popover(self):
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_start=8, margin_end=8, margin_top=8, margin_bottom=8,
            spacing=6,
        )
        box.set_size_request(180, -1)

        # Spaces / Tabs radio
        type_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._spaces_radio = Gtk.CheckButton(label="Spaces")
        self._tabs_radio = Gtk.CheckButton(label="Tabs", group=self._spaces_radio)
        type_box.append(self._spaces_radio)
        type_box.append(self._tabs_radio)
        box.append(type_box)

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Size buttons
        size_label = Gtk.Label(label="Size:", xalign=0, css_classes=["dim-label"])
        box.append(size_label)

        size_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._size_btns = {}
        size_group = None
        for n in (2, 3, 4, 8):
            btn = Gtk.ToggleButton(label=str(n))
            if size_group is None:
                size_group = btn
            else:
                btn.set_group(size_group)
            btn.connect("toggled", self._on_size_btn_toggled, n)
            size_box.append(btn)
            self._size_btns[n] = btn
        box.append(size_box)

        self._spaces_radio.connect("toggled", self._on_type_toggled)

        popover = Gtk.Popover()
        popover.set_child(box)
        popover.connect("show", self._on_indent_popover_show)
        return popover

    def _on_indent_popover_show(self, popover):
        insert_spaces = ConfigService.get_preference("editor_insert_spaces", True)
        tab_size = ConfigService.get_preference("editor_tab_size", 4)

        self._spaces_radio.set_active(insert_spaces)
        self._tabs_radio.set_active(not insert_spaces)

        closest = min(self._size_btns.keys(), key=lambda k: abs(k - tab_size))
        # Activate without triggering the signal
        self._updating_indent = True
        for n, btn in self._size_btns.items():
            btn.set_active(n == closest)
        self._updating_indent = False

    def _on_type_toggled(self, radio):
        if not radio.get_active():
            return
        self._emit_indent_changed()

    def _on_size_btn_toggled(self, btn, size):
        if not btn.get_active():
            return
        if getattr(self, "_updating_indent", False):
            return
        self._emit_indent_changed()

    def _emit_indent_changed(self):
        insert_spaces = self._spaces_radio.get_active()
        tab_size = next(
            (n for n, b in self._size_btns.items() if b.get_active()), 4
        )
        ConfigService.set_preference("editor_insert_spaces", insert_spaces)
        ConfigService.set_preference("editor_tab_size", tab_size)
        self._update_indent_label(insert_spaces, tab_size)
        self.emit("indent-changed", insert_spaces, tab_size)

    def _update_indent_label(self, insert_spaces, tab_size):
        label = f"Spaces: {tab_size}" if insert_spaces else f"Tab: {tab_size}"
        self._indent_btn.set_label(label)

    # ── EOL popover ──────────────────────────────────────────────────── #

    def _build_eol_popover(self):
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_start=4, margin_end=4, margin_top=4, margin_bottom=4,
        )

        eol_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=["navigation-sidebar"],
        )
        for eol_id, label in (("lf", "LF (Unix)"), ("crlf", "CRLF (Windows)")):
            row = Gtk.ListBoxRow()
            row.eol_id = eol_id
            row.set_child(Gtk.Label(
                label=label, xalign=0,
                margin_start=8, margin_end=8, margin_top=4, margin_bottom=4,
            ))
            eol_list.append(row)

        eol_list.connect("row-activated", self._on_eol_row_activated)
        box.append(eol_list)

        popover = Gtk.Popover()
        popover.set_child(box)
        return popover

    def _on_eol_row_activated(self, list_box, row):
        self._eol_btn.get_popover().popdown()
        eol = row.eol_id
        self._eol_btn.set_label(eol.upper())
        self.emit("line-ending-changed", eol)

    # ── Public API ───────────────────────────────────────────────────── #

    def set_status(self, state: str, message: str):
        self._status_label.set_label(message)
        app_icons = {
            "disconnected": "edith-disconnected-symbolic",
            "connected":    "edith-connected-symbolic",
        }
        system_icons = {
            "connecting":  "network-transmit-symbolic",
            "downloading": "network-receive-symbolic",
            "uploading":   "network-transmit-symbolic",
            "error":       "dialog-error-symbolic",
        }
        if state in app_icons:
            self._status_icon.set_from_icon_name(app_icons[state])
        else:
            self._status_icon.set_from_icon_name(
                system_icons.get(state, "network-offline-symbolic")
            )
        is_transferring = state in ("downloading", "uploading", "connecting")
        self._spinner.set_visible(is_transferring)
        self._spinner.set_spinning(is_transferring)

    def show_transfer(self, label: str, fraction: float, pending: int):
        self._spinner.set_visible(True)
        self._spinner.set_spinning(True)
        text = label
        if fraction >= 0:
            text += f"  {int(fraction * 100)} %"
        if pending > 0:
            text += f"  (+{pending})"
        self._transfer_label.set_label(text)
        self._transfer_label.set_visible(True)
        if fraction >= 0:
            self._progress_bar.set_fraction(fraction)
            self._progress_bar.set_visible(True)
        else:
            self._progress_bar.set_visible(False)

    def clear_transfer(self):
        self._spinner.set_spinning(False)
        self._spinner.set_visible(False)
        self._transfer_label.set_visible(False)
        self._progress_bar.set_visible(False)

    def set_transfer_progress(self, text: str):
        if text:
            self._transfer_label.set_label(text)
            self._transfer_label.set_visible(True)
            self._spinner.set_visible(True)
            self._spinner.set_spinning(True)
        else:
            self._transfer_label.set_visible(False)

    def set_cursor_position(self, line: int, col: int):
        self._cursor_btn.set_label(f"Ln {line}, Col {col}")
        self._cursor_btn.set_visible(True)

    def set_word_wrap(self, enabled: bool):
        self._wrap_btn.set_label("Wrap" if enabled else "No Wrap")
        self._wrap_btn.set_visible(True)

    def set_language_name(self, name: str):
        self._syntax_btn.set_label(name)
        self._syntax_btn.set_visible(True)

    def set_indent(self, insert_spaces: bool, tab_size: int):
        self._update_indent_label(insert_spaces, tab_size)
        self._indent_btn.set_visible(True)

    def set_line_ending(self, eol: str):
        self._eol_btn.set_label(eol.upper())
        self._eol_btn.set_visible(True)

    def show_file_info(self):
        """Show all file-info buttons (language, indent, EOL)."""
        self._syntax_btn.set_visible(True)
        self._indent_btn.set_visible(True)
        self._eol_btn.set_visible(True)

    def hide_file_info(self):
        """Hide all file-info buttons (no file open)."""
        self._cursor_btn.set_visible(False)
        self._wrap_btn.set_visible(False)
        self._syntax_btn.set_visible(False)
        self._indent_btn.set_visible(False)
        self._eol_btn.set_visible(False)

    # kept for compatibility
    def hide_language_selector(self):
        self.hide_file_info()

    def hide_connection_status(self):
        self._status_icon.set_visible(False)
        self._status_label.set_visible(False)
