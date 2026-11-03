# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tell the user when a server-list change was refused.

ConfigService raises ConfigDamagedError rather than write over a servers.json
it can't parse. Unhandled, that exception only reaches the log, and the user's
"Add Server" appears to do nothing at all.
"""

import functools

from edith.services.config import ConfigDamagedError
from edith.i18n import _


def refuses_damaged_config(refresh: str):
    """Decorate a widget method that changes the server list.

    On ConfigDamagedError, re-read the config through the widget's `refresh`
    method (so the UI doesn't show a change that wasn't saved), show an error
    toast, and return False.
    """
    def decorate(handler):
        @functools.wraps(handler)
        def wrapper(self, *args, **kwargs):
            try:
                return handler(self, *args, **kwargs)
            except ConfigDamagedError:
                getattr(self, refresh)()
                root = self.get_root()
                if root is not None and hasattr(root, "show_toast"):
                    root.show_toast(
                        _("Not saved: the server list file is damaged and "
                          "can’t be changed"),
                        "error", 8,
                    )
                return False
        return wrapper
    return decorate
