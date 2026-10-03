#!/bin/bash
# Publishes the current version as a GitHub release: builds the DMG, zip and
# install.sh with package.sh, tags v<version> and uploads them.
#
#   1. bump CFBundleShortVersionString (and CFBundleVersion) in Resources/Info.plist
#   2. update Packaging/release-notes.md, commit, push
#   3. ./release.sh
#
# The one-line installer always fetches the newest release's install.sh:
#   curl -fsSL https://github.com/sxwrpv/NotchNest/releases/latest/download/install.sh | bash
set -euo pipefail

cd "$(dirname "$0")"
VERSION=$(/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" Resources/Info.plist)
TAG="v$VERSION"

if [[ -n "$(git status --porcelain)" ]]; then
    echo "Commit your changes first — the release is built from the tagged commit." >&2
    exit 1
fi
git fetch --quiet origin
if [[ -z "$(git branch -r --contains HEAD)" ]]; then
    echo "Push this commit first (it isn't on GitHub yet)." >&2
    exit 1
fi
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || gh release view "$TAG" >/dev/null 2>&1; then
    echo "$TAG already exists — bump the version in Resources/Info.plist." >&2
    exit 1
fi

./package.sh

NAME="NotchNest-$VERSION"
git tag -a "$TAG" -m "NotchNest $VERSION"
git push --quiet origin "$TAG"
gh release create "$TAG" \
    --title "NotchNest $VERSION" \
    --notes-file Packaging/release-notes.md \
    "dist/$NAME.dmg" "dist/$NAME.zip" "dist/$NAME.sha256" dist/install.sh

echo "==> Released $TAG. Install with:"
echo "    curl -fsSL https://github.com/sxwrpv/NotchNest/releases/latest/download/install.sh | bash"
