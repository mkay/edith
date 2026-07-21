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


class RemoteFilesProvider(Gdk.ContentProvider):
    """A `text/uri-list` provider that downloads on demand."""

    __gtype_name__ = "EdithRemoteFilesProvider"

    def __init__(self, client, file_infos, on_status=None):
        super().__init__()
        self._client = client
        self._file_infos = list(file_infos)
        self._on_status = on_status
        self._uris = None          # cached result of a completed download
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

        # A second request for an already-materialised drag costs nothing.
        if self._uris is not None:
            self._write_uris(stream, self._uris, task)
            return

        def worker():
            try:
                uris = self._download_all(cancellable)
            except Exception as exc:                      # noqa: BLE001
                GLib.idle_add(self._fail, task, str(exc))
                return
            GLib.idle_add(self._succeed, task, stream, uris)

        self._notify(f"Preparing {self._describe()} for drop…", "info")
        threading.Thread(target=worker, daemon=True).start()

    def do_write_mime_type_finish(self, result):
        return result.propagate_boolean()

    # ── Internals ─────────────────────────────────────────────────────── #

    def _describe(self):
        if len(self._file_infos) == 1:
            return self._file_infos[0].name
        return f"{len(self._file_infos)} items"

    def _notify(self, message, kind):
        if self._on_status:
            GLib.idle_add(self._on_status, message, kind)

    def _download_all(self, cancellable):
        from edith.services.temp_manager import TempManager

        uris = []
        for info in self._file_infos:
            if cancellable is not None and cancellable.is_cancelled():
                raise GLib.Error.new_literal(
                    Gio.io_error_quark(), "Cancelled", Gio.IOErrorEnum.CANCELLED
                )
            local_path = str(TempManager.get_temp_path(info.path))
            if info.is_dir:
                self._client.download_recursive(info.path, local_path)
            else:
                self._client.download(info.path, local_path)
            uris.append(GLib.filename_to_uri(os.path.abspath(local_path), None))
        return uris

    def _succeed(self, task, stream, uris):
        with self._lock:
            self._uris = uris
        self._notify(f"Dropped {self._describe()}", "success")
        self._write_uris(stream, uris, task)
        return GLib.SOURCE_REMOVE

    def _fail(self, task, message):
        self._notify(f"Drag failed: {message}", "error")
        task.return_error(
            GLib.Error.new_literal(
                Gio.io_error_quark(), message, Gio.IOErrorEnum.FAILED
            )
        )
        return GLib.SOURCE_REMOVE

    def _write_uris(self, stream, uris, task):
        # text/uri-list is CRLF separated per RFC 2483.
        payload = "".join(f"{uri}\r\n" for uri in uris).encode()
        try:
            stream.write_all(payload, None)
            stream.close(None)
        except GLib.Error as exc:
            task.return_error(exc)
            return
        task.return_boolean(True)
