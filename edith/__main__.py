import locale
import sys

from edith import LOCALEDIR
from edith.i18n import GETTEXT_DOMAIN
from edith.application import EdithApplication


def main():
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
