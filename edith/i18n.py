"""Translation helpers.

Every user-facing string goes through `_()` (or `ngettext()` for anything
counted, `C_()` where the same English word needs different translations in
different places). Import them from here rather than installing `_` into
builtins, so the dependency is explicit and xgettext can find the call sites.
"""

import gettext

from edith import LOCALEDIR

GETTEXT_DOMAIN = "edith"

_translation = gettext.translation(GETTEXT_DOMAIN, LOCALEDIR, fallback=True)

#: Translate a string. Falls back to the English source when no catalogue
#: exists for the current locale.
_ = _translation.gettext

#: Translate a counted string, picking the plural form for `n`.
ngettext = _translation.ngettext


def C_(context, message):
    """Translate `message` disambiguated by `context`.

    Use when the same English word needs different translations depending on
    where it appears — e.g. "Open" as a verb on a button versus a noun in a
    menu heading.
    """
    return _translation.pgettext(context, message)
