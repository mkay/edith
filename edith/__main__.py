# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

import faulthandler
import locale
import logging
import os
import signal
import sys

from edith import LOCALEDIR
from edith.i18n import GETTEXT_DOMAIN
from edith.application import EdithApplication
from edith.services import freeze_watchdog

# Held open for the process lifetime; see the faulthandler setup in main().
_fault_log = None


def main():
    # Warnings and above always go to stderr; EDITH_DEBUG=1 turns on the
    # diagnostic chatter (undo-history resets, renderer crashes, …).
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("EDITH_DEBUG") else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # `kill -USR1 <pid>` dumps a Python stack trace for every thread, and a
    # native crash (segfault, abort in GTK/WebKit) dumps one too.  The only
    # way to see what a frozen UI is waiting on, since a hung main loop can't
    # report anything itself.
    #
    # Both go to the diagnostic log, not to stderr: started from a launcher
    # there is no stderr to read, so a hard crash would otherwise leave
    # nothing behind — unlike a Python exception, which the excepthook
    # records.  The file is deliberately kept open for the process lifetime;
    # faulthandler writes to the descriptor, so closing it would disarm both
    # handlers.
    global _fault_log
    try:
        _fault_log = open(freeze_watchdog.fault_log_path(), "a", buffering=1)
    except OSError:
        _fault_log = None
    try:
        faulthandler.enable(file=_fault_log or sys.stderr)
        # chain=False: the default SIGUSR1 action terminates the process, and
        # dumping a hung UI must not kill it.
        faulthandler.register(signal.SIGUSR1, all_threads=True, chain=False,
                              file=_fault_log or sys.stderr)
    except (AttributeError, ValueError, RuntimeError):
        # No usable output (e.g. launched detached) — not worth failing over.
        pass

    # Honour the user's locale, and point the C library at our catalogues so
    # anything translated below the Python layer resolves too.
    try:
        locale.setlocale(locale.LC_ALL, "")
        locale.bindtextdomain(GETTEXT_DOMAIN, LOCALEDIR)
        locale.textdomain(GETTEXT_DOMAIN)
    except (locale.Error, AttributeError):
        # An unsupported LANG shouldn't stop the app from starting.
        pass

    app = EdithApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
