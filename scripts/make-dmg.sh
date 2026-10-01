#!/usr/bin/env bash
# Package the locally built app as a compressed drag-and-drop macOS disk image.
# Usage: ./scripts/make-dmg.sh [version]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-0.0.1}"
APP="$ROOT/Refract Image.app"
RELEASE_DIR="$ROOT/release"
ASSET="Refract-Image-$VERSION-macos-arm64.dmg"
DMG="$RELEASE_DIR/$ASSET"

if [[ $# -gt 1 || ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Usage: $0 [major.minor.patch]" >&2
  exit 2
fi
if [[ ! -d "$APP" ]]; then
  echo "Missing $APP. Build it first with ./scripts/make-swift-app.sh." >&2
  exit 1
fi
BUILT_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
if [[ "$BUILT_VERSION" != "$VERSION" ]]; then
  echo "App version $BUILT_VERSION does not match requested DMG version $VERSION." >&2
  echo "Rebuild with REFRACT_APP_VERSION=$VERSION ./scripts/make-swift-app.sh." >&2
  exit 1
fi
if ! command -v hdiutil >/dev/null 2>&1; then
  echo "hdiutil is required to create a macOS disk image." >&2
  exit 1
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/refract-image-dmg.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT
STAGING="$TMP_DIR/Refract Image"
mkdir -p "$STAGING" "$RELEASE_DIR"
ditto "$APP" "$STAGING/Refract Image.app"
ln -s /Applications "$STAGING/Applications"
cat > "$STAGING/Install.txt" <<'TXT'
Refract Image for macOS

Drag Refract Image.app to Applications, then open it.
The first setup needs uv and downloads the runtime and selected model.
This build targets Apple silicon and macOS 13 or later.
TXT

rm -f "$DMG"
hdiutil create \
  -volname "Refract Image" \
  -srcfolder "$STAGING" \
  -format UDZO \
  -ov \
  "$DMG"

(cd "$RELEASE_DIR" && shasum -a 256 "$ASSET" > SHA256SUMS.txt)
echo "Created $DMG"
echo "SHA-256: $(awk '{print $1}' "$RELEASE_DIR/SHA256SUMS.txt")"
