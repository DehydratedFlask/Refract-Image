"""Stage FLUX.2 Klein 4B for mflux, with the ablated (uncensored) text encoder.

    python -m refract_backend.tools.prepare_klein                # stock Qwen3-4B encoder
    python -m refract_backend.tools.prepare_klein --uncensored   # ablated encoder
    python -m refract_backend.tools.prepare_klein --encoder stock|ablated

mflux loads `text_encoder/*.safetensors` and knows nothing about GGUF, so the ablated
encoder — published only as a llama.cpp `Q4_0` GGUF — is dequantised to bf16 and renamed
into mflux's key layout here. It stays bf16 because Q4_0 is a llama.cpp scheme with no MLX
affine equivalent; the staged pack is therefore larger than the stock one. The point is that
the ablated weights are reachable, not that they are small.

Conversion is a pure rename, not a reshape: llama.cpp stores `[in, out]` and gguf's
`dequantize` already returns HF's `[out, in]`, which the stock shards confirm. Every
produced key is checked against the stock `model.safetensors.index.json` before anything is
written, so a mismatch is a refusal rather than a model that fails at generation time.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

from ..paths import configure_environment, data_root, models_dir

KLEIN_REPO = "black-forest-labs/FLUX.2-klein-4B"
KLEIN_REVISION = "e7b7dc27f91deacad38e78976d1f2b499d76a294"
STAGED_NAME = "flux2-klein-4b"

BASE_PATTERNS = [
    "model_index.json",
    "transformer/*",
    "vae/*",
    "text_encoder/config.json",
    "text_encoder/generation_config.json",
    "text_encoder/*.safetensors",
    "text_encoder/model.safetensors.index.json",
    "tokenizer/*",
]

ENCODERS: dict[str, dict[str, Any]] = {
    "stock": {
        "label": "Stock Qwen3-4B (as shipped with FLUX.2 Klein)",
        "repo": None,
        "revision": None,
        "filename": None,
        "note": None,
    },
    "ablated": {
        "label": "Ablated Qwen3-4B (Huihui abliteration, GGUF Q4_0)",
        "repo": "WeReCooking/flux2-klein-4B-uncensored-text-encoder",
        "revision": "56ef4c31cc05d8e09ee98e0e8f39bdd0c9b0dc8f",
        "filename": "qwen3-4b-abl-q4_0.gguf",
        # Per its own card: this removes prompt refusal but adds no visual knowledge.
        "note": "Prompt-filtering removed. Per the source card this adds no visual "
        "knowledge — it removes the refusal, not the capability.",
        "duplicate_of": "Cordux/flux2-klein-4B-uncensored-text-encoder",
    },
}

#: llama.cpp tensor name -> mflux/HF key suffix, inside `blk.N.`.
_LAYER_ALIASES = {
    "attn_q": "self_attn.q_proj",
    "attn_k": "self_attn.k_proj",
    "attn_v": "self_attn.v_proj",
    "attn_output": "self_attn.o_proj",
    "attn_q_norm": "self_attn.q_norm",
    "attn_k_norm": "self_attn.k_norm",
    "attn_norm": "input_layernorm",
    "ffn_norm": "post_attention_layernorm",
    "ffn_gate": "mlp.gate_proj",
    "ffn_up": "mlp.up_proj",
    "ffn_down": "mlp.down_proj",
}

#: Ties the vocab: the causal LM's output projection is the input embedding.
_DROPPED = ("lm_head", "rope_freqs")


def say(message: str) -> None:
    print(f"  {message}", flush=True)


#: Set by :func:`stage_klein` so the module's own progress lines reach the job queue instead of
#: only the terminal. Staged conversion takes minutes; a UI that shows nothing for that long
#: reads as a hang.
_REPORT: Callable[[str], None] | None = None


def _report(message: str) -> None:
    if _REPORT is not None:
        _REPORT(message)


def _download_base() -> Path:
    from huggingface_hub import snapshot_download

    cache = data_root() / "hf-flux2-klein-4b"
    say(f"Fetching {KLEIN_REPO}@{KLEIN_REVISION[:8]}")
    _report(f"fetching {KLEIN_REPO}")
    return Path(
        snapshot_download(
            KLEIN_REPO,
            revision=KLEIN_REVISION,
            local_dir=str(cache),
            allow_patterns=BASE_PATTERNS,
        )
    )


def _download_gguf(spec: dict[str, Any]) -> Path:
    from huggingface_hub import hf_hub_download

    say(f"Fetching {spec['repo']}@{spec['revision'][:8]}")
    _report(f"fetching {spec['repo']}")
    return Path(
        hf_hub_download(
            spec["repo"],
            spec["filename"],
            revision=spec["revision"],
            local_dir=str(data_root() / "hf-gguf"),
        )
    )


def _gguf_to_mflux_keys(name: str) -> str | None:
    """`blk.7.attn_k.weight` -> `model.layers.7.self_attn.k_proj.weight`."""
    if any(dropped in name for dropped in _DROPPED):
        return None
    if name == "token_embd.weight":
        return "model.embed_tokens.weight"
    if name == "output_norm.weight":
        return "model.norm.weight"
    if not name.startswith("blk."):
        return None
    parts = name.split(".")
    if len(parts) != 4 or parts[2:] != ["", "weight"] and not parts[2].endswith("weight"):
        # blk.N.<leaf>.weight is the only layer shape; anything else is unfamiliar.
        if len(parts) != 4:
            return None
    leaf = parts[2].removesuffix(".weight")
    alias = _LAYER_ALIASES.get(leaf)
    if alias is None:
        return None
    return f"model.layers.{parts[1]}.{alias}.weight"


def convert_encoder(gguf_path: Path, expected_keys: set[str], out_dir: Path) -> dict[str, Any]:
    """Dequantise the GGUF encoder to bf16 safetensors under mflux's key layout."""
    import mlx.core as mx
    import numpy as np
    from gguf import GGUFReader
    from gguf.constants import GGMLQuantizationType
    from gguf.quants import dequantize

    reader = GGUFReader(str(gguf_path))
    say(f"GGUF holds {len(reader.tensors)} tensors")

    converted: dict[str, Any] = {}
    unmapped: list[str] = []
    for tensor in reader.tensors:
        key = _gguf_to_mflux_keys(tensor.name)
        if key is None:
            unmapped.append(tensor.name)
            continue
        values = dequantize(tensor.data, GGMLQuantizationType(tensor.tensor_type))
        array = mx.array(np.ascontiguousarray(values)).astype(mx.bfloat16)
        converted[key] = array
        mx.eval(array)

    say(f"converted {len(converted)} tensors to bf16")

    # A renamed-but-wrong conversion is the failure mode that only shows up mid-generation,
    # so the produced key set is compared against the stock shards before anything is saved.
    produced = set(converted)
    missing = expected_keys - produced
    extra = produced - expected_keys
    if missing or extra:
        for name in sorted(missing)[:6]:
            print(f"missing from the GGUF: {name}", file=sys.stderr)
        for name in sorted(extra)[:6]:
            print(f"not in the stock encoder: {name}", file=sys.stderr)
        print(
            f"Refusing to write: {len(missing)} missing, {len(extra)} unexpected keys.",
            file=sys.stderr,
        )
        return {"ok": False, "converted": len(converted), "missing": len(missing), "extra": len(extra)}

    if unmapped:
        say(f"dropped {len(unmapped)} GGUF tensors with no mflux equivalent: {unmapped[:4]}")

    out_dir.mkdir(parents=True, exist_ok=True)
    mx.save_safetensors(str(out_dir / "model.safetensors"), converted, metadata={"format": "pt"})
    return {"ok": True, "converted": len(converted), "missing": 0, "extra": 0, "dropped": len(unmapped)}


def _stock_encoder_keys(base: Path) -> set[str]:
    index = json.loads((base / "text_encoder" / "model.safetensors.index.json").read_text())
    return set(index["weight_map"])


def stage_klein(encoder: str = "ablated", progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Stage FLUX.2 Klein 4B into a directory mflux loads directly.

    Split out of :func:`main` so the app can run exactly the same work as a cancellable,
    progress-reporting job rather than a terminal command a new user cannot reach.
    """
    global _REPORT
    _REPORT = progress

    spec = ENCODERS.get(encoder)
    if spec is None:
        raise ValueError(f"unknown encoder {encoder!r}; known: {', '.join(ENCODERS)}")

    started = time.time()
    base = _download_base()
    expected = _stock_encoder_keys(base)
    say(f"stock encoder declares {len(expected)} tensors")
    _report(f"copying {STAGED_NAME} into place")

    target = models_dir() / STAGED_NAME
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for item in ("transformer", "vae", "tokenizer"):
        shutil.copytree(base / item, target / item)
    shutil.copy2(base / "model_index.json", target / "model_index.json")
    (target / "text_encoder").mkdir()
    shutil.copy2(base / "text_encoder" / "config.json", target / "text_encoder" / "config.json")
    if (base / "text_encoder" / "generation_config.json").exists():
        shutil.copy2(base / "text_encoder" / "generation_config.json", target / "text_encoder" / "generation_config.json")

    if spec["repo"] is None:
        for shard in sorted((base / "text_encoder").glob("*.safetensors")):
            shutil.copy2(shard, target / "text_encoder" / shard.name)
        say("kept the stock text encoder (bf16, 2 shards)")
        _report("kept the stock text encoder")
    else:
        _report("converting the ablated encoder to bf16 (a few minutes)")
        report = convert_encoder(_download_gguf(spec), expected, target / "text_encoder")
        if not report.get("ok"):
            shutil.rmtree(target, ignore_errors=True)
            raise RuntimeError("the ablated encoder did not convert; nothing was staged")
        say(f"ablated encoder written: {report['converted']} bf16 tensors")
        _report(f"ablated encoder written ({report['converted']} tensors)")
        (target / "text_encoder" / "encoder-origin.json").write_text(
            json.dumps({k: v for k, v in spec.items() if v is not None}, indent=2)
        )

    manifest = {
        "name": STAGED_NAME,
        "family": "flux2",
        "base_repo": KLEIN_REPO,
        "base_revision": KLEIN_REVISION,
        "encoder": encoder,
        "encoder_label": spec["label"],
        "encoder_note": spec["note"],
        "encoder_duplicate_of": spec.get("duplicate_of"),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_s": round(time.time() - started, 1),
    }
    (target / "refract-manifest.json").write_text(json.dumps(manifest, indent=2))
    say(f"staged {target} in {manifest['elapsed_s']}s")

    from .paths import dir_size_bytes

    return {
        "prepared": True,
        "path": str(target),
        "name": STAGED_NAME,
        "size_bytes": dir_size_bytes(target),
        "encoder": encoder,
        "encoder_label": spec["label"],
        "manifest": manifest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--uncensored", action="store_true", help="use the ablated GGUF encoder")
    group.add_argument("--encoder", help="encoder key: " + ", ".join(ENCODERS))
    args = parser.parse_args(argv)

    configure_environment()
    key = args.encoder or ("ablated" if args.uncensored else "stock")

    try:
        outcome = stage_klein(key)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Staged {outcome['path']} with the {outcome['encoder_label']} in {outcome['manifest']['elapsed_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
