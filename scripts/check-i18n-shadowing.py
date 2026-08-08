#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fail when a scope both binds the name `_` and calls `_()`.

gettext is imported as `_`, and `_` is also the usual name for a value being
ignored — a callback argument, an unused tuple element. Where the two meet,
the translation function is gone:

    def on_success(_):                    # `_` is now this function's argument
        show_toast(_("Downloaded"))       # TypeError: 'NoneType' is not callable

    def _build_ui(self):
        btn = Gtk.Button(label=_("Cancel"))   # UnboundLocalError: the binding
        for _, label in FORMATS:              # below makes `_` local to the
            ...                               # whole function, including here

Neither is caught by ruff, flake8 or pylint: `_` is exactly what they expect
an ignored value to be called. The first flavour only fails when its line
runs, so it reaches users; the second breaks its dialog outright.

Usage: check-i18n-shadowing.py [package-dir]     (default: edith)
"""

import ast
import sys
from pathlib import Path

GETTEXT_NAMES = ("_",)


class Scope:
    """A function-like scope: what it binds, and where it sits in the tree."""

    def __init__(self, node, parent):
        self.node = node
        self.parent = parent
        self.binds = set()
        self.globals = set()


def _target_names(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            yield n.id


def build_scopes(tree):
    """Map every AST node to its innermost function scope.

    Class bodies are deliberately not scopes here: a name bound in a class
    body is not visible to its methods, so it cannot shadow anything.
    Comprehensions are, since Python 3 gives them their own.
    """
    scopes = {}
    module = Scope(tree, None)

    def visit(node, scope):
        scopes[node] = scope

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                             ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            inner = Scope(node, scope)
            # Decorators and default values are evaluated in the *outer* scope.
            for child in ast.iter_child_nodes(node):
                if child in getattr(node, "decorator_list", []):
                    visit(child, scope)
                else:
                    visit(child, inner)
            args = getattr(node, "args", None)
            if args is not None:
                for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs,
                            args.vararg, args.kwarg]:
                    if arg is not None:
                        inner.binds.add(arg.arg)
            return

        if isinstance(node, ast.Global):
            scope.globals.update(node.names)
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                scope.binds.update(_target_names(t))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            scope.binds.update(_target_names(node.target))
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            scope.binds.update(_target_names(node.optional_vars))
        elif isinstance(node, ast.ExceptHandler) and node.name:
            scope.binds.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                scope.binds.add(alias.asname or alias.name.split(".")[0])

        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, module)
    return scopes, module


def check_file(path):
    """Yield (lineno, name, scope_description) for each shadowed call."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    scopes, module = build_scopes(tree)

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        name = node.func.id
        if name not in GETTEXT_NAMES:
            continue

        scope = scopes.get(node.func)
        while scope is not None and scope is not module:
            if name in scope.globals:
                break                      # explicitly declared global — fine
            if name in scope.binds:
                owner = scope.node
                where = getattr(owner, "name", "<lambda>")
                yield node.lineno, name, f"{where}() at line {owner.lineno}"
                break
            scope = scope.parent


def main(argv):
    root = Path(argv[1] if len(argv) > 1 else "edith")
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        return 2

    findings = []
    for path in sorted(root.rglob("*.py")):
        try:
            findings.extend((path, *hit) for hit in check_file(path))
        except SyntaxError as exc:
            print(f"ERROR: cannot parse {path}: {exc}", file=sys.stderr)
            return 2

    for path, lineno, name, where in findings:
        print(f"{path}:{lineno}: `{name}()` is shadowed by a binding in {where}")

    if findings:
        print(f"\n{len(findings)} shadowed gettext call(s). Rename the *binding* "
              f"(`_result`, `_unused`), never the call — xgettext extracts by name.",
              file=sys.stderr)
        return 1

    print(f"OK: no shadowed gettext calls under {root}/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
