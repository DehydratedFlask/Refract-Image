#!/usr/bin/env python3
"""Real end-to-end check: load the weights, edit from reference images, save a PNG.

    .runtime/bin/python scripts/smoke.py                  # 512px, 4 steps, mlx-q4 pack
    .runtime/bin/python scripts/smoke.py --steps 8 --resolution 768
    .runtime/bin/python scripts/smoke.py --dry-run        # staging and validation only

Unlike the test suite (which runs the placeholder runner), this exercises the actual MLX
path: mflux loads the quantised transformer and text encoder, encodes the references,
denoises, decodes the RGBA latent and writes a file. It is deliberately small — a short
run at 512px — because it exists to answer "does this work on this machine at all", and to
report peak memory while doing so.

The community 4-bit pack stores quantised weights only: it has no per-component
`config.json`, and mflux refuses to build the module tree without them. Those files are
architecture descriptions, a few kilobytes each, and the pack is a conversion of the same
upstream weights — so this script (like the app's Models pane) fetches them from the base
model and stages a complete local checkpoint.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

PACK_REPO = "abenzerps/Qwen-Image-2.1-Uncensored-GGUF"
BASE_REPO = "Qwen/Qwen-Image-2.1"
COMPONENTS = ("transformer", "text_encoder", "vae")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one real generation end to end")
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--resolution", type=int, default=512)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--prompt", default="recolour the shapes so the scene reads as dusk")
    parser.add_argument("--model", default=None, help="an existing model directory (skip staging)")
    parser.add_argument("--dry-run", action="store_true", help="stage and validate, do not load the model")
    args = parser.parse_args(argv)

    from refract_backend.paths import configure_environment, ensure_dirs, outputs_dir, storage_report
    from refract_backend import ingest, model_store, sysinfo

    # Before huggingface_hub (and therefore mflux) is imported, so every cache it opens is
    # the one under the app's data root.
    configure_environment()
    ensure_dirs()

    storage = storage_report()
    print(f"data root     {storage['data_root']}  ({'external' if storage['external'] else 'internal disk'})")
    print(f"mflux         {sysinfo.mflux_version()} @ {(sysinfo.mflux_commit() or '?')[:8]}")
    print(f"reference     {'available' if sysinfo.runner_available() else sysinfo.runner_unavailable_reason()}")

    if args.model:
        model_dir = Path(args.model).expanduser()
    else:
        print(f"\nstaging {PACK_REPO}\n         (+ 4-bit text encoder and configs from other repositories)")
        started = time.time()
        staged = model_store.stage_pack(progress=_say)
        model_dir = Path(staged["path"])
        print(
            f"staged in {time.time() - started:.1f}s: {model_dir}\n"
            f"  {staged['linked_files']} files linked ({staged['bytes_linked'] / 1e9:.2f} GB), "
            f"{staged['bits']}-bit group {staged['group_size']}, {staged['quantized_layers']} quantised layers"
        )
        for component in COMPONENTS:
            shards = sorted((model_dir / component).glob("*.safetensors"))
            size = sum(path.stat().st_size for path in shards)
            print(f"  {component:<14} {len(shards)} shard(s)  {size / 1e9:.2f} GB")

    if args.dry_run:
        # The honest check: mflux's own loader and quantiser against the staged directory.
        report = ingest.verify_loads(model_dir)
        print(json.dumps(report, indent=2)[:2000])
        return 0 if report.get("ok") else 1

    references = _write_references(model_dir.parent / "smoke-references")
    print("\nreferences   " + ", ".join(str(path) for path in references))

    from mflux.models.common.config import ModelConfig
    from mflux.models.qwen21.reference import QwenImage21Edit

    print("\nloading the model (this is the slow part)")
    started = time.time()
    model = QwenImage21Edit(
        quantize=None,
        model_path=str(model_dir),
        model_config=ModelConfig.qwen_image_21(),
    )
    print(f"loaded in {time.time() - started:.1f}s, peak MLX memory {sysinfo.peak_memory_gb():.2f} GB")

    out = outputs_dir() / "smoke"
    out.mkdir(parents=True, exist_ok=True)
    print(f"generating {args.resolution}px, {args.steps} steps, {len(references)} references")
    started = time.time()
    image = model.generate_image(
        seed=args.seed,
        prompt=args.prompt,
        num_inference_steps=args.steps,
        guidance=1.0,
        image_paths=[str(path) for path in references],
        output_resolution=args.resolution,
        use_kv_cache=True,
    )
    elapsed = time.time() - started
    target = out / f"smoke-{args.resolution}-{args.steps}steps.png"
    image.save(path=target)
    pil = image.image if hasattr(image, "image") else None
    print(
        f"done in {elapsed:.1f}s ({elapsed / max(args.steps, 1):.2f}s/step) · "
        f"peak MLX memory {sysinfo.peak_memory_gb():.2f} GB"
    )
    print(f"wrote {target} ({target.stat().st_size / 1e6:.2f} MB){f' {pil.size}' if pil else ''}")
    return 0


def _write_references(directory: Path) -> list[Path]:
    """Two simple, deterministic references: a bright square on one, a disc on the other."""
    from PIL import Image, ImageDraw

    directory.mkdir(parents=True, exist_ok=True)
    first = directory / "reference-square.png"
    second = directory / "reference-circle.png"
    if not first.exists():
        image = Image.new("RGB", (640, 480), (24, 28, 38))
        draw = ImageDraw.Draw(image)
        draw.rectangle((160, 120, 480, 360), fill=(230, 120, 60))
        image.save(first)
    if not second.exists():
        image = Image.new("RGB", (480, 640), (18, 22, 30))
        draw = ImageDraw.Draw(image)
        draw.ellipse((90, 170, 390, 470), fill=(80, 170, 240))
        image.save(second)
    return [first, second]


def _say(event: dict) -> None:
    message = event.get("message")
    if message:
        print(f"  {event.get('phase', ''):<12} {message}")


if __name__ == "__main__":
    raise SystemExit(main())
