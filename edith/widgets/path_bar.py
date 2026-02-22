import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GLib, GObject, Gtk

_CSS = """
button.path-segment {
    min-height: 0;
    min-width: 0;
    padding: 3px 8px;
    border-radius: 6px;
    border: none;
    background: none;
    box-shadow: none;
    outline: none;
}
button.path-segment:hover {
    background-color: alpha(@window_fg_color, 0.08);
}
button.path-segment:active {
    background-color: alpha(@window_fg_color, 0.16);
}
"""


class PathBar(Gtk.Box):
    """Clickable breadcrumb path widget for the title bar."""

    __gsignals__ = {
        "navigate": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__(
            orientation=Gtk.Orientation.HORIZONTAL,
            valign=Gtk.Align.CENTER,
        )

        css = Gtk.CssProvider()
        css.load_from_string(_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self._sw = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        self._buttons_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            valign=Gtk.Align.CENTER,
        )
        self._sw.set_child(self._buttons_box)
        self.append(self._sw)

    def set_path(self, path: str):
        # Clear existing children
        child = self._buttons_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._buttons_box.remove(child)
            child = nxt

        # Build (cumulative_path, segment_label) pairs
        parts = [p for p in path.split("/") if p]
        cumulative = ["/"]
        for part in parts:
            cumulative.append(cumulative[-1].rstrip("/") + "/" + part)
        labels = ["/"] + parts

        for i, (seg_path, label) in enumerate(zip(cumulative, labels)):
            is_current = (i == len(cumulative) - 1)
            is_root = (i == 0)

            if not is_root:
                sep = Gtk.Label(
                    label="/",
                    css_classes=["dim-label"],
                    margin_start=1,
                    margin_end=1,
                )
                self._buttons_box.append(sep)

            if is_root:
                inner = Gtk.Image(icon_name="drive-harddisk-symbolic")
            else:
                inner = Gtk.Label(label=label)
                if is_current:
                    inner.add_css_class("heading")

            btn = Gtk.Button(css_classes=["path-segment"])
            if not is_current:
                btn.add_css_class("dim-label")
            btn.set_child(inner)
            btn.connect("clicked", lambda _b, p=seg_path: self.emit("navigate", p))
            self._buttons_box.append(btn)

        # Scroll to rightmost end so current directory is always visible
        GLib.idle_add(self._scroll_to_end)

    def _scroll_to_end(self):
        adj = self._sw.get_hadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False
