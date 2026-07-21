# Translating Edith

Translations live in `po/`. Each language is one `.po` file named after its
locale code (`de.po`, `tr.po`, `fr.po` …).

## Adding a new language

```sh
meson setup build
ninja -C build edith-pot                 # refresh po/edith.pot from the source
msginit --locale=tr --input=po/edith.pot --output=po/tr.po
```

Then add the code to `po/LINGUAS` (one per line, alphabetical). A language is
only built and installed once it appears there.

## Updating an existing language

```sh
ninja -C build edith-pot
ninja -C build edith-update-po           # merge new strings into every .po
```

Strings whose English source changed are marked `#, fuzzy`. Fuzzy entries are
**not** shown to users — the English original is displayed instead — so an
unreviewed string can never reach the interface. Clear the flag once you have
checked the translation.

## Editing

Use [Poedit](https://poedit.net/) or GNOME's Gtranslator, or any text editor.

Placeholders like `{name}` and `{app}` may be moved anywhere in the sentence —
they are substituted by name, not position:

```po
msgid "Open with {app}"
msgstr "Mit {app} öffnen"
```

Do not rename or drop a placeholder; the application will fail to start that
string. Run `msgfmt --check po/xx.po -o /dev/null` to verify before submitting.

Counted strings have two entries; supply as many forms as your language needs:

```po
msgid "{n} item"
msgid_plural "{n} items"
msgstr[0] "{n} Objekt"
msgstr[1] "{n} Objekte"
```

## Testing

```sh
LANGUAGE=de edith
```

## Credit

Translate the string `translator-credits` with your name and it will appear in
the About dialog. Untranslated, it stays hidden.

## Submitting

Open a pull request with your `.po` file (and the `po/LINGUAS` line if the
language is new). Machine-translated entries are welcome as a starting point,
but please leave them marked fuzzy until a speaker has reviewed them.
