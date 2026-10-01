"""Run with python -m refract_backend.tools.prepare_encoder.

Only the text_encoder directory is replaced, after mflux validates the conversion.
The previous encoder remains alongside it as text_encoder.stock-backup.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .. import ingest, model_store
from ..paths import configure_environment, hf_cache_dir

HERETIC_REPO = "pottokao/Qwen-Image-2.1-Text-Encoder-Heretic"


def convert_encoder(source: Path, target: Path, config: Path, revision: str) -> dict:
    import mlx.core as mx

    # Conversion is a streaming CPU task, not inference. Avoid long Metal command
    # buffers starving the interactive app or triggering the GPU watchdog.
    mx.set_default_device(mx.cpu)
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config, target / "config.json")
    expected = ingest.expected_module_keys("text_encoder", ingest.component_config(target))
    index = json.loads((source / "model.safetensors.index.json").read_text())
    weight_map = {}
    seen = set()
    quantized = 0
    metadata = {
        "quantization_level": "4",
        "refract_reference_encoder": "true",
        "refract_encoder_source": HERETIC_REPO,
        "refract_encoder_revision": revision,
        "refract_encoder_abliterated": "true",
    }
    _, transform = ingest.text_encoder_transforms()
    for position, name in enumerate(sorted(set(index["weight_map"].values()))):
        print(f"Converting encoder shard {position + 1}: {name}", flush=True)
        data = mx.load(str(source / name))
        arrays = {}
        for raw, value in data.items():
            key = ingest.reference_text_encoder_key(raw, from_base=True)
            if key not in expected:
                continue
            value = transform(key, value)
            if tuple(value.shape) != expected[key]:
                raise ValueError(f"encoder tensor shape mismatch: {key}: {value.shape} != {expected[key]}")
            seen.add(key)
            # Keep vision dense, matching the existing reference pipeline. Quantize only
            # language-model linear/embedding matrices; norms stay bf16.
            if key.startswith("language_model.") and key.endswith(".weight") and value.ndim == 2 and value.shape[-1] % 64 == 0:
                weight, scales, biases = mx.quantize(value, group_size=64, bits=4)
                arrays[key] = weight
                arrays[key.removesuffix("weight") + "scales"] = scales
                arrays[key.removesuffix("weight") + "biases"] = biases
                mx.eval(weight, scales, biases)
                quantized += 1
            else:
                arrays[key] = value
        mx.eval(list(arrays.values()))
        shard = f"{position}.safetensors"
        weight_map.update(ingest._write_mflux_shards(target, {shard: arrays}, metadata))
        # _write_mflux_shards prunes other shards; keep earlier shards outside its glob
        # until all source shards have been converted.
        (target / shard).rename(target / f"{shard}.ready")
        del data, arrays
        mx.clear_cache()
    missing = sorted(key for key in expected if key not in seen and not key.endswith(".inv_freq"))
    if missing:
        raise ValueError(f"Heretic encoder is missing {len(missing)} tensors: {missing[:8]}")
    if not quantized:
        raise ValueError("No encoder matrices were quantized")
    for path in target.glob("*.safetensors.ready"):
        path.rename(path.with_suffix(""))
    ingest.write_json_atomic(target / "model.safetensors.index.json", {"metadata": metadata, "weight_map": weight_map})
    return {"repo_id": HERETIC_REPO, "revision": revision, "abliterated": True, "bits": 4, "quantized_layers": quantized}


def prepare_encoder() -> dict:
    configure_environment()
    from huggingface_hub import HfApi, snapshot_download

    print("Checking existing checkpoint and storage", flush=True)
    root = model_store.staged_pack_dir()
    if root is None:
        raise ValueError("Prepare the existing MLX checkpoint first")
    backup = root / "text_encoder.stock-backup"
    encoder = root / "text_encoder"
    manifest_path = root / "refract-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get("encoder_provenance") or {}).get("abliterated"):
        print("Heretic encoder already installed", flush=True)
        return manifest["encoder_provenance"]
    if backup.exists():
        raise ValueError(f"Existing backup needs review before installation: {backup}")
    if shutil.disk_usage(root).free < 24 * 1024**3:
        raise ValueError("At least 24 GiB free is required for the encoder download and conversion")
    print("Resolving pinned Heretic revision", flush=True)
    revision = HfApi().model_info(HERETIC_REPO, timeout=30).sha
    print(f"Downloading {HERETIC_REPO}@{revision} (HF shards only)", flush=True)
    source = Path(snapshot_download(HERETIC_REPO, revision=revision,
        allow_patterns=["model-*.safetensors", "model.safetensors.index.json", "config.json"], cache_dir=str(hf_cache_dir())))
    temporary = root / "text_encoder.heretic-staging"
    if temporary.exists():
        shutil.rmtree(temporary)
    provenance = convert_encoder(source, temporary, encoder / "config.json", revision)
    # Verification uses a small checkpoint wrapper to preserve the original encoder
    # until the exact same loader the real app uses has accepted the converted one.
    wrapper = root / "encoder-verification"
    wrapper.mkdir(exist_ok=True)
    (wrapper / "text_encoder").symlink_to(temporary, target_is_directory=True)
    try:
        report = ingest.analyze(temporary, component="text_encoder")
        if not report.get("ok"):
            raise ValueError(f"Converted encoder failed analysis: {report.get('errors')}")
        verification = ingest.verify_loads(wrapper, component="text_encoder")
        if not verification.get("ok"):
            raise ValueError(f"Converted encoder failed mflux load: {verification}")
        provenance["verification"] = verification
    finally:
        (wrapper / "text_encoder").unlink(missing_ok=True)
        wrapper.rmdir()
    encoder.rename(backup)
    try:
        temporary.rename(encoder)
    except Exception:
        backup.rename(encoder)
        raise
    manifest["encoder_provenance"] = provenance
    manifest["components"]["text_encoder"] = f"{HERETIC_REPO}@{revision} (Heretic abliterated, MLX 4-bit; dense vision)"
    manifest["verification"]["text_encoder"] = verification
    ingest.write_json_atomic(manifest_path, manifest)
    print(json.dumps(provenance, indent=2), flush=True)
    print("Encoder installed. Original transformer unchanged. Restart the app to load it.", flush=True)
    return provenance


if __name__ == "__main__":
    prepare_encoder()
