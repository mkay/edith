"""Release notes shown once after an update, and again on demand from About.

The notes themselves live in data/whatsnew.md and are bundled into the GResource,
so they travel with the build and cannot go missing from an install. They are
deliberately not translated: unlike every other string in the app they never pass
through gettext, and stay English whatever the language setting says.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from edith import APP_NAME, VERSION
from edith.i18n import _

RESOURCE_PATH = "/de/singular/edith/whatsnew.md"

# Remembers the version whose notes have been shown. Compared against VERSION, so
# it only ever moves forward — a downgrade re-shows the older version's notes,
# which is the honest thing to do since they describe what is actually running.
SEEN_KEY = "whats_new_seen"


def load_notes() -> list:
    """The bullets from data/whatsnew.md, or an empty list if unreadable.

    Never raises: release notes are a nicety, and a malformed file must not be
    able to stop the app from starting.
    """
    try:
        data = Gio.resources_lookup_data(RESOURCE_PATH, Gio.ResourceLookupFlags.NONE)
        text = data.get_data().decode("utf-8")
    except (GLib.Error, UnicodeDecodeError):
        return []

    # Drop the authoring comment, then treat every remaining non-blank line as one
    # bullet with an optional marker.
    while "<!--" in text and "-->" in text:
        head, _sep, rest = text.partition("<!--")
        text = head + rest.partition("-->")[2]

    bullets = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line[0] in "-*•":
            line = line[1:].strip()
        if line:
            bullets.append(line)
    return bullets


def release_notes_markup() -> str:
    """The notes as the AppStream-flavoured HTML subset Adw.AboutDialog wants."""
    bullets = load_notes()
    if not bullets:
        return ""
    items = "".join(f"<li>{GLib.markup_escape_text(b)}</li>" for b in bullets)
    return f"<ul>{items}</ul>"


class WhatsNewDialog(Adw.AlertDialog):
    """The bullets in a plain dismissable dialog."""

    def __init__(self, bullets: list):
        super().__init__(
            # Translators: dialog title for the release notes shown after an
            # update. The notes themselves are always English.
            heading=_("What's New"),
            body=f"{APP_NAME} {VERSION}",
        )
        self.add_response("close", _("Close"))
        self.set_default_response("close")
        self.set_close_response("close")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for bullet in bullets:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(Gtk.Label(label="•", valign=Gtk.Align.START))
            label = Gtk.Label(
                label=bullet,
                wrap=True,
                xalign=0.0,
                halign=Gtk.Align.START,
                hexpand=True,
            )
            row.append(label)
            box.append(row)

        # Long notes scroll rather than growing the dialog past the screen. The
        # height is a cap, not a request: four or five bullets never reach it.
        scroller = Gtk.ScrolledWindow(
            propagate_natural_height=True,
            max_content_height=320,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            child=box,
        )
        # AlertDialog sizes itself to its heading and body, which leaves the
        # bullets wrapping every few words. Ask for something readable instead.
        scroller.set_size_request(380, -1)
        self.set_extra_child(scroller)


def present(parent):
    """Show the notes unconditionally. Used by the About dialog's entry point."""
    bullets = load_notes()
    if not bullets:
        return
    WhatsNewDialog(bullets).present(parent)


def present_if_updated(parent, is_first_run: bool):
    """Show the notes if this is the first launch on a new version.

    [is_first_run] must be sampled before any config is written this session —
    see EdithApplication.do_activate. A fresh install has nothing "new" to
    report, and greeting a first-time user with release notes is just confusing.
    """
    from edith.services.config import ConfigService

    seen = ConfigService.get_preference(SEEN_KEY)
    if seen == VERSION:
        return

    # Recorded now rather than on dismissal: if the app dies while the dialog is
    # up the notes have still been seen, and re-showing them every launch would
    # be worse than missing them once.
    ConfigService.set_preference(SEEN_KEY, VERSION)

    # No record at all means either a fresh install or an upgrade from a version
    # that predates this feature. Only the latter gets the notes.
    if seen is None and is_first_run:
        return

    present(parent)
