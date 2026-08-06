#!/usr/bin/env bash
# Regenerate python-deps.json from the runtime's Python version.
#
# cryptography, bcrypt and pynacl build from Rust/C sources by default, which
# would drag cargo vendoring into the Flatpak build. We take the upstream
# manylinux wheels instead — hence --prefer-wheels, which needs --runtime so
# pip resolves against the runtime's interpreter rather than the host's.
set -euo pipefail

RUNTIME_VERSION=49
HERE="$(cd "$(dirname "$0")" && pwd)"

command -v flatpak-pip-generator >/dev/null || {
  echo "Install flatpak-builder-tools first:" >&2
  echo "  https://github.com/flatpak/flatpak-builder-tools/tree/master/pip" >&2
  exit 1
}

flatpak install -y flathub "org.gnome.Sdk//${RUNTIME_VERSION}"

flatpak-pip-generator \
  --runtime="org.gnome.Sdk//${RUNTIME_VERSION}" \
  --prefer-wheels=cryptography,bcrypt,pynacl,cffi \
  --ignore-pkg=invoke \
  --cleanup=all \
  --output="${HERE}/python-deps" \
  paramiko keyring defusedxml

echo "Wrote ${HERE}/python-deps.json"
