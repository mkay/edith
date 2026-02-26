#!/usr/bin/env bash
# Download Monaco Editor + Emmet and install into edith/data/monaco/.
set -euo pipefail

MONACO_VERSION="0.52.2"
EMMET_VERSION="5.0.0"
DATADIR="$(cd "$(dirname "$0")/.." && pwd)/edith/data/monaco"
DEST="$DATADIR/vs"

if [ -d "$DEST" ]; then
    echo "Monaco already present at $DEST — remove it first to re-fetch."
    exit 0
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# ── Monaco ────────────────────────────────────────────────────────────────
echo "Downloading monaco-editor@${MONACO_VERSION}…"
npm pack "monaco-editor@${MONACO_VERSION}" --pack-destination "$TMPDIR" >/dev/null 2>&1

echo "Extracting Monaco…"
tar -xzf "$TMPDIR"/monaco-editor-*.tgz -C "$TMPDIR"

mkdir -p "$DATADIR"
mv "$TMPDIR/package/min/vs" "$DEST"

# ── Emmet ─────────────────────────────────────────────────────────────────
echo "Downloading emmet-monaco-es@${EMMET_VERSION}…"
npm pack "emmet-monaco-es@${EMMET_VERSION}" --pack-destination "$TMPDIR" >/dev/null 2>&1

tar -xzf "$TMPDIR"/emmet-monaco-es-*.tgz -C "$TMPDIR"
cp "$TMPDIR/package/dist/emmet-monaco.min.js" "$DATADIR/emmet.js"

echo "Done — Monaco: $DEST  Emmet: $DATADIR/emmet.js"
