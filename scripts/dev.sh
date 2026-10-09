#!/usr/bin/env bash
# Run Refract Image in development.
#
#   ./scripts/dev.sh              rebuild and run the native SwiftUI app
#   ./scripts/dev.sh --web        backend + vite only, for browser work and QA screenshots
#   ./scripts/dev.sh --web --mock backend in mock mode: full UI, placeholder images
#
# Native mode starts only the SwiftUI app and its owned inference backend.
# --web keeps the legacy React development interface available separately.
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
export REFRACT_ROOT="${REFRACT_ROOT:-$WORKSPACE/.refract}"
export HF_HOME="${HF_HOME:-$REFRACT_ROOT/hf-home}"

if [[ "$MODE" == "native" ]]; then
  APP="$WORKSPACE/Refract Image.app"
  BIN="$APP/Contents/MacOS/RefractImage"
  "$ROOT/scripts/make-swift-app.sh"
  ARGS=(--verbose)
  (( MOCK )) && ARGS+=(--mock)
  exec "$BIN" "${ARGS[@]}"
fi

PORT="${REFRACT_PORT:-8765}"
TOKEN="${REFRACT_TOKEN:-dev-token-$(date +%s)}"
LOG="$WORKSPACE/.runtime/backend-dev.log"

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
npm run dev
