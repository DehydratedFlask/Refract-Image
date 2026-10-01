#!/usr/bin/env bash
# One-time setup for Refract Image.
#
# Creates the Python runtime that the app spawns (mflux on MLX, pinned to a commit), the
# frontend dependencies, and the runtime.json the app reads to find its interpreter — a
# GUI app on macOS does not inherit your shell environment, so it cannot rely on PATH.
#
#   ./scripts/bootstrap.sh            # normal install
#   ./scripts/bootstrap.sh --refresh  # re-resolve mflux from its main branch
#   ./scripts/bootstrap.sh --runtime /Volumes/Runtime/refract-runtime
#                                    # put the runtime somewhere else (the first-run
#                                    # setup window offers exactly this)
#   ./scripts/bootstrap.sh --data-root /Volumes/Models/Refract
#                                    # put models, downloads and outputs somewhere else
#   ./scripts/bootstrap.sh --program ~/Library/Application\ Support/Refract/program
#                                    # install the backend from an extracted app payload
#                                    # instead of the checkout, for a standalone install
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$ROOT/.runtime"
PY="$RUNTIME/bin/python"
PIN_FILE="$RUNTIME/mflux-commit.txt"
REFRESH=0
DATA_DIR_OVERRIDE=""
PROGRAM_DIR=""

# Flags are parsed before anything is created, because the runtime directory they may move is
# the first thing this script writes.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --refresh) REFRESH=1 ;;
    --runtime) RUNTIME="${2:?--runtime needs a path}"; PY="$RUNTIME/bin/python"; PIN_FILE="$RUNTIME/mflux-commit.txt"; shift ;;
    --data-root) DATA_DIR_OVERRIDE="${2:?--data-root needs a path}"; shift ;;
    --program) PROGRAM_DIR="${2:?--program needs a path}"; shift ;;
    *) printf 'Unknown option %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

# Puts the uv/npm caches next to the checkout instead of on the internal disk. Safe to
# source before the runtime exists: it only asks the backend for the data root if it can.
# shellcheck source=scripts/_env.sh
source "$ROOT/scripts/_env.sh"

say() { printf "\033[1;34m==>\033[0m %s\n" "$1"; }
fail() { printf "\033[1;31mError:\033[0m %s\n" "$1" >&2; exit 1; }

command -v uv >/dev/null || fail "uv is required (https://docs.astral.sh/uv/). Install it, then re-run."
if [[ -f "$ROOT/app/package.json" ]]; then
  command -v node >/dev/null || fail "Node.js is required to build the UI."
fi
# No Rust and no Xcode project: the app shell is compiled by swiftc from the Command Line Tools,
# which scripts/make-swift-app.sh checks for itself.

say "Python runtime in $RUNTIME"
uv venv "$RUNTIME" --python 3.12 >/dev/null

# mflux from source: the released 0.20.0 wheel has no Qwen-Image-2.1 reference-editing
# pipeline (no `qwen21/reference`, no mflux-generate-qwen-2.1-edit), and it also cannot
# load a pack whose text encoder is already quantised. Both are required here, so the
# runtime pins a commit from main rather than a release tag.
if [[ $REFRESH -eq 1 || ! -f "$PIN_FILE" ]]; then
  say "Resolving mflux from its main branch"
  SHA="$(git ls-remote https://github.com/mflux-community/mflux.git HEAD | cut -f1)"
  [[ -n "$SHA" ]] || fail "could not reach GitHub to resolve mflux"
  printf '%s' "$SHA" > "$PIN_FILE"
else
  SHA="$(cat "$PIN_FILE")"
fi

say "Installing the latest compatible runtime dependencies"
DEPS=(fastapi 'uvicorn[standard]' pillow huggingface_hub safetensors python-multipart
  "mflux @ git+https://github.com/mflux-community/mflux.git@${SHA}")
# A standalone install has no checkout to install from: the backend is plain Python that the
# app runs straight out of the payload it extracted, so it needs its dependencies but not an
# editable install of itself. The app puts PYTHONPATH on it.
BACKEND_SRC="${PROGRAM_DIR:+$PROGRAM_DIR/backend}"
if [[ -n "$BACKEND_SRC" && -d "$BACKEND_SRC" ]]; then
  say "  (dependencies only; the backend is run from $BACKEND_SRC)"
else
  say "Installing the Refract Image backend"
  DEPS+=( -e "$ROOT/backend" )
fi
# Resolve the highest compatible releases every time bootstrap runs, including after a
# partially completed install. The source URL keeps mflux on the commit resolved above;
# backend/pyproject.toml supplies the rest of the backend's declared requirements.
uv pip install --upgrade --python "$PY" "${DEPS[@]}"

say "Installing app dependencies"
# A standalone install ships the built UI inside the payload and has no `app/` to build, so
# Node is not a requirement there — only a checkout needs it.
if [[ -f "$ROOT/app/package.json" ]]; then
  (cd "$ROOT/app" && npm update --package-lock=false --no-audit --no-fund)
else
  say "  (no checkout to build; the UI comes from the app payload)"
fi

# The backend is importable now, so this resolves the real data root: `.refract` beside the
# checkout when it is on an external volume, ~/Library/Application Support/Refract
# otherwise. A GUI app started from Finder inherits no shell environment, so the path is
# recorded on disk for it to read.
source "$ROOT/scripts/_env.sh"
DATA_DIR="${DATA_DIR_OVERRIDE:-${REFRACT_ROOT:?could not resolve the data root}}"
mkdir -p "$DATA_DIR"
cat > "$DATA_DIR/runtime.json" <<JSON
{
  "python": "$PY",
  "repo_root": "$ROOT",
  "runtime_root": "$RUNTIME",
  "mflux_commit": "$SHA",
  "data_root": "$DATA_DIR"
}
JSON

say "Wrote $DATA_DIR/runtime.json"
say "Verifying the runtime imports"
"$PY" - <<'PY'
import importlib.metadata as md
from mflux.models.qwen21.reference import QwenImage21Edit  # noqa: F401

print(f"    mflux {md.version('mflux')} · mlx {md.version('mlx')} · reference editing available")
PY
uv pip check --python "$PY"

# setup.sh orchestrates bootstrap and prints its own summary, so it asks for silence here.
if [[ -n "${REFRACT_BOOTSTRAP_QUIET:-}" ]]; then
  say "Runtime ready"
  exit 0
fi

cat <<TXT

Bootstrap complete.

Storage (models, downloads, jobs, images):
  $DATA_DIR

Caches (uv wheels, npm tarballs, model downloads):
  $ROOT/.cache and $HF_HOME

Runtime (Python, mflux, MLX):
  $RUNTIME

Next:
  ./scripts/make-swift-app.sh          # build ./Refract Image.app
  ./scripts/dev.sh                     # run the app against vite (hot reload)
  ./scripts/dev.sh --web               # run the UI in a browser, for development
  ./scripts/prepare-model.sh           # optional: write a local model copy up front

The first generation downloads the 4-bit model pack (~8.9 GB) unless you prepare it now.
Source builds resolve the latest versions allowed by app/package.json; the committed npm
lockfile remains unchanged for reproducible builds that use npm ci.
TXT
