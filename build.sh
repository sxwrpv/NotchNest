#!/bin/bash
# Builds NotchNest.app from the Swift package. No Xcode required — uses SwiftPM
# plus a hand-assembled .app bundle. Pass --run to launch it when done.
#
# The bundle is self-contained: it carries the dictation engine's source, its
# hash-locked requirements and the `uv` installer, and provisions Python and
# the models itself on first launch (see Sources/NotchNest/Setup/). Package a
# shareable disk image with ./package.sh.
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

# uv provisions the engine's Python on the user's Mac. It's a single static
# binary (MIT/Apache-2.0); ship the local one unless UV_BIN points elsewhere.
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "$UV_BIN" ]]; then
    echo "uv not found — install it (brew install uv, or https://docs.astral.sh/uv/) or set UV_BIN." >&2
    exit 1
fi
if ! lipo -archs "$UV_BIN" 2>/dev/null | grep -q arm64; then
    echo "$UV_BIN is not an arm64 binary; NotchNest's engine needs Apple Silicon." >&2
    exit 1
fi

echo "==> Assembling $BUNDLE…"
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources" "$BUNDLE/Contents/Helpers"
cp "$BIN" "$BUNDLE/Contents/MacOS/$APP_NAME"
cp "Resources/Info.plist" "$BUNDLE/Contents/Info.plist"
cp "Resources/AppIcon.icns" "$BUNDLE/Contents/Resources/AppIcon.icns"
printf 'APPL????' > "$BUNDLE/Contents/PkgInfo"

cp -L "$UV_BIN" "$BUNDLE/Contents/Helpers/uv"
chmod 755 "$BUNDLE/Contents/Helpers/uv"
mkdir -p "$BUNDLE/Contents/Resources/Licenses"
cp LICENSE "$BUNDLE/Contents/Resources/Licenses/NotchNest-LICENSE"
cp Packaging/licenses/* "$BUNDLE/Contents/Resources/Licenses/"

# Engine source only — its venv, tests and caches stay in the repo.
ENGINE="$BUNDLE/Contents/Resources/DictationEngine"
mkdir -p "$ENGINE/murmur"
cp DictationEngine/{main.py,setup_engine.py,config.default.yaml,requirements.lock} "$ENGINE/"
cp DictationEngine/murmur/*.py "$ENGINE/murmur/"

echo "==> Code signing…"
# The self-signed "NotchNest Dev" identity keeps the signature stable across
# rebuilds, so TCC grants (Accessibility, Microphone) stick permanently — on
# this Mac and on every Mac that installs a build signed with it.
if security find-identity -v -p codesigning 2>/dev/null | grep -q "NotchNest Dev"; then
    IDENTITY="NotchNest Dev"
    NOTE="NotchNest Dev — TCC grants persist across rebuilds"
else
    IDENTITY="-"
    NOTE="ad-hoc — TCC grants reset each rebuild"
fi
# Inside-out: the nested helper first, then the bundle that seals it.
codesign --force --sign "$IDENTITY" "$BUNDLE/Contents/Helpers/uv"
codesign --force --sign "$IDENTITY" "$BUNDLE"
codesign --verify --strict "$BUNDLE"
echo "    signed ($NOTE)"

echo "==> Done: $(pwd)/$BUNDLE"

if [[ "${1:-}" == "--run" ]]; then
    echo "==> Launching…"
    # Kill any previous instance first so we always run the fresh build.
    pkill -x "$APP_NAME" 2>/dev/null || true
    sleep 0.3
    open "$BUNDLE"
fi
