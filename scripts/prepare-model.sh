#!/usr/bin/env bash
# Prepare a model without opening the app.
#
#   ./scripts/prepare-model.sh                     # download + stage the Uncensored 4-bit model
#   ./scripts/prepare-model.sh --source upstream-q4
#   ./scripts/prepare-model.sh --list
#
# Preparing writes a local model directory, so later launches neither download anything
# nor (for upstream-q4) re-quantise the transformer.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.runtime/bin/python"
[[ -x "$PY" ]] || { echo "Run ./scripts/bootstrap.sh first." >&2; exit 1; }

# shellcheck source=scripts/_env.sh   (weights go to the checkout's volume, not the SSD-less one)
source "$ROOT/scripts/_env.sh"

exec "$PY" -m refract_backend.tools.prepare "$@"
