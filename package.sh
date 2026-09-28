#!/bin/bash
# Builds a shareable NotchNest disk image, a zip, and the one-line installer
# (install.sh, pinned to this build's zip checksum) in dist/. ./release.sh
# publishes them as a GitHub release.
#
# The app inside is self-contained: on a new Mac it installs its own Python,
# the hash-locked engine packages and the right models for that Mac's memory
# on first launch. Recipients only need Apple Silicon + macOS 14 or later.
set -euo pipefail

cd "$(dirname "$0")"
./build.sh

APP="NotchNest.app"
VERSION=$(/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" "$APP/Contents/Info.plist")
NAME="NotchNest-$VERSION"
mkdir -p dist

echo "==> Staging disk image…"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/$APP"
ln -s /Applications "$STAGE/Applications"
cp "Packaging/READ ME FIRST.txt" "$STAGE/"

echo "==> Creating dist/$NAME.dmg…"
rm -f "dist/$NAME.dmg"
hdiutil create -quiet -volname "NotchNest $VERSION" -srcfolder "$STAGE" \
    -fs HFS+ -format UDZO -imagekey zlib-level=9 "dist/$NAME.dmg"

echo "==> Creating dist/$NAME.zip…"
rm -f "dist/$NAME.zip"
ditto -c -k --keepParent "$APP" "dist/$NAME.zip"

echo "==> Verifying…"
MOUNT=$(mktemp -d)
hdiutil attach -quiet -nobrowse -readonly -mountpoint "$MOUNT" "dist/$NAME.dmg"
codesign --verify --strict "$MOUNT/$APP"
hdiutil detach -quiet "$MOUNT"

echo "==> Writing dist/install.sh…"
ZIP_SHA=$(shasum -a 256 "dist/$NAME.zip" | cut -d' ' -f1)
sed -e "s/@VERSION@/$VERSION/g" -e "s/@SHA256@/$ZIP_SHA/g" Packaging/install.sh.in > dist/install.sh
chmod 755 dist/install.sh
bash -n dist/install.sh

cd dist
shasum -a 256 "$NAME.dmg" "$NAME.zip" install.sh | tee "$NAME.sha256"
ls -lh "$NAME.dmg" "$NAME.zip" install.sh
echo "==> Share dist/$NAME.dmg — recipients follow READ ME FIRST inside it."
