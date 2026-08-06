# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

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
            # Nothing launched from a .desktop file has a stderr anyone reads,
            # so the traceback above vanishes in exactly the situation where a
            # user reports "it crashed". Keep a durable copy.
            try:
                from edith.services.freeze_watchdog import record
                record(
                    f"background task failed: {type(e).__name__}: {e}\n"
                    + traceback.format_exc()
                )
            except Exception:
                pass
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
