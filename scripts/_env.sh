#!/usr/bin/env bash
# Shared environment for the Refract scripts. Source it, do not run it:
#
#     ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
#     source "$ROOT/scripts/_env.sh"
#
# Two jobs:
#
# 1. Point every cache this project creates (uv wheels, npm tarballs, Hugging Face downloads)
#    at the checkout's own volume. The app downloads around 9 GB of
#    weights before it can generate anything, so on a machine where the project lives on an
#    external drive none of that should land on the internal disk by accident.
# 2. Set REFRACT_ROOT, the single directory the backend and the app keep their data in. It
#    is *asked of the backend* (`refract_backend.paths.data_root`) rather than recomputed
#    here, so the shell, the service and the native app can never disagree about it. That
#    function prefers `.refract` beside an external checkout and falls back to
#    ~/Library/Application Support/Refract; REFRACT_ROOT overrides it.
#
# Anything already set in the environment wins, so a caller can redirect a single cache
# without editing this file.

: "${ROOT:?source scripts/_env.sh after setting ROOT to the checkout path}"
PY="${PY:-$ROOT/.runtime/bin/python}"

export REFRACT_REPO_ROOT="${REFRACT_REPO_ROOT:-$ROOT}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT/.cache/uv}"
export npm_config_cache="${npm_config_cache:-$ROOT/.cache/npm}"

if [[ -x "$PY" ]]; then
  # Two lines, read positionally: paths may contain spaces (this checkout does).
  refract_paths="$("$PY" -c 'from refract_backend.paths import data_root, hf_home; print(data_root()); print(hf_home())' 2>/dev/null || true)"
  if [[ -n "$refract_paths" ]]; then
    export REFRACT_ROOT="${REFRACT_ROOT:-$(printf '%s\n' "$refract_paths" | sed -n 1p)}"
    export HF_HOME="${HF_HOME:-$(printf '%s\n' "$refract_paths" | sed -n 2p)}"
  fi
fi
