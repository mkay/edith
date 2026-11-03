<!--
Release notes for the CURRENT version only. Shown once on the first launch after an
update, and reachable again from the About dialog. Curated by hand — nothing here is
generated from git history, so rewrite it whenever the version in meson.build is
bumped.

English only, deliberately: these notes do not go through gettext, so they stay in
English regardless of the language setting. Keep them to short bullets — five one- or
two-line bullets is what the dialog holds without scrolling. Don't write the version
number here; the dialog takes it from the build.

Cover everything since the last *released* version, not just the last session's work.
The released version is what AUR shows, which can be several sessions behind main:
  git log $(git tag --sort=-version:refname | head -1)..HEAD --oneline

Blank lines and this comment are ignored. Every other line is one bullet; a leading
"-", "*" or "•" is optional and stripped.

Links may be written as [label](https://…) or as a bare URL. Prefer the labelled
form: a full URL wraps mid-scheme in a dialog this narrow.
-->

- Servers are checked before logging in: a new SFTP server asks once to confirm its key, a changed key is refused, and FTPS certificates are verified.
- A dropped connection is noticed and offers to reconnect, keeping open tabs and unsaved edits.
- Saving keeps a file's encoding and line endings, a failed upload no longer looks saved, and failed downloads leave no partial files.
- PuTTY .ppk keys work, and FileZilla import keeps start folders, FTP encryption, key files and saved passwords.
- Links to folders open as folders, and cancelling an FTP transfer no longer drops the connection.
