# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Questions about SSH host keys and TLS certificates the connect flow
can't decide alone."""

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw, GLib

from edith.i18n import _
from edith.services import trust_store


def _esc(text) -> str:
    return GLib.markup_escape_text(str(text))


def _tt(text) -> str:
    return f"<tt>{_esc(text)}</tt>"


def present_trust_dialog(parent, error, on_trusted, on_declined=None) -> bool:
    """Ask about `error` if it is a host key or certificate problem.

    on_trusted() runs after the user trusted the key or certificate (it is
    already recorded, so connecting again will pass); on_declined() when
    they didn't, or when there was nothing to decide (a changed key).
    Returns False, showing nothing, for any other error.
    """
    if isinstance(error, trust_store.HostKeyUnknown):
        dialog = _unknown_host_key(error)
        trust = lambda: trust_store.trust_host_key(error.host, error.port, error.key)
    elif isinstance(error, trust_store.UntrustedCertificate):
        dialog = _untrusted_certificate(error)
        trust = lambda: trust_store.trust_certificate(error.host, error.port, error.der)
    elif isinstance(error, (trust_store.HostKeyChanged, trust_store.HostKeyRevoked)):
        dialog = _refused_host_key(error)
        trust = None
    else:
        return False

    def on_response(d, response):
        if response == "trust" and trust is not None:
            try:
                trust()
            except OSError as e:
                failed = Adw.AlertDialog(
                    heading=_("Couldn't Save Trust"), body=str(e)
                )
                failed.add_response("ok", _("OK"))
                failed.present(parent)
                if on_declined:
                    on_declined()
                return
            on_trusted()
        elif on_declined:
            on_declined()

    dialog.connect("response", on_response)
    dialog.present(parent)
    return True


def _unknown_host_key(error):
    body = _(
        "Edith hasn't connected to {host} before, so it can't confirm this "
        "is the server you mean and not someone in between. Compare the "
        "fingerprint with the one your hosting provider or administrator "
        "gives you."
    ).format(host=_esc(error.host))
    body += "\n\n" + _("{type} key fingerprint:").format(type=_esc(error.key_type))
    body += "\n" + _tt(error.fingerprint)
    if error.other_types:
        body += "\n\n" + _(
            "Keys of another type are already known for this host; the "
            "server offered one Edith hasn't seen."
        )

    dialog = Adw.AlertDialog(heading=_("Unknown Server"), body=body)
    dialog.set_body_use_markup(True)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("trust", _("Trust and Connect"))
    dialog.set_response_appearance("trust", Adw.ResponseAppearance.SUGGESTED)
    # Enter must not wave a key through.
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    return dialog


def _refused_host_key(error):
    name = trust_store.host_id(error.host, error.port)
    if isinstance(error, trust_store.HostKeyRevoked):
        heading = _("Server Key Revoked")
        body = _(
            "{host} presented a key that is marked as revoked in {file}. "
            "Edith won't connect."
        ).format(host=_esc(error.host), file=_tt(error.source))
    else:
        heading = _("Server Key Changed")
        body = _(
            "{host} presented a different {type} key from the one Edith "
            "knows. That happens when a server is reinstalled or its keys "
            "are replaced, but it is also what someone intercepting the "
            "connection would look like, so Edith won't connect."
        ).format(host=_esc(error.host), type=_esc(error.key_type))
        body += "\n\n" + _(
            "If you know the key changed, remove the old one, then connect "
            "again:"
        )
        body += "\n" + _tt(f"ssh-keygen -R '{name}' -f '{error.source}'")
    body += "\n\n" + _("Fingerprint of the key presented:")
    body += "\n" + _tt(error.fingerprint)

    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.set_body_use_markup(True)
    dialog.add_response("ok", _("OK"))
    return dialog


def _untrusted_certificate(error):
    info = trust_store.describe_certificate(error.der)
    body = _(
        "The certificate of {host} couldn't be verified: {reason}."
    ).format(host=_esc(error.host), reason=_esc(error.reason.rstrip(".")))
    body += "\n\n" + _(
        "Shared hosting servers often present the provider's certificate "
        "rather than one for your domain. Trust it only if it is the "
        "certificate you expect."
    )
    if info:
        issued_to = info["subject"]
        if info["names"] and info["names"] != [info["subject"]]:
            issued_to += " (" + ", ".join(info["names"][:5]) + ")"
        body += "\n\n" + _("Issued to: {name}").format(name=_esc(issued_to))
        body += "\n" + _("Issued by: {name}").format(name=_esc(info["issuer"]))
        body += "\n" + _("Valid: {start} to {end}").format(
            start=info["not_before"].strftime("%Y-%m-%d"),
            end=info["not_after"].strftime("%Y-%m-%d"),
        )
    body += "\n\n" + _("SHA-256 fingerprint:")
    body += "\n" + _tt(error.fingerprint)

    dialog = Adw.AlertDialog(heading=_("Untrusted Certificate"), body=body)
    dialog.set_body_use_markup(True)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("trust", _("Trust and Connect"))
    dialog.set_response_appearance("trust", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    return dialog
