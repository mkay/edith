#!/bin/bash
# SPDX-FileCopyrightText: 2025 Kreuder <mk@singular.de>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Build Edith's Flatpak and publish it to the static OSTree repo that users add
# as a remote. Run from a clean checkout at the tag you are releasing: the
# manifest's source is `type: dir`, so whatever is in the working tree is what
# ships, and a stray edit would be published as if it were the release.
#
# The published repo is a plain directory of content-addressed files served over
# HTTP — no OSTree software runs on the host. GitHub Pages is therefore enough.
#
# Not wired into release.sh: publishing is a separate decision from tagging, and
# a failure here should never leave a half-finished release behind.

set -euo pipefail

APP_ID="de.singular.edith"
MANIFEST="build-aux/flatpak/de.singular.edith.yaml"

# Throwaway build state; the repo below is the only durable output.
BUILD_DIR="${EDITH_FLATPAK_BUILD:-$HOME/.cache/edith-flatpak/build}"
REPO="${EDITH_FLATPAK_REPO:-$HOME/.cache/edith-flatpak/repo}"

# Working checkout of the GitHub Pages repo that serves the files.
PAGES="${EDITH_PAGES_CHECKOUT:-$HOME/Staging/edith-flatpak}"
PAGES_URL="${EDITH_PAGES_URL:-https://mkay.github.io/edith-flatpak/}"

# Signing key. Passphrase-protected by design, so gpg-agent will prompt once.
GPG_KEY="${EDITH_GPG_KEY:-}"

die() { echo "ERROR: $*" >&2; exit 1; }

[[ -f "$MANIFEST" ]] || die "run this from the repository root ($MANIFEST not found)"
[[ -n "$GPG_KEY" ]] || die "set EDITH_GPG_KEY to the signing key's fingerprint"
gpg --list-secret-keys "$GPG_KEY" >/dev/null 2>&1 || die "no secret key for $GPG_KEY"

# The manifest ships the working tree, so a dirty tree ships uncommitted work.
if ! git diff --quiet || ! git diff --cached --quiet; then
    die "working tree is dirty — commit or stash before publishing"
fi

VERSION=$(git describe --tags --exact-match 2>/dev/null || true)
if [[ -z "$VERSION" ]]; then
    echo "WARNING: HEAD is not tagged; publishing an untagged build" >&2
    VERSION=$(git rev-parse --short HEAD)
fi
echo "==> Publishing $APP_ID at $VERSION"

# 1. Build unsigned. org.flatpak.Builder is itself a Flatpak and its sandbox
#    ships no pinentry, so a --gpg-sign here fails with "GPG Agent: No pinentry"
#    however warm the host's passphrase cache is. Signing happens on the host in
#    step 3, where the agent can actually reach a pinentry.
echo "==> Building"
flatpak run org.flatpak.Builder --user --force-clean \
    --repo="$REPO" \
    "$BUILD_DIR" "$MANIFEST"

# 2. Debug symbols roughly double the repo for something no user of a binary
#    remote will ever consume. Dropped before the summary is regenerated so the
#    ref never appears to clients; prune then reclaims the objects.
if ostree refs --repo="$REPO" | grep -q "^runtime/$APP_ID.Debug/"; then
    echo "==> Dropping the Debug ref"
    ostree refs --repo="$REPO" --delete "runtime/$APP_ID.Debug/x86_64/master"
fi

# 3. Sign every remaining commit, on the host. Done after the Debug ref is gone
#    so nothing is signed that is about to be pruned.
echo "==> Signing commits"
for ref in $(ostree refs --repo="$REPO"); do
    ostree gpg-sign --repo="$REPO" "$ref" "$GPG_KEY" >/dev/null
    echo "    signed $ref"
done

# 4. Static deltas turn an update into one small download instead of hundreds of
#    object fetches. --prune drops anything no ref reaches any more, which is
#    what stops the repo growing without bound across releases. This also signs
#    the summary, which is what a client checks first.
echo "==> Updating repo metadata"
flatpak build-update-repo \
    --generate-static-deltas \
    --prune \
    --gpg-sign="$GPG_KEY" \
    "$REPO"

# 5. Publish. rsync --delete so a pruned object actually disappears from the
#    served copy; without it the repo would only ever accumulate.
[[ -d "$PAGES/.git" ]] || die "$PAGES is not a git checkout — clone the Pages repo there first"
# Replaced wholesale rather than merged: a pruned object has to disappear from
# the served copy too, and at this size a full copy costs less than depending on
# rsync, which is not installed everywhere. Only repo/ is touched — the README,
# .nojekyll and .flatpakrepo live beside it and survive.
echo "==> Syncing into $PAGES"
rm -rf "$PAGES/repo"
cp -a "$REPO" "$PAGES/repo"

# 6. The file users actually click. Regenerated every time so the embedded key
#    can never drift from the key the repo was signed with. The summary is read
#    from the metainfo rather than repeated here, so the two cannot disagree.
SUMMARY=$(sed -n 's:.*<summary>\(.*\)</summary>.*:\1:p' data/de.singular.edith.metainfo.xml.in | head -1)
[[ -n "$SUMMARY" ]] || die "could not read <summary> from the metainfo"
echo "==> Writing edith.flatpakrepo"
cat > "$PAGES/edith.flatpakrepo" <<EOF
[Flatpak Repo]
Title=Edith
Url=${PAGES_URL}repo/
Homepage=https://github.com/mkay/edith
Comment=$SUMMARY
Description=A GTK4 client for editing files that live on a remote server.
Icon=${PAGES_URL}icon.svg
GPGKey=$(gpg --export "$GPG_KEY" | base64 -w0)
EOF

# GitHub Pages runs Jekyll by default, which ignores directories beginning with
# an underscore and would silently drop parts of the repo.
touch "$PAGES/.nojekyll"

echo "==> Committing"
git -C "$PAGES" add -A
if git -C "$PAGES" diff --cached --quiet; then
    echo "==> Nothing changed, not committing"
else
    git -C "$PAGES" commit -q -m "Publish $VERSION"
    echo "==> Run: git -C $PAGES push"
fi

echo
echo "Published $VERSION. Users install with:"
echo "  flatpak remote-add --user edith ${PAGES_URL}edith.flatpakrepo"
echo "  flatpak install edith $APP_ID"
