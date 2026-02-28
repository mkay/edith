import gi
import json

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")

from pathlib import Path

from gi.repository import Adw, GLib, Gtk, GObject, WebKit

from edith.models.open_file import OpenFile
from edith.monaco_languages import EXT_TO_MONACO, get_language_name
from edith.services.config import ConfigService


class MonacoEditor(Gtk.Box):
    """Single editor tab backed by Monaco running in a WebKitGTK WebView."""

    __gsignals__ = {
        "modified-changed":    (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "save-requested":      (GObject.SignalFlags.RUN_FIRST, None, ()),
        "line-ending-detected":(GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "cursor-changed":      (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
        "wrap-changed":        (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self, open_file: OpenFile):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.open_file = open_file
        self._ready = False
        self._pending_js = []
        self._language_id = None
        self._line_ending = "lf"
        self._word_wrap = True
        self._cursor_line = 1
        self._cursor_col = 1
        self._pending_save_callback = None
        self._is_svg = open_file.filename.lower().endswith(".svg")

        self._build_ui()
        self._load_file_and_init()

    # ------------------------------------------------------------------ #
    #  UI                                                                  #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        self._webview = WebKit.WebView()
        self._webview.set_vexpand(True)
        self._webview.set_hexpand(True)

        settings = self._webview.get_settings()
        settings.set_allow_file_access_from_file_urls(True)
        settings.set_allow_universal_access_from_file_urls(True)
        settings.set_javascript_can_access_clipboard(True)
        # Disable GPU compositing to avoid a WebKit/Skia bug where GrResourceCache
        # corrupts GPU texture state, causing SIGILL crashes via ud2 traps.
        # (GrResourceCache::notifyARefCntReachedZero / refAndMakeResourceMRU)
        settings.set_hardware_acceleration_policy(
            WebKit.HardwareAccelerationPolicy.NEVER
        )

        ucm = self._webview.get_user_content_manager()
        ucm.register_script_message_handler("edith")
        ucm.connect("script-message-received::edith", self._on_script_message)
        self._webview.connect("web-process-terminated", self._on_web_process_terminated)

        # Intercept dead-key events before GTK's IM can swallow them.
        # The grave/backtick key is mapped as dead_grave on many keyboard
        # layouts, so it never reaches the WebView normally.
        dead_key_ctrl = Gtk.EventControllerKey()
        dead_key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        dead_key_ctrl.connect("key-pressed", self._on_webview_key_capture)
        self._webview.add_controller(dead_key_ctrl)

        monaco_dir = Path(__file__).parent.parent / "data" / "monaco"
        self._webview.load_uri((monaco_dir / "editor.html").as_uri())

        if self._is_svg:
            self._build_svg_split_ui()
        else:
            self.append(self._webview)

    def _build_svg_split_ui(self):
        """For SVG files: editor + collapsible preview panel side by side."""
        # Floating preview toggle button overlaid on the editor
        self._preview_btn = Gtk.ToggleButton(
            label="Preview",
            css_classes=["flat", "osd"],
            halign=Gtk.Align.END,
            valign=Gtk.Align.START,
            margin_top=6,
            margin_end=6,
        )
        self._preview_btn.connect("toggled", self._on_preview_toggled)

        overlay = Gtk.Overlay()
        overlay.set_child(self._webview)
        overlay.add_overlay(self._preview_btn)
        overlay.set_vexpand(True)
        overlay.set_hexpand(True)

        # Preview panel (hidden by default, picture created lazily on first show)
        self._preview_sep = Gtk.Separator(
            orientation=Gtk.Orientation.VERTICAL,
            visible=False,
        )
        self._preview_picture = None  # created lazily in _refresh_svg_preview

        # ScrolledWindow with propagate_natural_width=False stops the SVG's
        # intrinsic width from bubbling up and blowing out the layout.
        self._preview_panel = Gtk.ScrolledWindow()
        self._preview_panel.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        self._preview_panel.set_propagate_natural_width(False)
        self._preview_panel.set_size_request(300, -1)
        self._preview_panel.set_hexpand(False)
        self._preview_panel.set_vexpand(True)
        self._preview_panel.set_visible(False)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        content.set_vexpand(True)
        content.append(overlay)
        content.append(self._preview_sep)
        content.append(self._preview_panel)
        self.append(content)

    # ------------------------------------------------------------------ #
    #  JS communication                                                    #
    # ------------------------------------------------------------------ #

    def _on_webview_key_capture(self, ctrl, keyval, keycode, state):
        from gi.repository import Gdk
        ctrl_held = bool(state & Gdk.ModifierType.CONTROL_MASK)
        no_alt_super = not (state & (Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK))

        # Grave/dead_grave: commonly treated as a dead key by GTK's IM.
        # Intercept here and inject directly into Monaco so it isn't swallowed.
        if keyval in (Gdk.KEY_grave, Gdk.KEY_dead_grave):
            if not ctrl_held and no_alt_super:
                self._eval_js('EdithBridge.typeText("`")')
                return True

        # Ctrl+/ or Ctrl+Shift+7 (QWERTZ: Shift+7 = /) → toggle line comment.
        # GTK swallows this before it reaches the WebView, so intercept here.
        if keyval == Gdk.KEY_slash and ctrl_held and no_alt_super:
            self._eval_js("EdithBridge.toggleLineComment()")
            return True

        return False

    def _eval_js(self, script):
        if not self._ready:
            self._pending_js.append(script)
            return
        self._webview.evaluate_javascript(script, -1, None, None, None, None, None)

    def _on_script_message(self, ucm, js_result):
        try:
            msg = json.loads(js_result.to_string())
        except Exception:
            return

        msg_type = msg.get("type")
        data = msg.get("data", {})

        if msg_type == "ready":
            self._ready = True
            for script in self._pending_js:
                self._webview.evaluate_javascript(
                    script, -1, None, None, None, None, None
                )
            self._pending_js = []

        elif msg_type == "init-complete":
            self._line_ending = data.get("lineEnding", "lf")
            self._word_wrap = data.get("wordWrap", True)
            self.emit("line-ending-detected", self._line_ending)
            self.emit("wrap-changed", self._word_wrap)

        elif msg_type == "cursor-changed":
            self._cursor_line = data.get("line", 1)
            self._cursor_col = data.get("column", 1)
            self.emit("cursor-changed", self._cursor_line, self._cursor_col)

        elif msg_type == "wrap-changed":
            self._word_wrap = data.get("wordWrap", True)
            self.emit("wrap-changed", self._word_wrap)

        elif msg_type == "save-content":
            content = data.get("content", "")
            try:
                with open(self.open_file.local_path, "w") as f:
                    f.write(content)
            except Exception:
                pass
            self._eval_js("EdithBridge.markClean()")
            self._refresh_svg_preview()
            cb = self._pending_save_callback
            self._pending_save_callback = None
            if cb:
                cb()

        elif msg_type == "modified-changed":
            modified = data.get("modified", False)
            self.open_file.is_modified = modified
            self.emit("modified-changed", modified)

        elif msg_type == "save-requested":
            self.emit("save-requested")

        elif msg_type == "close-requested":
            self.activate_action("win.close-tab", None)

    def _on_web_process_terminated(self, webview, reason):
        """WebKit renderer crashed — reset state and reload from disk."""
        self._ready = False
        self._pending_js = []
        self._pending_save_callback = None
        self._load_file_and_init()  # queues init JS into _pending_js
        monaco_dir = Path(__file__).parent.parent / "data" / "monaco"
        self._webview.load_uri((monaco_dir / "editor.html").as_uri())

    # ------------------------------------------------------------------ #
    #  File loading & init                                                 #
    # ------------------------------------------------------------------ #

    def _load_file_and_init(self):
        try:
            with open(self.open_file.local_path, "r", errors="replace") as f:
                content = f.read()
        except Exception as e:
            content = f"Error loading file: {e}"

        lang_id = self._detect_language()
        self._language_id = lang_id

        theme_id = self._resolve_theme()
        font_family = ConfigService.get_preference("editor_font", "")
        font_size = ConfigService.get_preference("editor_font_size", 0)

        settings = {
            "insertSpaces": ConfigService.get_preference("editor_insert_spaces", True),
            "tabSize":      ConfigService.get_preference("editor_tab_size", 4),
            "minimap":      ConfigService.get_preference("editor_minimap", False),
            "renderWhitespace": ConfigService.get_preference(
                "editor_render_whitespace", "selection"
            ),
            "stickyScroll": ConfigService.get_preference("editor_sticky_scroll", False),
            "fontLigatures": ConfigService.get_preference("editor_font_ligatures", False),
            "lineNumbers":  ConfigService.get_preference("editor_line_numbers", "on"),
        }

        custom_options = ConfigService.get_preference("editor_overrides", {})

        self._eval_js(
            "EdithBridge.init({}, {}, {}, {}, {}, true, {}, {})".format(
                json.dumps(content),
                json.dumps(lang_id or "plaintext"),
                json.dumps(theme_id),
                json.dumps(font_family or ""),
                json.dumps(font_size or 14),
                json.dumps(settings),
                json.dumps(custom_options),
            )
        )

    def _detect_language(self):
        filename = self.open_file.filename
        ext = filename.rsplit(".", 1)[-1] if "." in filename else ""

        if ext:
            assoc = ConfigService.get_preference("syntax_associations", {})
            if ext in assoc:
                return assoc[ext]
            if ext in EXT_TO_MONACO:
                return EXT_TO_MONACO[ext]

        basename = filename.lower()
        if basename == "dockerfile":
            return "dockerfile"
        if basename in ("makefile", "gnumakefile"):
            return "shell"
        if basename in (".bashrc", ".bash_profile", ".zshrc", ".profile"):
            return "shell"

        return "plaintext"

    def _resolve_theme(self):
        saved = ConfigService.get_preference("syntax_scheme", "")
        if saved:
            return saved
        style_manager = Adw.StyleManager.get_default()
        return "vs-dark" if style_manager.get_dark() else "vs"

    def _on_preview_toggled(self, btn):
        visible = btn.get_active()
        btn.set_label("Close Preview" if visible else "Preview")
        self._preview_sep.set_visible(visible)
        self._preview_panel.set_visible(visible)
        if visible:
            self._refresh_svg_preview()

    def _refresh_svg_preview(self):
        if not self._is_svg or not self._preview_panel.get_visible():
            return
        if self._preview_picture is None:
            self._preview_picture = Gtk.Picture()
            self._preview_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            self._preview_picture.set_can_shrink(True)
            self._preview_panel.set_child(self._preview_picture)
        self._preview_picture.set_filename(self.open_file.local_path)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def show_find(self):
        self._eval_js("EdithBridge.showFind()")

    def show_replace(self):
        self._eval_js("EdithBridge.showReplace()")

    def hide_search(self):
        self._eval_js("EdithBridge.hideFind()")

    def find_next(self):
        self._eval_js(
            "editor.getAction('editor.action.nextMatchFindAction').run()"
        )

    def find_prev(self):
        self._eval_js(
            "editor.getAction('editor.action.previousMatchFindAction').run()"
        )

    def goto_line(self, line: int):
        self._eval_js(f"EdithBridge.gotoLine({line})")

    def toggle_wrap(self):
        self._eval_js("EdithBridge.toggleWrap()")

    def save_to_disk(self, on_done=None):
        """Async save: ask Monaco for content (formatting first if configured),
        write to disk on callback, then call on_done."""
        self._pending_save_callback = on_done
        format_on_save = ConfigService.get_preference("editor_format_on_save", False)
        flag = "true" if format_on_save else "false"
        self._eval_js(f"EdithBridge.savePrepare({flag})")

    def apply_scheme(self, scheme_id: str):
        self._eval_js(f"EdithBridge.setTheme({json.dumps(scheme_id)})")

    def apply_font(self, font_family: str, font_size: int):
        self._eval_js(
            f"EdithBridge.setFont({json.dumps(font_family)}, {json.dumps(font_size)})"
        )

    def set_language(self, lang_id):
        self._language_id = lang_id
        self._eval_js(
            f"EdithBridge.setLanguage({json.dumps(lang_id or 'plaintext')})"
        )

    def set_indent(self, insert_spaces: bool, tab_size: int):
        self._eval_js(
            f"EdithBridge.setIndent({'true' if insert_spaces else 'false'}, {tab_size})"
        )

    def set_line_ending(self, eol: str):
        self._line_ending = eol
        self._eval_js(f"EdithBridge.setLineEnding({json.dumps(eol)})")

    def set_minimap(self, enabled: bool):
        self._eval_js(f"EdithBridge.setMinimap({'true' if enabled else 'false'})")

    def set_render_whitespace(self, mode: str):
        self._eval_js(f"EdithBridge.setRenderWhitespace({json.dumps(mode)})")

    def set_sticky_scroll(self, enabled: bool):
        self._eval_js(
            f"EdithBridge.setStickyScroll({'true' if enabled else 'false'})"
        )

    def set_font_ligatures(self, enabled: bool):
        self._eval_js(
            f"EdithBridge.setFontLigatures({'true' if enabled else 'false'})"
        )

    def set_line_numbers(self, mode: str):
        self._eval_js(f"EdithBridge.setLineNumbers({json.dumps(mode)})")

    def apply_custom_options(self, opts: dict):
        self._eval_js(f"EdithBridge.setCustomOptions({json.dumps(opts)})")

    def get_language_name(self) -> str:
        return get_language_name(self._language_id or "plaintext")

    def get_language_id(self):
        return self._language_id

    def get_line_ending(self) -> str:
        return self._line_ending

    def get_word_wrap(self) -> bool:
        return self._word_wrap

    def get_cursor_position(self) -> tuple[int, int]:
        return self._cursor_line, self._cursor_col
