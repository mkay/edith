# Edith

A GTK4/libadwaita SFTP client for live remote file editing.

Connect to remote servers over SSH, browse files, and edit them in a tabbed editor with syntax highlighting — changes are uploaded back on save.

## Features

- **Server management** — saved connections with password/key auth, organized into collapsible folder groups
- **File browser** — navigate remote directories with drag-and-drop move, upload, copy, rename, delete
- **Tabbed editor** — GtkSourceView 5 with syntax highlighting, customizable themes and fonts
- **Secure credentials** — passwords stored via GNOME Keyring / libsecret
- **Live editing** — files downloaded to temp, edited locally, uploaded on save

## Dependencies

- Python 3
- GTK 4
- libadwaita
- GtkSourceView 5
- python-paramiko
- python-gobject
- python-keyring

## Building (Arch Linux)

```sh
makepkg -si
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
