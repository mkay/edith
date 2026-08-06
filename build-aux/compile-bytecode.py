#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Precompile Edith's Python sources at install time.

Without this there is no __pycache__ under the install prefix, which is
root-owned: every launch recompiles every module *and* fails to write the
result, so the cost is paid again on the next launch. That turns any lazily
imported module into a multi-second stall the first time it is reached —
including from inside a drag-and-drop handler, where it looks like a freeze.
"""

import compileall
import os
import sys

destdir = os.environ.get("DESTDIR", "")
prefix = os.environ.get("MESON_INSTALL_PREFIX", "/usr")
moduledir = os.path.join(prefix, "share", "edith")

if destdir:
    moduledir = os.path.join(destdir, os.path.relpath(moduledir, "/"))

if not os.path.isdir(moduledir):
    sys.exit(0)

# quiet=1 keeps the install log readable; a compile failure must not break
# the install, since the app still runs from source.
compileall.compile_dir(moduledir, quiet=1, force=True)
