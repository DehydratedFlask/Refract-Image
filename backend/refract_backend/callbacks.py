"""The bridge between mflux's denoise loop and a long-lived server process.

mflux ships a `MemorySaver` that drops the text encoder after the first generation, which
is right for a one-shot CLI and wrong here: this service is warm across jobs, and
re-loading 17.5 GB of text encoder between every prompt would dominate latency. So this
module reproduces the loop-time reporting mflux does (step, elapsed, peak memory, decoded
previews) and the parts of memory hygiene that are safe to keep (cache eviction between
jobs happens in the runner), while deliberately *not* evicting the encoder.

Cancellation rides on the same hook mflux uses for Ctrl-C: raising
`StopImageGenerationException` from inside the loop unwinds cleanly through the model's
`generate_image` call.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from mflux.utils.exceptions import StopImageGenerationException

EventSink = Callable[[dict[str, Any]], None]


class ProgressCallback:
    """Reports denoise progress and decodes cheap periodic previews.

    Implements mflux's BeforeLoop/InLoop/AfterLoop/Interrupt protocol. Every optional
    failure (a preview decode, a memory probe) is swallowed: progress reporting must never
    be the reason a generation fails.
    """

    def __init__(
        self,
        model: Any,
        report: EventSink,
        cancel_event: Any,
        latent_creator: Any | None = None,
        preview_dir: Path | None = None,
        preview_interval: int = 0,
    ) -> None:
        self.model = model
        self.report = report
        self.cancel_event = cancel_event
        self.latent_creator = latent_creator
        self.preview_dir = preview_dir
        self.preview_interval = max(0, preview_interval)
        self.started_at: float | None = None
        self.last_preview_step = -1

    # ---- mflux callback protocol -----------------------------------------
    def call_before_loop(self, seed: int, prompt: str, latents, config, **kwargs) -> None:
        self.started_at = time.time()
        self.report(
            {
                "phase": "denoise",
                "step": 0,
                "total_steps": int(getattr(config, "num_inference_steps", 0) or 0),
                "message": "denoising",
            }
        )

    def call_in_loop(self, t: int, seed: int, prompt: str, latents, config, time_steps=None, **kwargs) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise StopImageGenerationException(f"Cancelled at step {t + 1}")

        total = int(getattr(config, "num_inference_steps", 0) or 0)
        step = t + 1
        elapsed = time.time() - self.started_at if self.started_at else 0.0
        per_step = elapsed / step if step else None
        eta = per_step * (total - step) if per_step is not None else None

        from . import sysinfo

        event: dict[str, Any] = {
            "phase": "denoise",
            "step": step,
            "total_steps": total,
            "elapsed_seconds": round(elapsed, 2),
            "seconds_per_step": round(per_step, 2) if per_step else None,
            "eta_seconds": round(eta, 1) if eta else None,
            "peak_memory_gb": sysinfo.peak_memory_gb(),
        }
        preview = self._maybe_preview(step, total, seed, prompt, latents, config, elapsed)
        if preview:
            event["preview_path"] = preview
        self.report(event)

    def call_after_loop(self, seed: int, prompt: str, latents, config) -> None:
        self.report({"phase": "decoding", "message": "decoding latents"})

    def call_interrupt(self, t: int, seed: int, prompt: str, latents, config, time_steps=None) -> None:
        self.report({"phase": "cancelled", "message": f"interrupted at step {t + 1}"})

    # ---- helpers ----------------------------------------------------------
    def _maybe_preview(self, step: int, total: int, seed: int, prompt: str, latents, config, elapsed: float):
        """Decode the latents every `preview_interval` steps.

        A VAE decode is not free (roughly a second at 1024px), so this is opt-in on a
        stride and always off when the caller passes 0.
        """
        if not self.preview_interval or self.preview_dir is None or self.latent_creator is None:
            return None
        due = step % self.preview_interval == 0 or step in (1, total)
        if not due or step == self.last_preview_step:
            return None
        self.last_preview_step = step
        try:
            from mflux.utils.image_util import ImageUtil

            unpacked = self.latent_creator.unpack_latents(
                latents=latents, height=config.height, width=config.width
            )
            vae = self.model.vae
            channels = getattr(vae, "latent_channels", 32)
            if hasattr(vae, "decode_packed_latents") and unpacked.shape[1] > channels:
                decoded = vae.decode_packed_latents(unpacked)
            else:
                decoded = vae.decode(unpacked)
            image = ImageUtil.to_image(
                decoded_latents=decoded,
                config=config,
                seed=seed,
                prompt=prompt,
                quantization=getattr(self.model, "bits", None) or 0,
                generation_time=elapsed,
            )
            target = self.preview_dir / f"preview_{step:03d}.png"
            image.save(path=target, export_json_metadata=False, overwrite=True)
            for stale in self.preview_dir.glob("preview_*.png"):
                if stale.name != target.name:
                    stale.unlink(missing_ok=True)
            return str(target)
        except Exception as exc:  # pragma: no cover - depends on live model internals
            self.report({"message": f"preview unavailable: {exc}"})
            return None
