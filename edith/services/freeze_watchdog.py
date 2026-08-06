# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Catch main-loop freezes without needing anyone to be watching.

An intermittent deadlock is close to impossible to debug by hand: by the time
you notice the UI is dead, you have to still be near a terminal and remember
to signal the process. This does it automatically — a heartbeat on the GTK
main loop, and a background thread that dumps every thread's stack the moment
the heartbeat stops.

The dump goes to stderr and to a file under the user's cache directory, so the
evidence survives even when the app was started from a desktop launcher and
stderr goes nowhere useful.
"""

import faulthandler
import logging
import os
import threading
import time
from pathlib import Path

from gi.repository import GLib

log = logging.getLogger(__name__)

# How often the main loop is expected to tick, and how far behind it has to
# fall before we call it frozen.  Well above normal jank (a big directory
# listing or a slow WebKit layout can block for a beat) but far below the
# point where a user has given up and killed the app.
_BEAT_INTERVAL_MS = 1000
_STALL_THRESHOLD_S = 10.0
_RECHECK_INTERVAL_S = 1.0

# Keep the log from growing without bound if something stalls repeatedly.
_MAX_LOG_BYTES = 1024 * 1024


def _log_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    try:
        d = Path(base) / "edith"
        d.mkdir(parents=True, exist_ok=True)
        return d / "freeze-dump.log"
    except OSError:
        return None


def record(text):
    """Append a line to the diagnostic log. Never raises."""
    path = _log_path()
    if path is None:
        return
    try:
        with open(path, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except OSError:
        pass


def install_exit_logging():
    """Record how the process ends.

    Edith's stderr goes nowhere when it's started from a desktop launcher, so
    an unexplained "it just quit" leaves no trace at all. This distinguishes a
    clean shutdown from an uncaught exception from a kill.
    """
    import atexit
    import sys

    record(f"=== started (pid {os.getpid()}) ===")

    def on_exception(exc_type, exc, tb):
        import traceback
        record(f"!!! uncaught exception in main thread: {exc_type.__name__}: {exc}")
        record("".join(traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)

    def on_thread_exception(args):
        import traceback
        record(
            f"!!! uncaught exception in thread {args.thread.name if args.thread else '?'}: "
            f"{args.exc_type.__name__}: {args.exc_value}"
        )
        record("".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback)))

    sys.excepthook = on_exception
    threading.excepthook = on_thread_exception
    # Reached on a normal interpreter exit. Its *absence* in the log means the
    # process was killed or died on a fatal signal.
    atexit.register(lambda: record(f"=== exited normally (pid {os.getpid()}) ==="))


class FreezeWatchdog:
    def __init__(self, threshold=_STALL_THRESHOLD_S):
        self._threshold = threshold
        self._last_beat = time.monotonic()
        self._lock = threading.Lock()
        self._reported = False
        self._dump_path = self._resolve_dump_path()

    @staticmethod
    def _resolve_dump_path():
        return _log_path()

    def start(self):
        GLib.timeout_add(_BEAT_INTERVAL_MS, self._beat)
        threading.Thread(target=self._watch, daemon=True,
                         name="edith-freeze-watchdog").start()

    # ── main thread ───────────────────────────────────────────────────── #

    def _beat(self):
        recovered_after = None
        with self._lock:
            now = time.monotonic()
            if self._reported:
                # Came back to life — a stall, not a permanent deadlock.
                recovered_after = now - self._last_beat
                self._reported = False
            self._last_beat = now
        if recovered_after is not None:
            # Record this in the dump file too. Without it a report is
            # ambiguous: a 10s hiccup and a permanent deadlock both leave
            # exactly one entry, and only the absence of a recovery line
            # tells them apart.
            log.warning("main loop recovered after %.1fs", recovered_after)
            self._append(
                f"----- recovered after {recovered_after:.1f}s "
                f"(the block above was temporary) -----\n"
            )
        return GLib.SOURCE_CONTINUE

    # ── watchdog thread ───────────────────────────────────────────────── #

    def _watch(self):
        while True:
            time.sleep(_RECHECK_INTERVAL_S)
            with self._lock:
                stalled_for = time.monotonic() - self._last_beat
                already_reported = self._reported
                if stalled_for > self._threshold and not already_reported:
                    self._reported = True
                else:
                    continue
            # Dump outside the lock: the main thread must never block on us.
            self._dump(stalled_for)

    def _dump(self, stalled_for):
        header = (
            f"\n===== edith: main loop blocked for {stalled_for:.1f}s "
            f"(pid {os.getpid()}) =====\n"
        )
        try:
            faulthandler.dump_traceback(all_threads=True)
        except Exception:  # noqa: BLE001 - diagnostics must never raise
            pass
        log.error(header.strip())

        if self._dump_path is None:
            return
        self._rotate_if_large()
        try:
            with open(self._dump_path, "a") as f:
                f.write(header)
                f.write(time.strftime("%Y-%m-%d %H:%M:%S\n"))
                faulthandler.dump_traceback(file=f, all_threads=True)
                f.flush()
        except OSError:
            pass

    def _append(self, text):
        if self._dump_path is None:
            return
        try:
            with open(self._dump_path, "a") as f:
                f.write(text)
        except OSError:
            pass

    def _rotate_if_large(self):
        try:
            if self._dump_path.stat().st_size > _MAX_LOG_BYTES:
                self._dump_path.replace(
                    self._dump_path.with_suffix(".log.1")
                )
        except OSError:
            pass


def install(threshold=_STALL_THRESHOLD_S):
    """Start watching the main loop. Safe to call once at startup."""
    install_exit_logging()
    watchdog = FreezeWatchdog(threshold)
    watchdog.start()
    return watchdog
