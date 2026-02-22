"""Bridge between background threads and the GTK main loop."""

import threading
import traceback

from gi.repository import GLib


def run_async(task, on_success, on_error):
    """Run `task()` in a thread; deliver result via GLib.idle_add.

    Args:
        task: Callable that does blocking work (runs in thread).
        on_success: Called on GTK main thread with the return value.
        on_error: Called on GTK main thread with the exception.
    """

    def worker():
        try:
            result = task()
            GLib.idle_add(_deliver_success, result)
        except Exception as e:
            traceback.print_exc()
            GLib.idle_add(_deliver_error, e)

    def _deliver_success(result):
        on_success(result)
        return GLib.SOURCE_REMOVE

    def _deliver_error(error):
        on_error(error)
        return GLib.SOURCE_REMOVE

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t
