"""The mflux driver.

Everything model-specific lives here. The service keeps one warm model instance because
loading 4-18 GB of weights per request would dominate latency, so this module owns:

* resolving a *source id* into (weights path, quantisation) for mflux,
* the memory policy — warm (default: text encoder stays resident, MLX cache is dropped
  between jobs) versus low-memory (mflux's `MemorySaver` evicts the encoder after each
  encode and the model is released after the job),
* registering a fresh progress/cancel callback per job (callbacks accumulate on a reused
  model otherwise, so each job starts from a clean registry),
* naming and saving outputs with their metadata sidecar.
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import sysinfo
from .callbacks import ProgressCallback
from .model_store import SOURCES, installed_models, models_dir, pack_snapshot, stage_pack, staged_pack_dir
# OutputDirError is re-exported here: a run failing because of its destination is part of
# this module's contract, even though the check itself lives with the other path rules.
from .paths import OutputDirError, prepare_output_dir
from .schemas import GenerateRequest

ReporterFn = Callable[[dict[str, Any]], None]


KLEIN_STAGED_NAME = "flux2-klein-4b"

#: FLUX.2 Klein is distilled for few-step sampling. Refract's UI defaults to 40 steps for
#: Qwen-Image, and a stale value is clamped rather than silently costing 10x the time.
_FLUX2_MAX_STEPS = 12


class ModelNotReady(RuntimeError):
    """Raised when a source is selected but its weights are not on disk yet."""


@dataclass
class ResolvedModel:
    source_id: str
    label: str
    path: str | None
    quantize: int | None
    notes: list[str] = field(default_factory=list)
    family: str = "qwen21"

    @property
    def supports_negative_prompt(self) -> bool:
        spec = SOURCES.get(self.source_id)
        return True if spec is None else spec.supports_negative_prompt

    @property
    def default_steps(self) -> int:
        spec = SOURCES.get(self.source_id)
        return 40 if spec is None else spec.default_steps

    @property
    def is_repo(self) -> bool:
        return self.path is not None and self.path.count("/") == 1 and not Path(self.path).exists()


def resolve_model(
    source_id: str,
    model_path: str | None = None,
    progress: ReporterFn | None = None,
) -> ResolvedModel:
    spec = SOURCES.get(source_id)
    if spec is None:
        raise ModelNotReady(f"unknown model source {source_id!r}")

    if source_id == "mlx-q4":
        # The 4-bit transformer ships as one bare safetensors file and the text encoder is
        # missing its visual tower, so mflux cannot load the downloads as they are: a complete
        # checkpoint is staged once (relabel, graft, repair, verify) and every later run loads
        # it straight from disk.
        staged = staged_pack_dir()
        if staged is None:
            if not pack_snapshot():
                raise ModelNotReady(
                    "The Uncensored 4-bit model is not on disk yet. Download it from Models, "
                    "or switch the source to the upstream checkpoint."
                )
            manifest = stage_pack(progress=progress)
            staged = Path(manifest["path"])
        return ResolvedModel(
            source_id,
            f"{spec.label} ({staged.name})",
            str(staged),
            None,
            list(spec.notes),
        )
    if source_id == "upstream-q4":
        return ResolvedModel(source_id, spec.label, spec.repo_id, 4, list(spec.notes))

    if source_id == "flux2-klein-4b":
        # Flux2 shares no component with Qwen-Image: its own transformer, VAE, tokenizer and
        # Qwen3-4B text encoder, staged into a directory mflux loads directly.
        staged = models_dir() / KLEIN_STAGED_NAME
        if not staged.is_dir():
            raise ModelNotReady(
                "FLUX.2 Klein 4B is not staged yet. Run prepare_klein, or pick another source."
            )
        manifest = {}
        manifest_path = staged / "refract-manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
            except (OSError, ValueError):
                manifest = {}
        return ResolvedModel(
            source_id,
            f"{spec.label} ({staged.name})",
            str(staged),
            None,
            list(spec.notes),
            family="flux2",
        )

    if source_id == "custom":
        path = model_path or _first_installed(customised=True)
        if not path:
            raise ModelNotReady(
                "No custom model directory yet. Build one in Models > Install custom weights, "
                "which takes a base model and swaps in a validated third-party transformer pack."
            )
        return ResolvedModel(source_id, f"Custom weights ({Path(path).name})", path, None, list(spec.notes))

    path = model_path or _first_installed(customised=False)
    if not path:
        raise ModelNotReady(
            "No prepared model yet. Open Models and choose Prepare, or switch the source to "
            "the Uncensored 4-bit model (download it, then prepare it)."
        )
    # A prepared directory carries its quantisation level in its own metadata, so mflux
    # reads it back rather than re-quantising; pass None and let stored metadata win.
    return ResolvedModel("prepared", f"Prepared model ({Path(path).name})", path, None, list(spec.notes))


def _first_installed(customised: bool) -> str | None:
    for item in installed_models():
        if bool(item["customised"]) == customised:
            return item["path"]
    return None


def slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return (slug[:limit].rstrip("-")) or "image"


def reference_output_size(request: GenerateRequest) -> tuple[int, int] | None:
    """Original, EXIF-oriented pixels only when one reference and no explicit size."""
    if not request.match_reference_size or len(request.reference_paths) != 1:
        return None
    if request.width is not None or request.height is not None:
        return None
    from PIL import Image, ImageOps

    with Image.open(request.reference_paths[0]) as image:
        return ImageOps.exif_transpose(image).size


def inference_size(size: tuple[int, int]) -> tuple[int, int]:
    """Packable dimensions bounded by the supported 2048px inference budget."""
    scale = min(1.0, 2048 / max(size))
    return tuple(max(256, min(2048, int(round(value * scale / 32)) * 32)) for value in size)


class MfluxRunner:
    """Owns the warm model instance and runs one generation at a time."""

    def __init__(self) -> None:
        self._model: Any | None = None
        self._key: tuple[str | None, int | None, str] | None = None

    # ---- model lifecycle --------------------------------------------------
    def build_model(
        self,
        model_path: str | None,
        quantize: int | None,
        source_id: str | None = None,
        family: str = "qwen21",
    ):
        # The family belongs in the key as well as the weights: Qwen-Image and Flux2 are
        # different classes over different files, so reusing one for the other would be
        # wrong rather than merely stale.
        key = (model_path, quantize, family)
        if self._model is not None and self._key == key:
            return self._model, getattr(self._model, "bits", None)

        self.release()
        model = self._construct(family, model_path, quantize)
        model.refract_source_id = source_id
        self._model = model
        self._key = key
        return model, getattr(model, "bits", None)

    @staticmethod
    def _construct(family: str, model_path, quantize):
        if family == "flux2":
            from mflux.models.common.config import ModelConfig
            from mflux.models.flux2.variants import Flux2Klein, Flux2KleinEdit

            config = ModelConfig.flux2_klein_4b()
            # FLUX.2's edit variant requires at least one reference image: with none it
            # concatenates a None latent and raises. The two classes share weights, so the
            # right one is chosen per run and the cache key already distinguishes them.
            return Flux2KleinEdit(
                quantize=quantize,
                model_path=model_path,
                model_config=config,
            )

        from mflux.models.common.config import ModelConfig
        from mflux.models.qwen21.reference import QwenImage21Edit

        return QwenImage21Edit(
            quantize=quantize,
            model_path=model_path,
            model_config=ModelConfig.qwen_image_21(),
        )

    def release(self, model: Any | None = None) -> None:
        if model is None or model is self._model:
            self._model = None
            self._key = None
        sysinfo.clear_cache()

    # ---- generation -------------------------------------------------------
    def generate(
        self,
        request: GenerateRequest,
        reporter: ReporterFn,
        cancel_event,
        job_dir: Path | None = None,
    ) -> dict[str, Any]:
        from mflux.callbacks.callback_registry import CallbackRegistry
        from mflux.callbacks.instances.memory_saver import MemorySaver
        from mflux.models.common.vae.tiling_config import TilingConfig
        from mflux.models.qwen21.reference.latent_creator.qwen_image21_latent_creator import (
            QwenImage21LatentCreator,
        )

        # Resolved before anything expensive happens: a folder the user picked can be
        # missing, read-only or a file, and finding that out after a five-minute denoise
        # wastes the run.
        target_dir = prepare_output_dir(request.output_dir)
        original_size = reference_output_size(request)
        if original_size:
            width, height = inference_size(original_size)
            request = request.model_copy(update={"width": width, "height": height})
            reporter({"message": f"matching reference {original_size[0]}×{original_size[1]} (inference {width}×{height})"})

        resolved = resolve_model(request.model_source, request.model_path, reporter)
        reporter(
            {
                "phase": "loading",
                "message": f"loading {resolved.label}",
                "model_source": resolved.source_id,
                "model_path": resolved.path,
            }
        )
        for note in resolved.notes[:1]:
            reporter({"message": note})

        load_started = time.time()
        model, bits = self.build_model(resolved.path, resolved.quantize, resolved.source_id, resolved.family)
        reporter(
            {
                "phase": "loading",
                "message": f"model ready in {time.time() - load_started:.1f}s"
                + (f" ({bits}-bit)" if bits else ""),
            }
        )

        # Per-job callback registry: a reused model would otherwise accumulate callbacks.
        model.callbacks = CallbackRegistry()
        sysinfo.set_cache_limit_gb(request.mlx_cache_limit_gb)
        sysinfo.reset_peak_memory()

        if resolved.family == "flux2":
            # FLUX.2 trips the Metal watchdog when the first real graph of a run has to
            # compile and upload 8 GB of bf16 weights inside the same command buffer. Paying
            # that cost here, with nothing else queued, is what makes the run reliable.
            _settle_gpu(model, reporter)

        if request.vae_tiling:
            model.tiling_config = TilingConfig(vae_decode_tile_size=512)
        else:
            model.tiling_config = None

        if request.low_ram:
            # Evicts the text encoder after encoding and frees the transformer after the
            # loop; the model is dropped below so that is safe across jobs.
            model.callbacks.register(
                MemorySaver(
                    model=model,
                    keep_transformer=True,
                    cache_limit_bytes=int((request.mlx_cache_limit_gb or 1.0) * 1000**3),
                    num_seeds=1,
                )
            )

        progress = ProgressCallback(
            model=model,
            report=reporter,
            cancel_event=cancel_event,
            # Only Qwen-Image's latent creator can decode its own previews; FLUX.2 uses a
            # different latent layout, and guessing there would decode garbage or raise.
            latent_creator=None if resolved.family == "flux2" else QwenImage21LatentCreator,
            preview_dir=job_dir,
            preview_interval=request.preview_interval if resolved.family != "flux2" else 0,
        )
        model.callbacks.register(progress)

        references = [str(Path(p).expanduser()) for p in request.reference_paths]
        if references:
            reporter(
                {
                    "phase": "encoding",
                    "message": f"conditioning on {len(references)} reference image"
                    f"{'s' if len(references) != 1 else ''}",
                }
            )
        else:
            reporter({"phase": "encoding", "message": "text-to-image (no reference images)"})

        outputs: list[str] = []
        seeds: list[int] = []
        width = height = None
        started = time.time()

        try:
            for seed in request.seed_list():
                actual_seed = seed if seed is not None and seed >= 0 else random.randint(0, 2**31 - 1)
                seeds.append(actual_seed)
                reporter({"phase": "encoding", "message": f"seed {actual_seed}", "seed": actual_seed})
                if resolved.family == "flux2":
                    # FLUX.2's signature is narrower: no output_resolution, no
                    # negative_prompt, and the dimensions are plain width/height. It is also
                    # a distilled few-step model, so a leftover 40-step Qwen-Image setting
                    # would be 10x the work for no gain.
                    width = request.width or 1024
                    height = request.height or 1024
                    image = model.generate_image(
                        seed=actual_seed,
                        prompt=request.prompt,
                        num_inference_steps=min(request.steps, _FLUX2_MAX_STEPS),
                        width=width,
                        height=height,
                        guidance=request.guidance,
                        image_paths=references or None,
                    )
                else:
                    image = model.generate_image(
                        seed=actual_seed,
                        prompt=request.prompt,
                        num_inference_steps=request.steps,
                        width=request.width,
                        height=request.height,
                        guidance=request.guidance,
                        negative_prompt=request.negative_prompt or None,
                        image_paths=references or None,
                        output_resolution=request.output_resolution,
                        use_kv_cache=request.use_kv_cache,
                    )
                if original_size and image.image.size != original_size:
                    from PIL import Image
                    image.image = image.image.resize(original_size, Image.Resampling.LANCZOS)
                    image.width, image.height = original_size
                image.model_path = resolved.path
                reporter({"phase": "saving", "message": "writing the image to disk"})
                path = self._save(image, request, target_dir, actual_seed)
                outputs.append(str(path))
                size = _image_size(image)
                if size:
                    width, height = size
        finally:
            try:
                model.callbacks = CallbackRegistry()
            except Exception:
                pass
            if request.low_ram:
                self.release(model)
            else:
                sysinfo.clear_cache()

        return {
            "outputs": outputs,
            "seeds": seeds,
            "width": width,
            "height": height,
            "duration_s": round(time.time() - started, 2),
            "peak_memory_gb": sysinfo.peak_memory_gb(),
            "bits": bits,
            "model_source": resolved.source_id,
            "model_path": resolved.path,
        }

    # ---- outputs ----------------------------------------------------------
    @staticmethod
    def _save(image: Any, request: GenerateRequest, target_dir: Path, seed: int) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        core = request.output_name or f"{slugify(request.prompt)}-{stamp}-s{seed}"
        path = target_dir / f"{core}.png"
        counter = 2
        while path.exists():
            path = target_dir / f"{core}-{counter}.png"
            counter += 1
        image.save(path=path, export_json_metadata=request.save_metadata)
        return path


def _settle_gpu(model: Any, reporter: ReporterFn) -> None:
    """Move MLX's first-run compilation and weight upload off the denoise loop.

    A no-op on a warm model: evaluating already-resident arrays is cheap, so this costs a few
    milliseconds once and is skipped entirely on subsequent runs with the same weights.
    """
    import mlx.core as mx

    try:
        mx.eval(mx.zeros((8, 8)))
        for name in ("transformer", "text_encoder", "vae"):
            module = getattr(model, name, None)
            if module is not None:
                mx.eval(module.parameters())
        mx.clear_cache()
        reporter({"message": "weights resident on the GPU"})
    except Exception as exc:  # reported, never fatal: the run may still succeed
        reporter({"message": f"GPU warm-up skipped ({type(exc).__name__})"})


def _image_size(image: Any) -> tuple[int, int] | None:
    for attr in ("image", "pil_image"):
        candidate = getattr(image, attr, None)
        size = getattr(candidate, "size", None)
        if isinstance(size, tuple) and len(size) == 2:
            return int(size[0]), int(size[1])
    return None
