#!/usr/bin/env bash
# Download Monaco Editor, Emmet, terser and csso into edith/data/monaco/.
set -euo pipefail

MONACO_VERSION="0.52.2"
EMMET_VERSION="5.0.0"
TERSER_VERSION="5.51.2"
CSSO_VERSION="5.0.5"
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
rm -rf "$TMPDIR/package"

# ── Minifiers (services/minifier.py runs these under JavaScriptCore) ──────
echo "Downloading terser@${TERSER_VERSION}…"
npm pack "terser@${TERSER_VERSION}" --pack-destination "$TMPDIR" >/dev/null 2>&1
tar -xzf "$TMPDIR"/terser-*.tgz -C "$TMPDIR"
cp "$TMPDIR/package/dist/bundle.min.js" "$DATADIR/terser.js"
rm -rf "$TMPDIR/package"

echo "Downloading csso@${CSSO_VERSION}…"
npm pack "csso@${CSSO_VERSION}" --pack-destination "$TMPDIR" >/dev/null 2>&1
tar -xzf "$TMPDIR"/csso-*.tgz -C "$TMPDIR"
cp "$TMPDIR/package/dist/csso.js" "$DATADIR/csso.js"

echo "Done — Monaco: $DEST  Emmet, terser, csso: $DATADIR"
