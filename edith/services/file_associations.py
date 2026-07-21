"""Map file extensions to the local application that should open them.

`Open Locally` hands a downloaded file to an application. By default that's
whatever the desktop environment considers the handler for the file's content
type; users can override this per extension, which is what the
`file_associations` preference stores (extension without dot -> desktop file
id, e.g. "png" -> "org.gimp.GIMP.desktop").
"""

from gi.repository import Gio

from edith.services.config import ConfigService


def normalize_ext(name: str) -> str:
    """Extension of `name`, lower-cased and without the leading dot."""
    name = name.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].strip().lstrip(".").lower()


def content_type_for(name: str) -> str | None:
    content_type, _uncertain = Gio.content_type_guess(name, None)
    return content_type


def get_associations() -> dict:
    """All user-defined extension -> desktop id mappings."""
    return dict(ConfigService.get_preference("file_associations", {}))


def parse_extensions(text: str) -> list[str]:
    """Split user input like "txt, md; .html js" into ["txt", "md", "html", "js"].

    Order is preserved and duplicates are dropped.
    """
    raw = text.replace(",", " ").replace(";", " ").split()
    exts = []
    for part in raw:
        ext = part.strip().lstrip(".").lower()
        if ext and ext not in exts:
            exts.append(ext)
    return exts


def set_association(ext: str, desktop_id: str):
    """Associate one or more extensions (accepts a comma-separated string)."""
    if not desktop_id:
        return
    exts = parse_extensions(ext)
    if not exts:
        return
    assoc = get_associations()
    for e in exts:
        assoc[e] = desktop_id
    ConfigService.set_preference("file_associations", assoc)


def remove_association(ext):
    """Remove one extension, or several when given a list."""
    exts = [ext] if isinstance(ext, str) else list(ext)
    assoc = get_associations()
    changed = False
    for e in exts:
        if assoc.pop(e, None) is not None:
            changed = True
    if changed:
        ConfigService.set_preference("file_associations", assoc)


def get_associations_by_app() -> dict:
    """Group associations as desktop id -> sorted list of extensions."""
    grouped = {}
    for ext, desktop_id in get_associations().items():
        grouped.setdefault(desktop_id, []).append(ext)
    for exts in grouped.values():
        exts.sort()
    return grouped


def app_for_desktop_id(desktop_id: str):
    """Return the Gio.AppInfo for a desktop id, or None if it's gone."""
    if not desktop_id:
        return None
    try:
        return Gio.DesktopAppInfo.new(desktop_id)
    except TypeError:
        return None


def resolve(name: str):
    """Return `(app_info, is_custom)` for a file name.

    `is_custom` is True when the app came from a user association rather than
    the desktop default. Both may be None/False when nothing can open the file.
    """
    ext = normalize_ext(name)
    if ext:
        desktop_id = get_associations().get(ext)
        app = app_for_desktop_id(desktop_id)
        if app is not None:
            return app, True

    content_type = content_type_for(name)
    if content_type:
        return Gio.AppInfo.get_default_for_type(content_type, False), False
    return None, False


def candidates_for(name: str) -> list:
    """Applications registered for this file's content type, default first."""
    content_type = content_type_for(name)
    if not content_type:
        return []
    return list(Gio.AppInfo.get_all_for_type(content_type))


def all_installed_apps() -> list:
    """Every application that should be shown to the user, sorted by name."""
    apps = [a for a in Gio.AppInfo.get_all() if a.should_show()]
    apps.sort(key=lambda a: (a.get_display_name() or "").lower())
    return apps


def launch(app_info, local_path: str) -> bool:
    """Open `local_path` with `app_info`. Returns False if the launch failed."""
    from gi.repository import GLib

    file = Gio.File.new_for_path(local_path)
    try:
        return app_info.launch([file], None)
    except GLib.Error:
        return False
