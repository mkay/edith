# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Expose remote files to other applications as a drag payload.

Dragging a row out of the file browser has to hand the receiving application
something it understands — in practice `text/uri-list` pointing at real files.
Remote files aren't local, so they have to be downloaded first.

The download is deferred until the receiver actually asks for the data, which
only happens on drop. Dragging a file around and releasing it over nothing
therefore costs nothing.
"""

import os
import threading

from gi.repository import Gdk, Gio, GLib

from edith.i18n import _, ngettext


class _Cancel:
    """Adapt a Gio.Cancellable to the Event-like API the clients expect."""

    def __init__(self, cancellable):
        self._cancellable = cancellable

    def is_set(self):
        return self._cancellable is not None and self._cancellable.is_cancelled()


class RemoteFilesProvider(Gdk.ContentProvider):
    """A `text/uri-list` provider that downloads on demand."""

    __gtype_name__ = "EdithRemoteFilesProvider"

    def __init__(self, client, file_infos, on_status=None):
        super().__init__()
        self._client = client
        self._file_infos = list(file_infos)
        self._on_status = on_status
        self._uris = None          # cached result of a completed download
        self._started = False      # a download is running or has finished
        self._waiters = []         # requests parked until that download lands
        self._lock = threading.Lock()

    # ── GdkContentProvider vfuncs ─────────────────────────────────────── #

    def do_ref_formats(self):
        builder = Gdk.ContentFormatsBuilder()
        builder.add_mime_type("text/uri-list")
        return builder.to_formats()

    def do_ref_storable_formats(self):
        return self.do_ref_formats()

    def do_write_mime_type_async(self, mime_type, stream, io_priority,
                                 cancellable, callback, user_data):
        task = Gio.Task.new(self, cancellable, callback, user_data)

        if mime_type != "text/uri-list":
            task.return_error(
                GLib.Error.new_literal(
                    Gio.io_error_quark(),
                    f"Unsupported format: {mime_type}",
                    Gio.IOErrorEnum.INVALID_ARGUMENT,
                )
            )
            return

        # Receivers ask for the payload more than once — XDND retries, and a
        # union provider gets queried per format.  Every one of those must be
        # served from a *single* download: parallel batches would fetch each
        # file several times over, open a channel per copy (which is what
        # kills the SSH connection on a large drag), and then race to write
        # and close the same drop stream, leaving the receiver with a
        # truncated URI list and nothing to copy.
        with self._lock:
            if self._uris is not None:
                uris = self._uris
            else:
                uris = None
                self._waiters.append((task, stream, io_priority, cancellable))
                start = not self._started
                self._started = True

        if uris is not None:
            self._write_uris(stream, uris, task, io_priority, cancellable)
            return

        if not start:
            return          # the in-flight download will serve this waiter

        def worker():
            try:
                downloaded = self._download_all(cancellable)
            except Exception as exc:                      # noqa: BLE001
                GLib.idle_add(self._fail, str(exc))
                return
            GLib.idle_add(self._succeed, downloaded)

        self._notify(_("Preparing {what} for drop…").format(what=self._describe()), "info")
        threading.Thread(target=worker, daemon=True).start()

    def do_write_mime_type_finish(self, result):
        return result.propagate_boolean()

    # ── Internals ─────────────────────────────────────────────────────── #

    def _describe(self):
        if len(self._file_infos) == 1:
            return self._file_infos[0].name
        return ngettext("{n} item", "{n} items", len(self._file_infos)).format(n=len(self._file_infos))

    def _notify(self, message, kind):
        if self._on_status:
            GLib.idle_add(self._on_status, message, kind)

    def _download_all(self, cancellable):
        from edith.services.temp_manager import TempManager
        from edith.services.transfer_queue import TransferAborted

        items = []
        uris = []
        for info in self._file_infos:
            local_path = str(TempManager.get_temp_path(info.path))
            items.append((info.path, local_path))
            uris.append(GLib.filename_to_uri(os.path.abspath(local_path), None))

        # One channel for the whole batch, not one per file.
        try:
            self._client.download_many(items, cancel_event=_Cancel(cancellable))
        except TransferAborted:
            raise GLib.Error.new_literal(
                Gio.io_error_quark(), "Cancelled", Gio.IOErrorEnum.CANCELLED
            ) from None
        return uris

    def _succeed(self, uris):
        with self._lock:
            self._uris = uris
            waiters = self._waiters
            self._waiters = []
        self._notify(_("Dropped {what}").format(what=self._describe()), "success")
        for task, stream, io_priority, cancellable in waiters:
            self._write_uris(stream, uris, task, io_priority, cancellable)
        return GLib.SOURCE_REMOVE

    def _fail(self, message):
        with self._lock:
            waiters = self._waiters
            self._waiters = []
            # Let a later request retry: a drag can be dropped again.
            self._started = False
        self._notify(_("Drag failed: {message}").format(message=message), "error")
        for task, _stream, _io_priority, _cancellable in waiters:
            task.return_error(
                GLib.Error.new_literal(
                    Gio.io_error_quark(), message, Gio.IOErrorEnum.FAILED
                )
            )
        return GLib.SOURCE_REMOVE

    def _write_uris(self, stream, uris, task, io_priority, cancellable):
        """Write the payload from a worker thread.

        The write must not happen on the main thread. A drag between two
        Edith windows is served *and* consumed by the same main loop, so a
        write that blocks — which it does as soon as the payload outgrows the
        pipe buffer — parks the main thread on a pipe only the main thread
        could drain. That deadlocks every window at once, permanently.

        write_all_async() is not a fix: on a blocking fd it blocks just the
        same. A thread is the only version that holds regardless of how the
        stream was set up.
        """
        # text/uri-list is CRLF separated per RFC 2483.
        payload = "".join(f"{uri}\r\n" for uri in uris).encode()

        def worker():
            try:
                stream.write_all(payload, cancellable)
                stream.close(cancellable)
            except GLib.Error as exc:
                GLib.idle_add(self._return_error, task, exc)
                return
            GLib.idle_add(self._return_ok, task)

        threading.Thread(target=worker, daemon=True,
                         name="edith-drag-write").start()

    @staticmethod
    def _return_ok(task):
        task.return_boolean(True)
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _return_error(task, exc):
        task.return_error(exc)
        return GLib.SOURCE_REMOVE
