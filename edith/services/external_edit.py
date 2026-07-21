"""Watch local copies of remote files opened in external applications.

`Open Locally` downloads a remote file to a temp directory and hands it to the
system's default application. This module notices when that local copy is
written back to disk so the change can be uploaded to the server.

Editors rarely write in place — vim, gedit and LibreOffice all save by writing
a sibling temp file and renaming it over the target, which replaces the inode a
file monitor is holding. So we monitor the *containing directory* and match on
file name instead. TempManager gives every file its own subdirectory, so a
watched directory holds exactly one interesting file.
"""

import os

from gi.repository import Gio, GLib

# Editors emit a burst of events per save; coalesce them.
_DEBOUNCE_MS = 600


class _Watch:
    def __init__(self, remote_path, local_path, callback, monitor):
        self.remote_path = remote_path
        self.local_path = local_path
        self.callback = callback
        self.monitor = monitor
        self.timer_id = None


class ExternalEditManager:
    """Tracks locally-opened remote files and reports local saves."""

    def __init__(self):
        self._watches = {}  # remote_path -> _Watch

    def watch(self, remote_path: str, local_path: str, callback):
        """Watch `local_path`; call `callback(remote_path, local_path)` on save.

        Re-watching a path that is already watched replaces the old watch.
        """
        self.unwatch(remote_path)

        directory = Gio.File.new_for_path(os.path.dirname(local_path))
        try:
            monitor = directory.monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None
            )
        except GLib.Error:
            return False

        watch = _Watch(remote_path, local_path, callback, monitor)
        monitor.connect("changed", self._on_dir_changed, watch)
        self._watches[remote_path] = watch
        return True

    def unwatch(self, remote_path: str):
        watch = self._watches.pop(remote_path, None)
        if not watch:
            return
        if watch.timer_id:
            GLib.source_remove(watch.timer_id)
        watch.monitor.cancel()

    def stop_all(self):
        for remote_path in list(self._watches):
            self.unwatch(remote_path)

    @property
    def watched_paths(self):
        return list(self._watches)

    def is_watching(self, remote_path: str) -> bool:
        return remote_path in self._watches

    # ──────────────────────────────────────────────────────────────────────

    def _on_dir_changed(self, monitor, file, other_file, event_type, watch):
        # A rename reports the destination in `other_file`.
        paths = [f.get_path() for f in (file, other_file) if f is not None]
        if watch.local_path not in paths:
            return

        if event_type not in (
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.MOVED_IN,
            Gio.FileMonitorEvent.RENAMED,
        ):
            return

        if watch.timer_id:
            GLib.source_remove(watch.timer_id)
        watch.timer_id = GLib.timeout_add(_DEBOUNCE_MS, self._fire, watch)

    def _fire(self, watch):
        watch.timer_id = None
        # The watch may have been cancelled while the timer was pending.
        if self._watches.get(watch.remote_path) is not watch:
            return GLib.SOURCE_REMOVE
        if os.path.isfile(watch.local_path):
            watch.callback(watch.remote_path, watch.local_path)
        return GLib.SOURCE_REMOVE
