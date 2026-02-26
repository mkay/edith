# Edith

A GTK4/libadwaita SFTP client for live remote file editing.
(a poor man's Nova ©)

Connect to remote servers over SSH, browse files, and edit them in a tabbed editor with syntax highlighting — changes are uploaded back on save.

> **Alpha software.** Early access for the brave and the bug‑tolerant.

![Edith Icon](data/de.singular.edith.svg)

## Features

- **Server management** — saved connections with password/key auth, organized into groups; two-level navigation with groups in the sidebar and servers in the main pane; double-click to connect
- **Server search** — Ctrl+F to filter servers by name
- **File browser** — navigate remote directories with drag-and-drop move, upload, copy, rename, delete; keyboard shortcuts for common actions
- **Path bar** — clickable breadcrumb navigation in the title bar with back/forward history
- **Monaco editor** — VS Code's editor engine (WebKitGTK), with syntax highlighting for 80+ languages, Emmet support, find/replace, go to line, word wrap, and customizable themes and fonts
- **Editor settings** — minimap, render whitespace, sticky scroll, font ligatures, format on save, line numbers (on/off/relative)
- **SVG preview** — toggle a live side-by-side preview panel for SVG files; refreshes after each save
- **Image viewer** — raster images (PNG, JPEG, GIF, WebP, BMP, ICO, TIFF, AVIF) open in a dedicated read-only tab showing dimensions, DPI, and file size
- **Syntax selector** — per-file language override in the status bar
- **Custom syntax associations** — map file extensions to languages (e.g. `.tpl` → PHP)
- **Secure credentials** — stored in the system keychain (GNOME Keyring or compatible); never written to disk in plain text
- **Live editing** — files downloaded to temp, edited locally, uploaded on save
- **Home directory support** — use `~` as initial directory to resolve the server's home path
- **Resizable sidebar** — drag to adjust; toggle with the button in the headerbar or F9
- **Safe quit** — warns before closing if there are unsaved edits or active file transfers

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| Ctrl+N | Add server |
| Ctrl+Shift+N | New window |
| Ctrl+F | Search servers |
| Ctrl+D | Disconnect |
| Ctrl+S | Save file |
| Ctrl+Z / Ctrl+Shift+Z | Undo / Redo |
| Ctrl+Shift+F | Find and replace |
| Ctrl+G | Go to line |
| Ctrl+W | Close tab |
| Ctrl+Q | Quit |
| F9 | Toggle sidebar |
| F2 | Rename selected file |
| Delete | Delete selected file |
| F5 | Refresh directory |
| Backspace | Go up one directory |

## Dependencies

- Python 3
- GTK 4
- libadwaita
- WebKitGTK 6.0
- python-paramiko
- python-gobject
- python-keyring

## Building (Arch Linux)

```sh
makepkg -sc
sudo pacman -U edith-*.pkg.tar.zst
```

If pacman reports conflicting files (e.g. after a manual install), use:

```sh
sudo pacman -U --overwrite '/usr/share/edith/*' edith-*.pkg.tar.zst
```

## Building (manual)

```sh
meson setup builddir --prefix=/usr
ninja -C builddir
sudo meson install -C builddir
```

## Usage

```sh
edith
```

Or launch from your application menu.

## License

GPL-3.0-or-later

## Screenshots

![Welcome screen](assets/edith_welcome.png)

![Connected with file open](assets/edith_connected.png)

## Disclaimer

This project was created with AI assistance. The code has not been thoroughly reviewed. Verify its correctness and suitability before use. 
