# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import dataclass


@dataclass
class OpenFile:
    """Tracks an open file being edited."""

    remote_path: str
    local_path: str
    is_modified: bool = False
    # Set when polling finds the file gone from the server; the tab still
    # holds the content, so closing it needs a warning like unsaved changes.
    is_deleted: bool = False

    @property
    def filename(self) -> str:
        return self.remote_path.rsplit("/", 1)[-1]
