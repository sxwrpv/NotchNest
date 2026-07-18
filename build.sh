#!/bin/bash
# Builds NotchNest.app from the Swift package. No Xcode required — uses SwiftPM
# plus a hand-assembled .app bundle. Pass --run to launch it when done.
set -euo pipefail

cd "$(dirname "$0")"
APP_NAME="NotchNest"
BUNDLE="$APP_NAME.app"
CONFIG="release"

echo "==> Building ($CONFIG)…"
swift build -c "$CONFIG"

BIN=".build/$CONFIG/$APP_NAME"
if [[ ! -f "$BIN" ]]; then
    echo "Build produced no binary at $BIN" >&2
    exit 1
fi

echo "==> Assembling $BUNDLE…"
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents/MacOS"
mkdir -p "$BUNDLE/Contents/Resources"
cp "$BIN" "$BUNDLE/Contents/MacOS/$APP_NAME"
cp "Resources/Info.plist" "$BUNDLE/Contents/Info.plist"
printf 'APPL????' > "$BUNDLE/Contents/PkgInfo"

echo "==> Code signing…"
# The self-signed "NotchNest Dev" identity keeps the signature stable across
# rebuilds, so TCC grants (Accessibility, Microphone) stick permanently.
if security find-identity -v -p codesigning 2>/dev/null | grep -q "NotchNest Dev" \
   && codesign --force --deep --sign "NotchNest Dev" "$BUNDLE" 2>/dev/null; then
    echo "    signed (NotchNest Dev — TCC grants persist across rebuilds)"
elif codesign --force --deep --sign - "$BUNDLE" 2>/dev/null; then
    echo "    signed (ad-hoc — TCC grants reset each rebuild)"
else
    echo "    codesign unavailable — app will still run locally"
fi

echo "==> Done: $(pwd)/$BUNDLE"

if [[ "${1:-}" == "--run" ]]; then
    echo "==> Launching…"
    # Kill any previous instance first so we always run the fresh build.
    pkill -x "$APP_NAME" 2>/dev/null || true
    sleep 0.3
    open "$BUNDLE"
fi
