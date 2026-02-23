"""Popover panel showing all queued/active/completed file transfers."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


class _JobRow(Gtk.Box):
    """One row representing a single transfer job."""

    def __init__(self, label, job_id, on_abort):
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            margin_start=12,
            margin_end=8,
            margin_top=8,
            margin_bottom=8,
        )
        self.status = "pending"
        self.job_id = job_id

        top = Gtk.Box(spacing=6)

        self._icon = Gtk.Image(
            icon_name="content-loading-symbolic",
            pixel_size=16,
        )
        top.append(self._icon)

        self._name_label = Gtk.Label(
            label=label,
            xalign=0,
            hexpand=True,
            ellipsize=3,  # END
            max_width_chars=24,
        )
        top.append(self._name_label)

        self._status_label = Gtk.Label(
            label="Queued",
            xalign=1,
            css_classes=["dim-label", "caption"],
        )
        top.append(self._status_label)

        self._abort_btn = Gtk.Button(
            icon_name="window-close-symbolic",
            css_classes=["flat", "circular"],
            valign=Gtk.Align.CENTER,
            tooltip_text="Cancel",
        )
        self._abort_btn.connect(
            "clicked", lambda _: on_abort(self.job_id, self.status)
        )
        top.append(self._abort_btn)

        self.append(top)

        self._progress = Gtk.ProgressBar(visible=False, margin_top=2)
        self.append(self._progress)

    def set_active(self, fraction):
        self.status = "active"
        self._icon.set_from_icon_name("emblem-synchronizing-symbolic")
        if fraction >= 0:
            self._status_label.set_label(f"{int(fraction * 100)} %")
            self._progress.set_fraction(fraction)
            self._progress.set_visible(True)
        else:
            self._status_label.set_label("…")
            self._progress.set_visible(False)

    def set_done(self):
        self.status = "done"
        self._icon.set_from_icon_name("object-select-symbolic")
        self._status_label.set_label("Done")
        self._progress.set_visible(False)
        self._abort_btn.set_visible(False)

    def set_aborted(self):
        self.status = "aborted"
        self._icon.set_from_icon_name("process-stop-symbolic")
        self._status_label.set_label("Cancelled")
        self._status_label.add_css_class("dim-label")
        self._progress.set_visible(False)
        self._abort_btn.set_visible(False)

    def set_failed(self, msg):
        self.status = "failed"
        self._icon.set_from_icon_name("dialog-error-symbolic")
        self._status_label.set_label("Failed")
        self._status_label.remove_css_class("dim-label")
        self._status_label.add_css_class("error")
        self._progress.set_visible(False)
        self._abort_btn.set_visible(False)
        self._name_label.set_tooltip_text(msg)


class TransferPanel(Gtk.Popover):
    """Popover that lists all upload jobs for the current session."""

    def __init__(self):
        super().__init__()
        self._queue = None
        self._rows = {}           # job_id → (_JobRow, Gtk.ListBoxRow)
        self._active_job_id = None

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_size_request(310, -1)

        # Header
        header_box = Gtk.Box(
            margin_start=12,
            margin_end=8,
            margin_top=10,
            margin_bottom=6,
            spacing=8,
        )
        header_label = Gtk.Label(
            label="Transfers",
            xalign=0,
            hexpand=True,
            css_classes=["heading"],
        )
        header_box.append(header_label)

        self._clear_btn = Gtk.Button(
            label="Clear Done",
            css_classes=["flat"],
            visible=False,
            valign=Gtk.Align.CENTER,
        )
        self._clear_btn.connect("clicked", self._on_clear_clicked)
        header_box.append(self._clear_btn)
        outer.append(header_box)
        outer.append(Gtk.Separator())

        self._empty_label = Gtk.Label(
            label="No uploads yet",
            css_classes=["dim-label"],
            margin_top=20,
            margin_bottom=20,
        )
        outer.append(self._empty_label)

        sw = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            propagate_natural_height=True,
            max_content_height=400,
            visible=False,
        )
        self._scroll = sw

        self._list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=["boxed-list"],
            margin_start=8,
            margin_end=8,
            margin_top=6,
            margin_bottom=6,
        )
        sw.set_child(self._list)
        outer.append(sw)

        self.set_child(outer)

    # ── Queue binding ─────────────────────────────────────────────────────────

    def bind_queue(self, queue):
        """Attach to a TransferQueue, clearing any rows from the previous session."""
        self._clear_all_rows()
        self._queue = queue
        self._active_job_id = None
        queue.connect("queued",   self._on_queued)
        queue.connect("started",  self._on_started)
        queue.connect("progress", self._on_progress)
        queue.connect("done",     self._on_done)
        queue.connect("failed",   self._on_failed)

    def unbind_queue(self):
        self._queue = None
        self._active_job_id = None
        self._clear_all_rows()

    def _clear_all_rows(self):
        self._rows.clear()
        child = self._list.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._list.remove(child)
            child = nxt
        self._scroll.set_visible(False)
        self._empty_label.set_visible(True)
        self._clear_btn.set_visible(False)

    # ── Queue signal handlers ─────────────────────────────────────────────────

    def _on_queued(self, queue, label, job_id):
        row = _JobRow(label, job_id, self._on_abort_job)
        list_row = Gtk.ListBoxRow(activatable=False)
        list_row.set_child(row)
        self._list.append(list_row)
        self._rows[job_id] = (row, list_row)
        self._scroll.set_visible(True)
        self._empty_label.set_visible(False)

    def _on_started(self, queue, label, job_id, pending):
        self._active_job_id = job_id
        if job_id in self._rows:
            self._rows[job_id][0].set_active(0.0)

    def _on_progress(self, queue, label, fraction, pending):
        if self._active_job_id is not None and self._active_job_id in self._rows:
            self._rows[self._active_job_id][0].set_active(fraction)

    def _on_done(self, queue, label):
        if self._active_job_id is not None and self._active_job_id in self._rows:
            self._rows[self._active_job_id][0].set_done()
        self._active_job_id = None
        self._update_clear_btn()

    def _on_failed(self, queue, label, msg):
        if self._active_job_id is not None and self._active_job_id in self._rows:
            row = self._rows[self._active_job_id][0]
            if msg == "Aborted":
                row.set_aborted()
            else:
                row.set_failed(msg)
        self._active_job_id = None
        self._update_clear_btn()

    # ── Abort / clear ─────────────────────────────────────────────────────────

    def _on_abort_job(self, job_id, status):
        if not self._queue:
            return
        self._queue.cancel(job_id)
        if status == "pending":
            # Pending cancellation is synchronous — remove row immediately.
            if job_id in self._rows:
                _, list_row = self._rows.pop(job_id)
                self._list.remove(list_row)
            if not self._rows:
                self._scroll.set_visible(False)
                self._empty_label.set_visible(True)
            self._update_clear_btn()
        # For active: the abort raises TransferAborted → "failed" signal fires
        # → _on_failed() updates the row to "Cancelled".

    def _update_clear_btn(self):
        has_finished = any(
            row.status in ("done", "failed", "aborted")
            for row, _ in self._rows.values()
        )
        self._clear_btn.set_visible(has_finished)

    def _on_clear_clicked(self, btn):
        to_remove = [
            jid for jid, (row, _) in self._rows.items()
            if row.status in ("done", "failed", "aborted")
        ]
        for jid in to_remove:
            _, list_row = self._rows.pop(jid)
            self._list.remove(list_row)
        if not self._rows:
            self._scroll.set_visible(False)
            self._empty_label.set_visible(True)
        self._update_clear_btn()
