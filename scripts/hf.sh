#!/usr/bin/env bash
# Run the Hugging Face CLI with this project's cache, e.g.
#
#     ./scripts/hf.sh download abenzerps/Qwen-Image-2.1-Uncensored-GGUF \
#         qwen-image-2.1-UC-MLX-4bit.safetensors
#     ./scripts/hf.sh scan-cache
#
# The plain `hf` command writes to ~/.cache/huggingface on the internal disk, which for
# this project means the weights land in the wrong place. This wrapper exports the
# same HF_HOME the app uses, so a manual download and an in-app download share one cache.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.runtime/bin/python"
# shellcheck source=scripts/_env.sh
source "$ROOT/scripts/_env.sh"

HF_CLI="${HF_CLI:-}"
if [[ -z "$HF_CLI" ]]; then
  for candidate in "$WORKSPACE/.runtime/bin/hf" "$(command -v hf 2>/dev/null || true)" "$(command -v huggingface-cli 2>/dev/null || true)"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then HF_CLI="$candidate"; break; fi
  done
fi
[[ -n "$HF_CLI" ]] || { echo "No huggingface-cli found. Install it, or use ./scripts/prepare-model.sh." >&2; exit 1; }

echo "HF_HOME=$HF_HOME" >&2
exec "$HF_CLI" "$@"
