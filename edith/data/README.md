# Edith — bundled data files

## Custom icons

**Location:** `icons/hicolor/scalable/actions/`

Drop an SVG file here with the same name as any icon the app uses and it will
override the system theme version. No build-system changes needed — `install_subdir`
picks up everything in the directory automatically.

### SVG spec

**Required:**

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
```

- `viewBox` is required. GTK scales the icon to whatever size the widget requests
  using the viewBox as the coordinate space.
- `width` / `height` are ignored by GTK but keep them matching the viewBox so
  other tools (Inkscape, browsers) render at a sensible default size.
- Standard grids: `16 16` or `24 24`. Pick one and use it consistently.

**Recoloring — the `-symbolic` suffix:**

Any filename ending in `-symbolic.svg` is recolored automatically by GTK to match
the current theme foreground, accent, and state colors (light mode, dark mode, hover,
disabled, etc.).

Use `currentColor` for all strokes and fills so GTK can apply those colors via CSS:

```xml
<!-- filled shape -->
<path fill="currentColor" d="M2 2h12v12H2z"/>

<!-- outlined shape -->
<path fill="none" stroke="currentColor" stroke-width="1.5" d="M2 2h12v12H2z"/>

<!-- both -->
<path fill="currentColor" stroke="currentColor" stroke-width="1" d="..."/>
```

**Stroke width guidelines:**

| Grid   | Typical stroke-width |
|--------|----------------------|
| 16×16  | 1.5 – 2 px           |
| 24×24  | 1.5 px               |

If you design at a larger artboard and scale down, add
`vector-effect="non-scaling-stroke"` to keep stroke width in screen pixels.

**Things to avoid:**

- `<style>` blocks or class-based colors — can interfere with GTK's recoloring
- External references (`xlink:href` to other files)
- Filters, blur, drop-shadow (librsvg support is limited)
- Gradients (won't recolor correctly)
- Embedded fonts
- Animations

**Minimal working example:**

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
  <rect x="2" y="2" width="12" height="12" rx="2"
        fill="none" stroke="currentColor" stroke-width="1.5"/>
  <line x1="8" y1="5" x2="8" y2="11" stroke="currentColor" stroke-width="1.5"/>
  <line x1="5" y1="8" x2="11" y2="8" stroke="currentColor" stroke-width="1.5"/>
</svg>
```

---

### All icon names currently used by the app

Place the corresponding `.svg` file in `icons/hicolor/scalable/actions/` to override.

#### UI chrome

| Filename | Used for |
|----------|----------|
| `open-menu-symbolic.svg` | Header bar — hamburger menu |
| `sidebar-show-symbolic.svg` | Header bar — toggle sidebar |
| `list-add-symbolic.svg` | Header bar — add server |
| `folder-new-symbolic.svg` | Header bar — new server group |
| `window-close-symbolic.svg` | Transfer panel — cancel button |
| `pan-down-symbolic.svg` | Dropdowns / expanders |
| `pan-end-symbolic.svg` | Dropdowns / expanders |

#### Navigation

| Filename | Used for |
|----------|----------|
| `go-previous-symbolic.svg` | Header bar — back |
| `go-next-symbolic.svg` | Header bar — forward |
| `go-up-symbolic.svg` | File browser — parent directory |
| `go-down-symbolic.svg` | File browser toolbar |

#### Connection / network status

| Filename | Used for |
|----------|----------|
| `network-wired-symbolic.svg` | Header bar — connect button (idle) |
| `network-wired-disconnected-symbolic.svg` | Header bar — connect button (connected) |
| `network-offline-symbolic.svg` | Status bar — disconnected |
| `network-transmit-symbolic.svg` | Status bar — connecting / uploading |
| `network-idle-symbolic.svg` | Status bar — connected |
| `network-receive-symbolic.svg` | Status bar — downloading |
| `network-server-symbolic.svg` | Server list rows |
| `drive-harddisk-symbolic.svg` | Server list rows (alternative) |

#### File browser

| Filename | Used for |
|----------|----------|
| `folder-symbolic.svg` | Directory rows |
| `folder-new-symbolic.svg` | New folder action |
| `document-open-symbolic.svg` | File dialogs |
| `edit-delete-symbolic.svg` | Delete action |
| `view-refresh-symbolic.svg` | Refresh action |

#### File-type icons (file rows)

| Filename | Used for |
|----------|----------|
| `text-x-generic-symbolic.svg` | Generic / unknown file type |
| `text-x-python-symbolic.svg` | `.py` files |
| `text-x-csrc-symbolic.svg` | `.c` files |
| `text-x-chdr-symbolic.svg` | `.h` files |
| `text-x-javascript-symbolic.svg` | `.js` / `.ts` files |
| `text-x-script-symbolic.svg` | Shell scripts |
| `text-css-symbolic.svg` | `.css` files |
| `text-html-symbolic.svg` | `.html` files |
| `text-xml-symbolic.svg` | `.xml` files |

#### Transfer queue / progress

| Filename | Used for |
|----------|----------|
| `emblem-synchronizing-symbolic.svg` | Header transfer button, active transfer row |
| `content-loading-symbolic.svg` | Transfer panel — queued state |
| `object-select-symbolic.svg` | Transfer panel — done state |
| `process-stop-symbolic.svg` | Transfer panel — cancelled state |
| `dialog-error-symbolic.svg` | Transfer panel — failed state, status bar error |

#### App icon

| Filename | Used for |
|----------|----------|
| `edith-symbolic.svg` | About dialog, window icon |

---

## Syntax highlighting themes

**Location:** `styles/`

GtkSourceView style schemes (`.xml` files). Registered at startup via
`GtkSource.StyleSchemeManager`. See the
[GtkSourceView style scheme reference](https://gnome.pages.gitlab.gnome.org/gtksourceview/gtksourceview5/style-reference.html)
for the file format.
