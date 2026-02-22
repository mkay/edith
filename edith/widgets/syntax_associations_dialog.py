import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")

from gi.repository import Adw, Gtk, GtkSource

from edith.services.config import ConfigService


class SyntaxAssociationsDialog(Adw.PreferencesDialog):
    """Settings dialog for custom file extension → syntax language mappings."""

    def __init__(self):
        super().__init__(title="Syntax Associations", search_enabled=False)

        self._selected_lang_id = None
        self._selected_lang_name = "Select language…"
        self._lang_search_entry = None  # set by _build_lang_popover
        self._assoc_rows = []

        page = Adw.PreferencesPage()

        # --- Add new association ---
        add_group = Adw.PreferencesGroup(title="Add Association")

        add_btn = Gtk.Button(
            label="Add",
            css_classes=["suggested-action"],
            valign=Gtk.Align.CENTER,
        )
        add_btn.connect("clicked", self._on_add)
        add_group.set_header_suffix(add_btn)

        self._ext_row = Adw.EntryRow(title="Extension (without dot, e.g. tpl)")
        add_group.add(self._ext_row)

        self._lang_btn = Gtk.MenuButton(
            label=self._selected_lang_name,
            popover=self._build_lang_popover(),
            css_classes=["flat"],
            valign=Gtk.Align.CENTER,
        )
        lang_row = Adw.ActionRow(title="Language")
        lang_row.add_suffix(self._lang_btn)
        lang_row.set_activatable_widget(self._lang_btn)
        add_group.add(lang_row)

        page.add(add_group)

        # --- Current associations ---
        self._assoc_group = Adw.PreferencesGroup(title="Current Associations")
        page.add(self._assoc_group)

        self.add(page)
        self._rebuild_assoc_list()

    # --- Language popover (shared with status bar approach) ---

    def _build_lang_popover(self):
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
        popover.connect("show", self._on_lang_popover_show)
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
        return not query or query in row.lang_name.lower()

    def _on_lang_popover_show(self, popover):
        self._lang_search_entry.set_text("")
        self._lang_list.invalidate_filter()
        self._lang_search_entry.grab_focus()

    def _on_lang_row_activated(self, list_box, row):
        self._lang_btn.get_popover().popdown()
        self._selected_lang_id = row.lang_id
        self._selected_lang_name = row.lang_name
        self._lang_btn.set_label(row.lang_name)

    # --- Add / delete ---

    def _on_add(self, btn):
        ext = self._ext_row.get_text().strip().lstrip(".")
        if not ext or not self._selected_lang_id:
            return

        assoc = ConfigService.get_preference("syntax_associations", {})
        assoc[ext] = self._selected_lang_id
        ConfigService.set_preference("syntax_associations", assoc)

        self._ext_row.set_text("")
        self._selected_lang_id = None
        self._selected_lang_name = "Select language…"
        self._lang_btn.set_label(self._selected_lang_name)

        self._rebuild_assoc_list()

    def _delete_association(self, ext):
        assoc = ConfigService.get_preference("syntax_associations", {})
        assoc.pop(ext, None)
        ConfigService.set_preference("syntax_associations", assoc)
        self._rebuild_assoc_list()

    def _rebuild_assoc_list(self):
        for row in self._assoc_rows:
            self._assoc_group.remove(row)
        self._assoc_rows = []

        assoc = ConfigService.get_preference("syntax_associations", {})
        if not assoc:
            row = Adw.ActionRow(title="No custom associations yet")
            row.set_sensitive(False)
            self._assoc_group.add(row)
            self._assoc_rows.append(row)
            return

        lm = GtkSource.LanguageManager.get_default()
        for ext, lang_id in sorted(assoc.items()):
            lang = lm.get_language(lang_id)
            lang_name = lang.get_name() if lang else lang_id

            row = Adw.ActionRow(title=f".{ext}", subtitle=lang_name)

            del_btn = Gtk.Button(
                icon_name="edit-delete-symbolic",
                css_classes=["flat", "destructive-action"],
                valign=Gtk.Align.CENTER,
                tooltip_text=f"Remove .{ext} association",
            )
            del_btn.connect("clicked", lambda _b, e=ext: self._delete_association(e))
            row.add_suffix(del_btn)

            self._assoc_group.add(row)
            self._assoc_rows.append(row)
