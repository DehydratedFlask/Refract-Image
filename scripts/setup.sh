#!/usr/bin/env bash
# One-click setup for Refract Image.
#
#   ./scripts/setup.sh              check prerequisites, install dependencies, fetch the model
#   ./scripts/setup.sh --yes        never prompt (scripted / unattended install)
#   ./scripts/setup.sh --skip-model dependencies only, no ~14 GB download
#   ./scripts/setup.sh --native     also build the native macOS app (Refract Image.app)
#   ./scripts/setup.sh --refresh    refresh compatible dependencies and re-resolve mflux
#   ./scripts/setup.sh --source X   prepare a different source id (see --list)
#
# Everything it runs is idempotent: re-running after an interruption picks up where it
# stopped. Hugging Face resumes partial files, a usable runtime is reused unless `--refresh`
# is passed, and an already-staged model is detected and skipped.
# Double-clicking `Install Refract Image.command` in the repository root runs this script.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$ROOT/.runtime"
PY="$RUNTIME/bin/python"

# ~14 GB of downloads plus the staged checkpoint. Downloads mostly become hard links into
# the staged model, but the relabelled transformer and the grafted visual tower are real
# copies, so the peak is comfortably under this. It is a floor, not a prediction.
MIN_FREE_GB=30

ASSUME_YES=0
SKIP_MODEL=0
BUILD_NATIVE=0
REFRESH=0
SOURCE="mlx-q4"

usage() {
  sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,\} \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y) ASSUME_YES=1 ;;
    --skip-model) SKIP_MODEL=1 ;;
    --native) BUILD_NATIVE=1 ;;
    --refresh) REFRESH=1 ;;
    --source) SOURCE="${2:?--source needs a source id (see ./scripts/prepare-model.sh --list)}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

say() { printf "\033[1;34m==>\033[0m %s\n" "$1"; }
note() { printf "    %s\n" "$1"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$1"; }
fail() { printf "\033[1;31mError:\033[0m %s\n" "$1" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

# Only ask when a human is actually there. A piped or CI run without --yes treats every
# prompt as "no" and prints the manual command instead of stalling on a read.
ask() {
  (( ASSUME_YES )) && return 0
  [[ -t 0 ]] || return 1
  printf "\033[1;33m?\033[0m %s [y/N] " "$1"
  read -r reply || return 1
  [[ "$reply" =~ ^[Yy] ]]
}

# A GUI app gets no shell PATH, but this script is run from Terminal, so make sure the
# usual install locations are searched even in a minimal shell.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"

# --------------------------------------------------------------------------- preflight
[[ "$(uname -s)" == "Darwin" ]] || fail "Refract Image runs on macOS only (it needs MLX)."
[[ "$(uname -m)" == "arm64" ]] || fail "MLX requires an Apple Silicon Mac (M-series). This is $(uname -m)."

MEM_GB=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 ))
if (( MEM_GB > 0 && MEM_GB < 32 )); then
  warn "This Mac reports ${MEM_GB} GB of unified memory. A 512 px generation peaks around 15.6 GB, so expect the model to be tight."
fi

# Missing tools are installed through Homebrew when it is available and the user consents;
# otherwise the exact command is printed so setup stops with a fixable message rather than a
# stack trace.
ensure_tool() {
  local cmd="$1" pkg="$2" hint="$3"
  have "$cmd" && return 0
  warn "$cmd is not installed."
  if have brew && ask "Install $cmd with Homebrew now (brew install $pkg)?"; then
    brew install "$pkg"
  fi
  have "$cmd" || fail "$cmd is required. $hint"
}

say "Checking prerequisites"
ensure_tool uv uv "Install it with: brew install uv  — or see https://docs.astral.sh/uv/"
ensure_tool node node "Install it with: brew install node"
note "uv $(uv --version 2>/dev/null | awk '{print $2}') · node $(node --version)"
# No Rust: the app shell is Swift compiled by swiftc, and `make-swift-app.sh` checks for the
# Command Line Tools itself, so a machine without them still gets the runtime and the model.
if [[ "$BUILD_NATIVE" == 1 ]] && ! xcrun --sdk macosx --show-sdk-path >/dev/null 2>&1; then
  warn "no macOS SDK (swiftc): the app build will be skipped. Install the Xcode Command Line Tools."
fi

# --------------------------------------------------------------------------- disk space
free_gb() { # a path's volume; walks up until an existing directory is found
  local dir="$1"
  while [[ ! -d "$dir" ]]; do dir="$(dirname "$dir")"; done
  df -Pk "$dir" | awk 'NR==2 {printf "%.0f", $4 / 1048576}'
}

check_space() {
  local label="$1" dir="$2" free
  free="$(free_gb "$dir")"
  (( free >= MIN_FREE_GB )) \
    || fail "$label is at $dir, which has only ${free} GB free. Setup needs about ${MIN_FREE_GB} GB for the downloads and the staged model — free space, or move the checkout to a larger drive."
  note "$label: ${free} GB free"
}

say "Checking free space"
check_space "Repository volume" "$ROOT"

# --------------------------------------------------------------------------- runtime + deps
# The runtime venv and npm modules are the expensive part of bootstrap, so a re-run skips
# it entirely when both are already usable. The import check is the same one bootstrap
# ends with: mflux's reference-editing pipeline is why the revision is pinned at all.
runtime_ready() {
  [[ -x "$PY" ]] || return 1
  [[ -d "$ROOT/app/node_modules" ]] || return 1
  "$PY" - <<'PY' >/dev/null 2>&1
import fastapi, uvicorn, huggingface_hub, safetensors, multipart, refract_backend  # noqa: F401
from PIL import Image  # noqa: F401
from mflux.models.qwen21.reference import QwenImage21Edit  # noqa: F401
PY
}

# --refresh re-resolves mflux and refreshes compatible runtime and UI packages, so it forces
# the bootstrap path even when the current runtime imports fine.
if runtime_ready && (( ! REFRESH )); then
  say "Python runtime and app dependencies are already in place"
else
  say "Installing the Python runtime, mflux and the app's dependencies"
  bootstrap_args=()
  (( REFRESH )) && bootstrap_args+=(--refresh)
  REFRACT_BOOTSTRAP_QUIET=1 "$ROOT/scripts/bootstrap.sh" "${bootstrap_args[@]}"
fi

# bootstrap wrote runtime.json and resolved the real data root, so _env.sh can now report
# where the weights will actually land — the checkout's volume on an external drive, or
# Application Support on the internal one.
source "$ROOT/scripts/_env.sh"
DATA_DIR="${REFRACT_ROOT:?could not resolve the data root}"
say "Data root"
note "$DATA_DIR"
check_space "Data volume" "$DATA_DIR"

# --------------------------------------------------------------------------- model
# `python -m refract_backend.model_store` here is deliberately the shallowest possible
# check: it reads the staged directory's index metadata (the same test staged_pack_dir
# applies everywhere else) and imports neither MLX nor the runner.
model_staged() {
  "$PY" - <<'PY' >/dev/null 2>&1
import sys
from refract_backend.model_store import staged_pack_dir
sys.exit(0 if staged_pack_dir() else 1)
PY
}

if (( SKIP_MODEL )); then
  say "Skipping the model download (--skip-model)"
elif model_staged; then
  say "The $SOURCE model is already staged"
  note "Re-run with ./scripts/prepare-model.sh --source $SOURCE to rebuild it."
else
  say "Downloading and staging the $SOURCE model"
  note "About 14 GB in, ~11 GB written out. Resumable — interrupt it and re-run to continue."
  "$ROOT/scripts/prepare-model.sh" --source "$SOURCE"
fi

# --------------------------------------------------------------------------- app bundle
# The app is Swift compiled by swiftc against the SDK in the Command Line Tools, so this needs no
# toolchain beyond what the steps above already used. A failure here is a warning rather than a
# stop: the runtime and the model are installed, and `make-swift-app.sh` can be re-run on its own.
if (( BUILD_NATIVE )); then
  if xcrun --sdk macosx --show-sdk-path >/dev/null 2>&1; then
    say "Building the native macOS app"
    "$ROOT/scripts/make-swift-app.sh" || warn "could not build it. Everything else is installed — run ./scripts/make-swift-app.sh once that is fixed."
  else
    say "Skipping the app build: no macOS SDK (install the Xcode Command Line Tools)"
  fi
fi

# --------------------------------------------------------------------------- summary
STAGED="$("$PY" -c 'from refract_backend.model_store import staged_pack_dir; print(staged_pack_dir() or "nothing staged yet")' 2>/dev/null || echo "nothing staged yet")"

cat <<TXT

Setup complete.

  Data root    $DATA_DIR
  Model        $STAGED

Run Refract Image:
  open "Refract Image.app"   the native app, once built (--native)
  ./scripts/dev.sh            the native app against vite, for UI work
  ./scripts/dev.sh --web      the UI in a browser, for development
  ./scripts/try-reference.py  one real reference edit from the terminal

Make a double-clickable app:
  ./scripts/make-swift-app.sh    builds ./Refract Image.app (a real macOS window)

The app already defaults to the Uncensored 4-bit model, so there is nothing to select before
the first generation.

Note: the bundle is ad-hoc signed for this machine and not notarised, so macOS may ask once
whether you are sure you want to open an app downloaded from the internet.
TXT
