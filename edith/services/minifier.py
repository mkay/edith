# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Minify JavaScript and CSS with terser and csso.

Both are pure JavaScript, so they run in a bare JavaScriptCore context —
no WebView, no DOM. The bundles live next to Monaco in data/monaco/ and
are fetched by scripts/fetch-monaco.sh.
"""

import json
import threading
from pathlib import Path

import gi

gi.require_version("JavaScriptCore", "6.0")

from gi.repository import JavaScriptCore as JSC

_DATA_DIR = Path(__file__).parent.parent / "data" / "monaco"

# Extension → minifier kind. Only what terser and csso actually handle.
MINIFY_KINDS = {
    "js": "js",
    "mjs": "js",
    "cjs": "js",
    "css": "css",
}

# terser's API is promise-based. JavaScriptCore drains the microtask queue
# before evaluate() returns, so the result is available synchronously.
_GLUE = """
var __edith_result = null, __edith_error = null;
function __edith_minify_js(code) {
    __edith_result = null; __edith_error = null;
    Terser.minify(code, {}).then(
        function (r) { __edith_result = r.code; },
        function (e) { __edith_error = String(e); });
}
function __edith_minify_css(code) {
    __edith_result = null; __edith_error = null;
    try { __edith_result = csso.minify(code).css; }
    catch (e) { __edith_error = String(e); }
}
"""

_lock = threading.Lock()
_ctx = None


def _context():
    global _ctx
    if _ctx is None:
        ctx = JSC.Context()
        for name in ("terser.js", "csso.js"):
            ctx.evaluate((_DATA_DIR / name).read_text(encoding="utf-8"), -1)
            exc = ctx.get_exception()
            if exc:
                raise RuntimeError(f"{name}: {exc.get_message()}")
        ctx.evaluate(_GLUE, -1)
        _ctx = ctx
    return _ctx


def minify(code: str, kind: str) -> str:
    """Return `code` minified. `kind` is "js" or "css". Blocking; call
    from a worker thread. Raises ValueError on a syntax error."""
    func = {"js": "__edith_minify_js", "css": "__edith_minify_css"}[kind]
    with _lock:
        ctx = _context()
        ctx.clear_exception()
        ctx.evaluate(f"{func}({json.dumps(code)})", -1)
        exc = ctx.get_exception()
        if exc:
            raise ValueError(exc.get_message())
        err = ctx.get_value("__edith_error")
        if not err.is_null():
            raise ValueError(err.to_string())
        return ctx.get_value("__edith_result").to_string()
