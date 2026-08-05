import faulthandler
import locale
import logging
import os
import signal
import sys

from edith import LOCALEDIR
from edith.i18n import GETTEXT_DOMAIN
from edith.application import EdithApplication


def main():
    # Warnings and above always go to stderr; EDITH_DEBUG=1 turns on the
    # diagnostic chatter (undo-history resets, renderer crashes, …).
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("EDITH_DEBUG") else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # `kill -USR1 <pid>` dumps a Python stack trace for every thread to
    # stderr.  The only way to see what a frozen UI is actually waiting on,
    # since a hung main loop can't report anything itself.
    try:
        faulthandler.enable()
        # chain=False: the default SIGUSR1 action terminates the process, and
        # dumping a hung UI must not kill it.
        faulthandler.register(signal.SIGUSR1, all_threads=True, chain=False)
    except (AttributeError, ValueError, RuntimeError):
        # No usable stderr (e.g. launched detached) — not worth failing over.
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
