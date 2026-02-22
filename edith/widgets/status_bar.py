import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GtkSource", "5")

from gi.repository import GObject, Gtk, GtkSource


class StatusBar(Gtk.Box):
    """Status bar showing connection state, transfer progress, and syntax selector."""

    __gsignals__ = {
        "language-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
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
        self._status_icon = Gtk.Image(icon_name="network-offline-symbolic")
        self.append(self._status_icon)

        self._status_label = Gtk.Label(
            label="Disconnected",
            xalign=0,
            hexpand=True,
            ellipsize=3,
            css_classes=["dim-label", "caption"],
        )
        self.append(self._status_label)

        # Transfer spinner (hidden by default)
        self._spinner = Gtk.Spinner(spinning=False, visible=False)
        self.append(self._spinner)

        self._transfer_label = Gtk.Label(
            label="",
            visible=False,
            css_classes=["dim-label", "caption"],
        )
        self.append(self._transfer_label)

        # Syntax selector (right side, hidden until a file is open)
        self._lang_search_entry = None  # initialised in _build_language_popover
        self._syntax_btn = Gtk.MenuButton(
            popover=self._build_language_popover(),
            css_classes=["flat"],
            visible=False,
            valign=Gtk.Align.CENTER,
            tooltip_text="Change syntax highlighting",
        )
        self.append(self._syntax_btn)

    # --- Language popover ---

    def _build_language_popover(self):
        self._lang_search_entry = Gtk.SearchEntry(
            placeholder_text="Filter…",
            margin_start=8,
            margin_end=8,
            margin_top=8,
            margin_bottom=4,
        )

        self._lang_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=["navigation-sidebar"],
        )
        self._lang_list.set_filter_func(self._filter_lang_row)
        self._lang_list.connect("row-activated", self._on_lang_row_activated)

        self._add_lang_row(None, "Plain Text")

        lm = GtkSource.LanguageManager.get_default()
        langs = sorted(
            [lm.get_language(lid) for lid in lm.get_language_ids()],
            key=lambda l: l.get_name().lower(),
        )
        for lang in langs:
            self._add_lang_row(lang.get_id(), lang.get_name())

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
        popover.connect("show", self._on_popover_show)
        return popover

    def _add_lang_row(self, lang_id, name):
        row = Gtk.ListBoxRow()
        row.lang_id = lang_id
        row.lang_name = name
        label = Gtk.Label(
            label=name,
            xalign=0,
            margin_start=8,
            margin_end=8,
            margin_top=4,
            margin_bottom=4,
        )
        row.set_child(label)
        self._lang_list.append(row)

    def _filter_lang_row(self, row):
        query = self._lang_search_entry.get_text().lower().strip()
        if not query:
            return True
        return query in row.lang_name.lower()

    def _on_popover_show(self, popover):
        self._lang_search_entry.set_text("")
        self._lang_list.invalidate_filter()
        self._lang_search_entry.grab_focus()

    def _on_lang_row_activated(self, list_box, row):
        self._syntax_btn.get_popover().popdown()
        self.emit("language-selected", row.lang_id or "")

    # --- Public API ---

    def set_status(self, state: str, message: str):
        """Update the status display.

        state: "disconnected", "connecting", "connected", "downloading",
               "uploading", "error"
        """
        self._status_label.set_label(message)

        icon_map = {
            "disconnected": "network-offline-symbolic",
            "connecting": "network-transmit-symbolic",
            "connected": "network-idle-symbolic",
            "downloading": "network-receive-symbolic",
            "uploading": "network-transmit-symbolic",
            "error": "dialog-error-symbolic",
        }
        self._status_icon.set_from_icon_name(icon_map.get(state, "network-offline-symbolic"))

        is_transferring = state in ("downloading", "uploading", "connecting")
        self._spinner.set_visible(is_transferring)
        self._spinner.set_spinning(is_transferring)

    def set_transfer_progress(self, text: str):
        """Show transfer progress text."""
        if text:
            self._transfer_label.set_label(text)
            self._transfer_label.set_visible(True)
        else:
            self._transfer_label.set_visible(False)

    def set_language_name(self, name: str):
        """Update the syntax button label and show it."""
        self._syntax_btn.set_label(name)
        self._syntax_btn.set_visible(True)

    def hide_language_selector(self):
        """Hide the syntax selector (no file open)."""
        self._syntax_btn.set_visible(False)
