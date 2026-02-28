import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GLib, GObject, Gtk, Pango

# Non-current segments ellipsize at ~10 chars, current at ~40
_ELLIPSIZE_MIN = 7
_ELLIPSIZE_CURRENT = 28

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
.path-bar-scroll undershoot.left {
    background: linear-gradient(to right, alpha(@headerbar_bg_color, 0.95) 0px, transparent 20px);
}
.path-bar-scroll undershoot.right {
    background: linear-gradient(to left, alpha(@headerbar_bg_color, 0.95) 0px, transparent 20px);
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

        # Root button lives outside the scroll window so it's always visible
        self._root_btn = Gtk.Button(css_classes=["path-segment"])
        self._root_btn.set_child(Gtk.Image(icon_name="edith-drive-symbolic"))
        self._root_btn.connect("clicked", lambda _b: self.emit("navigate", "/"))
        self.append(self._root_btn)

        self._sw = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            hexpand=True,
            valign=Gtk.Align.CENTER,
            css_classes=["path-bar-scroll"],
        )
        self._buttons_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            valign=Gtk.Align.CENTER,
        )
        self._sw.set_child(self._buttons_box)
        self.append(self._sw)

        # Convert vertical scroll to horizontal navigation
        scroll_ctrl = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll_ctrl.connect("scroll", self._on_scroll)
        self._sw.add_controller(scroll_ctrl)

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

        # Style root as dim when not at root, bold when at root
        if parts:
            self._root_btn.remove_css_class("heading")
            self._root_btn.add_css_class("dim-label")
        else:
            self._root_btn.remove_css_class("dim-label")
            self._root_btn.add_css_class("heading")

        # Segments after root go into the scrollable area
        for i, (seg_path, label) in enumerate(zip(cumulative[1:], labels[1:]), start=1):
            is_current = (i == len(cumulative) - 1)

            sep = Gtk.Label(
                label="/",
                css_classes=["dim-label"],
                margin_start=1,
                margin_end=1,
            )
            self._buttons_box.append(sep)

            inner = Gtk.Label(label=label)
            if is_current:
                inner.add_css_class("heading")
            min_chars = _ELLIPSIZE_CURRENT if is_current else _ELLIPSIZE_MIN
            if len(label) > min_chars * 1.5:
                inner.set_width_chars(min_chars)
                inner.set_ellipsize(Pango.EllipsizeMode.MIDDLE)

            btn = Gtk.Button(css_classes=["path-segment"])
            if not is_current:
                btn.add_css_class("dim-label")
            btn.set_child(inner)
            btn.connect("clicked", lambda _b, p=seg_path: self.emit("navigate", p))
            self._buttons_box.append(btn)

        # Scroll to rightmost end so current directory is always visible
        GLib.idle_add(self._scroll_to_end)

    def _on_scroll(self, ctrl, dx, dy):
        if dy == 0:
            return False
        adj = self._sw.get_hadjustment()
        adj.set_value(adj.get_value() + dy * adj.get_step_increment())
        return True

    def _scroll_to_end(self):
        adj = self._sw.get_hadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False
