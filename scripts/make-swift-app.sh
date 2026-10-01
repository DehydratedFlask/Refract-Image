#!/usr/bin/env bash
# Build the native macOS app — a Swift/AppKit shell that owns the model service and renders the
# built UI in a window, instead of handing a URL to your browser.
#
#   ./scripts/make-swift-app.sh                 build ./Refract Image.app
#   ./scripts/make-swift-app.sh --out ~/Apps    write the bundle into another directory
#   ./scripts/make-swift-app.sh --name "Preview" write a bundle named Preview.app instead
#   ./scripts/make-swift-app.sh --skip-build    do not rebuild the UI first
#   ./scripts/make-swift-app.sh --smoke         launch headless checks after building
#   ./scripts/make-swift-app.sh --mock          build + smoke against the placeholder runner
#
# It needs the Command Line Tools (swiftc) and nothing else: no Xcode project, no package manager,
# no extra frameworks. The bundle is ad-hoc signed; the DMG is not notarized.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

OUT_DIR="$ROOT"
NAME="Refract Image"
APP_NAME="Refract Image"
EXECUTABLE_NAME="RefractImage"
VERSION="${REFRACT_APP_VERSION:-0.0.1}"
SKIP_BUILD=0
RUN_SMOKE=0
MOCK=0
IDENTITY="${REFRACT_SIGN_IDENTITY:--}"   # "-" is an ad-hoc signature

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT_DIR="${2:?--out needs a directory}"; shift ;;
    --name) NAME="${2:?--name needs a value}"; shift ;;
    --skip-build) SKIP_BUILD=1 ;;
    --smoke) RUN_SMOKE=1 ;;
    --mock) MOCK=1 ;;
    --sign) IDENTITY="${2:?--sign needs an identity}"; shift ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,\} \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf "\033[1;34m==>\033[0m %s\n" "$1"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$1" >&2; }

BUNDLE="$OUT_DIR/$NAME.app"
SWIFT_SOURCES=("$ROOT"/native/Sources/*.swift)

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "REFRACT_APP_VERSION must use major.minor.patch format." >&2
  exit 2
fi

# ------------------------------------------------------------------ prerequisites
if ! command -v xcrun >/dev/null 2>&1; then
  echo "xcrun not found: install the Xcode Command Line Tools (xcode-select --install)." >&2
  exit 1
fi
if ! xcrun --sdk macosx --show-sdk-path >/dev/null 2>&1; then
  echo "no macOS SDK. Run 'xcode-select --install' and try again." >&2
  exit 1
fi
SDK="$(xcrun --sdk macosx --show-sdk-path)"

if [[ ! -x "$ROOT/.runtime/bin/python" ]]; then
  warn "no runtime at .runtime/bin/python — the app will ask you to install it on first launch."
fi

# ------------------------------------------------------------------ the UI bundle
if (( ! SKIP_BUILD )); then
  if command -v npm >/dev/null 2>&1; then
    say "Building the UI"
    ( cd "$ROOT/app" && npm run build )
  else
    warn "npm not found; the app can only start if app/dist already exists."
  fi
fi

if [[ ! -f "$ROOT/app/dist/index.html" ]]; then
  echo "There is no built UI at app/dist/index.html, and the app has nothing to show without it." >&2
  echo "Run 'npm run build' in app/ (or drop --skip-build and let this script do it)." >&2
  exit 1
fi

# ------------------------------------------------------------------ compile
BUILD_DIR="$ROOT/native/build"
BIN="$BUILD_DIR/$EXECUTABLE_NAME"
mkdir -p "$BUILD_DIR"

say "Compiling ${#SWIFT_SOURCES[@]} Swift files"
xcrun -sdk macosx swiftc \
  -sdk "$SDK" \
  -target "arm64-apple-macos13.0" \
  -O \
  -swift-version 5 \
  -o "$BIN" \
  "${SWIFT_SOURCES[@]}" 2>&1 | sed 's/^/    /'

if [[ ! -x "$BIN" ]]; then
  echo "Compilation produced no binary." >&2
  exit 1
fi

# ------------------------------------------------------------------ bundle
say "Assembling $BUNDLE"
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"
cp "$BIN" "$BUNDLE/Contents/MacOS/$EXECUTABLE_NAME"
printf 'APPL????' > "$BUNDLE/Contents/PkgInfo"

cat > "$BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>local.refract.image</string>
  <key>CFBundleExecutable</key><string>$EXECUTABLE_NAME</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.graphics-design</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>CFBundleIconFile</key><string>icon</string>
</dict>
</plist>
PLIST

ICON="$ROOT/native/Resources/icon.icns"
if [[ -f "$ICON" ]]; then
  cp "$ICON" "$BUNDLE/Contents/Resources/icon.icns"
else
  warn "no native/Resources/icon.icns; the bundle will use the generic icon (see scripts/make-icon.py)."
fi

# ------------------------------------------------------------------ payload
# Everything the app needs that is not the binary: the built UI, the backend package, and the
# scripts that install them. About 1.3 MB compressed, which is the whole point — the heavy
# parts (a 3.5 GB runtime, 13-14 GB of weights) are downloaded during setup to wherever the
# user chooses, so the bundle itself stays small enough to be trivially distributable.
PAYLOAD_STAGE="$(mktemp -d)"
trap 'rm -rf "$PAYLOAD_STAGE"' EXIT

say "Staging the app payload"
mkdir -p "$PAYLOAD_STAGE/backend" "$PAYLOAD_STAGE/app" "$PAYLOAD_STAGE/scripts"
cp -R "$ROOT/backend/refract_backend" "$PAYLOAD_STAGE/backend/"
cp -R "$ROOT/app/dist" "$PAYLOAD_STAGE/app/dist"
cp "$ROOT/scripts/serve_app.py" "$PAYLOAD_STAGE/scripts/"
cp "$ROOT/scripts/bootstrap.sh" "$PAYLOAD_STAGE/scripts/"
cp "$ROOT/scripts/_env.sh" "$PAYLOAD_STAGE/scripts/"

# The first-run window offers a model choice before the runtime exists, so it cannot ask a
# running service what is available. This is that list, generated from the same registry the
# running app uses so the two cannot drift.
if [[ -x "$ROOT/.runtime/bin/python" ]]; then
  "$ROOT/.runtime/bin/python" -m refract_backend.tools.export_setup "$PAYLOAD_STAGE/setup.json" >/dev/null \
    || warn "could not generate setup.json; the setup window will not be able to list models."
else
  warn "no .runtime for export_setup; run scripts/bootstrap.sh to generate setup.json."
fi

# __pycache__ from a local test run would otherwise ride along and be stale on the user's disk.
find "$PAYLOAD_STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$PAYLOAD_STAGE" -name '*.pyc' -delete 2>/dev/null || true

(cd "$PAYLOAD_STAGE" && zip -q -r -X "$BUNDLE/Contents/Resources/payload.zip" .)
PAYLOAD_BYTES=$(stat -f%z "$BUNDLE/Contents/Resources/payload.zip")
if [[ $PAYLOAD_BYTES -lt 1048576 ]]; then
  say "Payload $((PAYLOAD_BYTES / 1024)) KB — the app installs everything else on first run"
else
  say "Payload $(printf '%.1f MB' "$(echo "scale=1; $PAYLOAD_BYTES / 1048576" | bc)") — the app installs everything else on first run"
fi

# An ad-hoc signature is what lets the bundle keep its identity across launches — which
# notifications and the saved window frame both hang on — without a developer certificate.
say "Signing ($IDENTITY)"
if ! codesign --force --sign "$IDENTITY" --timestamp=none "$BUNDLE" >/dev/null 2>&1; then
  warn "codesign failed; the app still runs, but notifications may not."
fi
/usr/bin/xattr -dr com.apple.quarantine "$BUNDLE" 2>/dev/null || true

say "Built $BUNDLE"
echo "    open it from Finder, or: open \"$BUNDLE\""
echo "    from a terminal:         \"$BUNDLE/Contents/MacOS/$EXECUTABLE_NAME\" --verbose"

# ------------------------------------------------------------------ smoke
if (( RUN_SMOKE )); then
  say "Running the smoke checks"
  ARGS=(--smoke)
  (( MOCK )) && ARGS+=(--mock)
  set +e
  output="$("$BUNDLE/Contents/MacOS/$EXECUTABLE_NAME" "${ARGS[@]}" 2>&1)"
  status=$?
  set -e
  printf '%s\n' "$output" | sed 's/^/    /'
  if (( status == 0 )); then
    say "Smoke checks passed"
  else
    echo "Smoke checks failed (exit $status)." >&2
    exit "$status"
  fi
fi
