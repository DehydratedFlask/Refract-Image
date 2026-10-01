"""A stand-in runner for development and for machines without the MLX stack.

It is deliberately obvious that it is a stand-in: `/api/health` reports `mock: true` and
the app shows a banner. What it *is* good for is exercising everything around the model —
the reference strip, job queue, progress events, previews, comparison view and Library —
without a 9-33 GB download, and keeping the app usable if mflux is missing.
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any, Callable

from . import sysinfo
from .paths import prepare_output_dir
from .runner import reference_output_size, slugify

ReporterFn = Callable[[dict[str, Any]], None]


class MockRunner:
    def __init__(self, step_seconds: float = 0.08) -> None:
        self.step_seconds = step_seconds

    def build_model(self, model_path=None, quantize=None, source_id=None, family="qwen21"):
        return object(), quantize

    def release(self, model=None) -> None:
        return None

    def generate(
        self,
        request,
        reporter: ReporterFn,
        cancel_event,
        job_dir: Path | None = None,
    ) -> dict[str, Any]:
        from PIL import Image, ImageDraw

        target_dir = prepare_output_dir(request.output_dir)
        started = time.time()
        reporter({"phase": "loading", "message": "mock runner: pretending to load weights"})
        time.sleep(0.2)
        reporter(
            {
                "phase": "encoding",
                "message": (
                    f"mock conditioning on {len(request.reference_paths)} reference image(s)"
                    if request.reference_paths
                    else "mock text-to-image"
                ),
            }
        )
        time.sleep(0.2)

        total = request.steps
        for step in range(1, total + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError(f"cancelled at step {step}")
            time.sleep(self.step_seconds)
            elapsed = time.time() - started
            event: dict[str, Any] = {
                "phase": "denoise",
                "step": step,
                "total_steps": total,
                "elapsed_seconds": round(elapsed, 2),
                "seconds_per_step": round(elapsed / step, 3),
                "eta_seconds": round((elapsed / step) * (total - step), 1),
                "peak_memory_gb": round(0.4 + step * 0.01, 2),
            }
            if request.preview_interval and job_dir and (
                step % max(1, request.preview_interval) == 0 or step == total
            ):
                path = job_dir / f"preview_{step:03d}.png"
                self._render(request, step / max(1, total), (512, 512)).save(path)
                for stale in job_dir.glob("preview_*.png"):
                    if stale != path:
                        stale.unlink(missing_ok=True)
                event["preview_path"] = str(path)
            reporter(event)

        reporter({"phase": "decoding", "message": "mock decode"})
        seed = request.seed if request.seed is not None and request.seed >= 0 else random.randint(0, 2**31)
        size = reference_output_size(request) or (request.width or request.output_resolution, request.height or request.output_resolution)
        image = self._render(request, 1.0, size)
        # Same policy as the real runner, including failing before the "load" when the
        # chosen folder cannot be written to.
        target_dir = prepare_output_dir(request.output_dir)
        path = target_dir / f"{request.output_name or slugify(request.prompt)}-mock-s{seed}.png"
        counter = 2
        while path.exists():
            path = target_dir / f"{path.stem}-{counter}.png"
            counter += 1
        image.save(path)
        reporter({"phase": "saving", "message": "mock wrote the image"})

        # Keep the API contract identical to the real runner so the UI never branches.
        sysinfo.clear_cache()
        return {
            "outputs": [str(path)],
            "seeds": [seed],
            "width": size[0],
            "height": size[1],
            "duration_s": round(time.time() - started, 2),
            "peak_memory_gb": 0.8,
            "bits": None,
            "model_source": request.model_source,
            "model_path": request.model_path,
            "mock": True,
        }

    @staticmethod
    def _render(request, progress: float, size: tuple[int, int]):
        from PIL import Image, ImageDraw

        width, height = map(int, size)
        image = Image.new("RGB", (width, height), (18, 18, 20))
        draw = ImageDraw.Draw(image)
        for y in range(height):
            blend = y / height
            draw.line(
                [(0, y), (width, y)],
                fill=(
                    int(24 + 60 * blend * progress),
                    int(28 + 40 * blend),
                    int(46 + 120 * progress),
                ),
            )
        # Reference thumbnails, so the reference/result comparison is exercisable.
        x = 12
        for raw in list(request.reference_paths)[:4]:
            try:
                thumb = Image.open(raw).convert("RGB")
            except OSError:
                continue
            thumb.thumbnail((96, 96))
            image.paste(thumb, (x, 12))
            x += thumb.width + 8
        draw.text((12, height - 84), "MOCK RUNNER", fill=(255, 200, 120))
        draw.text((12, height - 64), (request.prompt[:64] or ""), fill=(240, 240, 245))
        draw.text(
            (12, height - 44),
            f"{request.steps} steps · seed {request.seed} · {(request.width or 0)}x{(request.height or 0)}",
            fill=(170, 170, 180),
        )
        return image
