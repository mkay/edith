# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import dataclass


@dataclass
class OpenFile:
    """Tracks an open file being edited."""

    remote_path: str
    local_path: str
    is_modified: bool = False

    @property
    def filename(self) -> str:
        return self.remote_path.rsplit("/", 1)[-1]
