"""Serialized file-transfer queue with per-item progress reporting."""

import threading
import traceback
from collections import deque

from gi.repository import GLib, GObject


class TransferAborted(Exception):
    """Raised inside a progress callback to abort the active transfer."""


class TransferQueue(GObject.Object):
    """Run upload tasks one at a time and stream progress to GTK.

    Signals
    -------
    queued(label, job_id)
        Fired on the main thread when a job is enqueued (before it starts).
    started(label, job_id, pending)
        Job began executing; ``pending`` = items still waiting after this one.
    progress(label, fraction, pending)
        Byte-level progress (fraction 0–1).
    done(label)
        Job completed successfully.
    failed(label, msg)
        Job failed or was aborted (msg == "Aborted" for user cancellation).
    idle
        Queue fully drained.
    """

    __gsignals__ = {
        "queued":   (GObject.SignalFlags.RUN_FIRST, None, (str, int)),
        "started":  (GObject.SignalFlags.RUN_FIRST, None, (str, int, int)),
        "progress": (GObject.SignalFlags.RUN_FIRST, None, (str, float, int)),
        "done":     (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "failed":   (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "idle":     (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__()
        self._queue = deque()
        self._lock = threading.Lock()
        self._worker_running = False
        self._next_id = 0
        self._active_job_id = None
        self._cancel_event = threading.Event()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def pending(self) -> int:
        """Items waiting (not counting the currently active one)."""
        with self._lock:
            return len(self._queue)

    @property
    def is_busy(self) -> bool:
        """True if a transfer is active or items are waiting."""
        with self._lock:
            return self._worker_running

    def enqueue(self, label: str, task, on_success=None, on_error=None) -> int:
        """Add a job. Returns its job_id.

        ``task`` is called in a background thread as ``task(progress_cb)``.
        ``progress_cb(bytes_done, bytes_total)`` may raise ``TransferAborted``
        if the user cancels the job — callers must let that propagate.
        """
        with self._lock:
            job_id = self._next_id
            self._next_id += 1
            self._queue.append((job_id, label, task, on_success, on_error))
            start = not self._worker_running
            if start:
                self._worker_running = True
        # enqueue() is always called on the main thread, so emit directly.
        self.emit("queued", label, job_id)
        if start:
            threading.Thread(target=self._run, daemon=True).start()
        return job_id

    def cancel(self, job_id: int) -> bool:
        """Cancel by ID. Aborts if active; removes silently if still pending."""
        with self._lock:
            if self._active_job_id == job_id:
                self._cancel_event.set()
                return True
            before = len(self._queue)
            self._queue = deque(
                item for item in self._queue if item[0] != job_id
            )
            return len(self._queue) < before

    def clear(self):
        """Discard all pending (not yet started) jobs."""
        with self._lock:
            self._queue.clear()

    # ── Worker (background thread) ────────────────────────────────────────────

    def _run(self):
        while True:
            with self._lock:
                if not self._queue:
                    self._worker_running = False
                    self._active_job_id = None
                    GLib.idle_add(self._cb_idle)
                    return
                job_id, label, task, on_success, on_error = self._queue.popleft()
                pending = len(self._queue)
                self._active_job_id = job_id
                self._cancel_event.clear()

            GLib.idle_add(self._cb_started, label, job_id, pending)
            progress_cb = self._make_progress_cb(label, pending)

            try:
                result = task(progress_cb)
                GLib.idle_add(self._cb_done, label)
                if on_success:
                    GLib.idle_add(on_success, result)
            except TransferAborted:
                GLib.idle_add(self._cb_failed, label, "Aborted")
                if on_error:
                    GLib.idle_add(on_error, TransferAborted())
            except Exception as exc:
                traceback.print_exc()
                GLib.idle_add(self._cb_failed, label, str(exc))
                if on_error:
                    GLib.idle_add(on_error, exc)

    def _make_progress_cb(self, label: str, pending: int):
        """Return a progress callback that checks for cancellation and throttles."""
        cancel = self._cancel_event
        last_pct = [-1]

        def cb(done: int, total: int):
            if cancel.is_set():
                raise TransferAborted()
            if total <= 0:
                return
            pct = done * 100 // total
            if pct == last_pct[0]:
                return
            last_pct[0] = pct
            GLib.idle_add(self._cb_progress, label, done / total, pending)

        return cb

    # ── idle_add targets (main thread) ────────────────────────────────────────

    def _cb_idle(self):
        self.emit("idle")
        return GLib.SOURCE_REMOVE

    def _cb_started(self, label, job_id, pending):
        self.emit("started", label, job_id, pending)
        return GLib.SOURCE_REMOVE

    def _cb_progress(self, label, fraction, pending):
        self.emit("progress", label, fraction, pending)
        return GLib.SOURCE_REMOVE

    def _cb_done(self, label):
        self.emit("done", label)
        return GLib.SOURCE_REMOVE

    def _cb_failed(self, label, msg):
        self.emit("failed", label, msg)
        return GLib.SOURCE_REMOVE
