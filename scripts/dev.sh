#!/usr/bin/env bash
# Run Refract Image in development.
#
#   ./scripts/dev.sh              native window pointed at vite: hot reload, real app
#   ./scripts/dev.sh --web        backend + vite only, for browser work and QA screenshots
#   ./scripts/dev.sh --web --mock backend in mock mode: full UI, placeholder images
#
# Native mode is the app itself (scripts/make-swift-app.sh, built on first run) loading vite's
# page with --dev-url, so an edit reloads the window while the service, the menus and the file
# panels are still the real thing. The web mode instead prints a URL carrying the backend address
# and token, which is what the frontend's connection resolution looks for in a browser.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.runtime/bin/python"

# shellcheck source=scripts/_env.sh   (cache locations + REFRACT_ROOT/HF_HOME)
source "$ROOT/scripts/_env.sh"

MODE="native"
MOCK=0
for arg in "$@"; do
  case "$arg" in
    --web) MODE="web" ;;
    --mock) MOCK=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

[[ -x "$PY" ]] || { echo "Run ./scripts/bootstrap.sh first." >&2; exit 1; }

export REFRACT_PYTHON="$PY"
# Fallbacks when the backend could not be imported to resolve them above: the same rules
# paths.py applies, so running the service without a runtime still lands in the right place.
export REFRACT_ROOT="${REFRACT_ROOT:-$ROOT/.refract}"
export HF_HOME="${HF_HOME:-$REFRACT_ROOT/hf-home}"

if [[ "$MODE" == "native" ]]; then
  APP="$ROOT/Refract Image.app"
  BIN="$APP/Contents/MacOS/RefractImage"
  if [[ ! -x "$BIN" ]]; then
    echo "building the native app (one time)"
    "$ROOT/scripts/make-swift-app.sh"
  fi
  [[ -d "$ROOT/app/node_modules" ]] || (cd "$ROOT/app" && npm update --package-lock=false --no-audit --no-fund)

  DEV_URL="http://localhost:${REFRACT_DEV_PORT:-1420}"
  VITE_PID=""
  # Reuse a vite that is already up — vite's own strictPort refuses a second one, and an
  # already-running dev server is the normal state a few seconds into a session.
  if ! curl -s -o /dev/null "$DEV_URL"; then
    (cd "$ROOT/app" && npm run dev > "$ROOT/.runtime/vite-dev.log" 2>&1) &
    VITE_PID=$!
    trap '[[ -n "${VITE_PID:-}" ]] && kill "$VITE_PID" 2>/dev/null || true' EXIT
    for _ in $(seq 1 120); do
      curl -s -o /dev/null "$DEV_URL" && break
      sleep 0.25
    done
  fi
  echo "window: $DEV_URL, log: $ROOT/.runtime/vite-dev.log"

  # Not exec'd: the app exiting has to take the dev server with it, or the next run finds a
  # stranger holding port 1420 showing yesterday's code.
  "$BIN" --dev-url "$DEV_URL" --verbose $([[ $MOCK -eq 1 ]] && echo --mock)
  exit $?
fi

PORT="${REFRACT_PORT:-8765}"
TOKEN="${REFRACT_TOKEN:-dev-token-$(date +%s)}"
LOG="$ROOT/.runtime/backend-dev.log"

PYTHONUNBUFFERED=1 "$PY" -m refract_backend --port "$PORT" --token "$TOKEN" --log-level info \
  $([[ $MOCK -eq 1 ]] && echo --mock) > "$LOG" 2>&1 &
BACKEND_PID=$!
trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT

# Wait for the service, then hand the UI a URL it can connect with.
for _ in $(seq 1 60); do
  if grep -q "REFRACT_READY" "$LOG" 2>/dev/null; then break; fi
  sleep 0.5
done
READY_LINE="$(grep -m1 "REFRACT_READY" "$LOG" || true)"
if [[ -z "$READY_LINE" ]]; then
  echo "The backend did not report readiness. See $LOG" >&2
  tail -20 "$LOG" >&2 || true
  exit 1
fi
REAL_PORT="$(printf '%s' "$READY_LINE" | sed -E 's/.*"port": *([0-9]+).*/\1/')"
MOCK_FLAG="$(printf '%s' "$READY_LINE" | grep -q '"mock": *true' && echo 1 || echo 0)"

echo "backend ready on 127.0.0.1:$REAL_PORT (mock=$MOCK_FLAG), log: $LOG"
echo "data root: $REFRACT_ROOT"
echo "open http://localhost:1420/?api=http://127.0.0.1:$REAL_PORT&token=$TOKEN&mock=$MOCK_FLAG"
cd "$ROOT/app"
exec npm run dev
