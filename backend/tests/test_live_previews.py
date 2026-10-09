from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import mlx.core as mx
import pytest
from PIL import Image

from refract_backend.callbacks import ProgressCallback
from refract_backend.jobs import Job
from refract_backend.runner import _preview_latent_creator
from refract_backend.schemas import GenerateRequest


@pytest.mark.parametrize("family,shape", [("qwen21", (1, 64, 1, 2, 2)), ("flux2", (1, 128, 2, 2))])
def test_preview_uses_the_correct_model_latent_layout(family, shape, tmp_path, monkeypatch):
    from mflux.utils.image_util import ImageUtil

    creator = _preview_latent_creator(family)
    channels = 64 if family == "qwen21" else 128
    latents = mx.zeros((1, 4, channels))
    vae = SimpleNamespace(latent_channels=32)
    seen = []

    def decode(unpacked, **kwargs):
        seen.append((unpacked.shape, kwargs))
        assert unpacked.shape == shape
        return mx.zeros((1, 3, 32, 32))

    if family == "flux2":
        vae.decode_packed_latents = decode
    else:
        vae.decode = decode
    model = SimpleNamespace(vae=vae, bits=4, tiling_config=None)

    class PreviewImage:
        def save(self, path, **kwargs):
            Image.new("RGB", (32, 32), "red").save(path)

    monkeypatch.setattr(ImageUtil, "to_image", lambda **kwargs: PreviewImage())
    events = []
    callback = ProgressCallback(model, events.append, threading.Event(), creator, tmp_path, 1)
    config = SimpleNamespace(num_inference_steps=5, height=32, width=32)
    callback.call_before_loop(1, "a vase", latents, config)
    for t in range(5):
        callback.call_in_loop(t, 1, "a vase", latents, config)
        progress, frame = events[-2:]
        assert progress["step"] == t + 1
        assert "preview_path" not in progress  # progress is not held up by VAE decoding
        assert frame["step"] == t + 1
        with Image.open(frame["preview_path"]) as image:
            assert image.size == (32, 32)
    assert len(seen) == 5
    assert len(list(tmp_path.glob("preview_*.png"))) == 3
    assert events[-1]["eta_seconds"] == 0
    if family == "flux2":
        assert all(kwargs == {"tiling_config": None} for _, kwargs in seen)
    callback.call_after_loop(1, "a vase", latents, config)
    assert events[-1]["eta_seconds"] is None


def test_preview_work_is_included_in_remaining_time(tmp_path, monkeypatch):
    events = []
    callback = ProgressCallback(object(), events.append, threading.Event(), preview_interval=1)
    clock = [100.0]
    monkeypatch.setattr("refract_backend.callbacks.time.time", lambda: clock[0])
    config = SimpleNamespace(num_inference_steps=4)
    callback.call_before_loop(1, "a vase", None, config)
    clock[0] += 2

    def preview(*args):
        clock[0] += 1  # VAE decode work
        return "/preview.png"

    monkeypatch.setattr(callback, "_maybe_preview", preview)
    callback.call_in_loop(0, 1, "a vase", None, config)
    assert events[-1]["seconds_per_step"] == 3
    assert events[-1]["eta_seconds"] == 9


def test_preview_disable_and_stride_are_honoured(tmp_path):
    creator = Mock()
    model = Mock()
    callback = ProgressCallback(model, Mock(), threading.Event(), creator, tmp_path, 0)
    assert callback._maybe_preview(1, 5, 1, "vase", None, None, 1) is None
    creator.unpack_latents.assert_not_called()
    callback.preview_interval = 3
    assert callback._maybe_preview(2, 5, 1, "vase", None, None, 1) is None
    creator.unpack_latents.assert_not_called()


def test_cancellation_prevents_expensive_preview_decode():
    from mflux.utils.exceptions import StopImageGenerationException

    cancel = threading.Event()
    cancel.set()
    callback = ProgressCallback(object(), Mock(), cancel)
    with pytest.raises(StopImageGenerationException):
        callback.call_in_loop(0, 1, "a vase", None, SimpleNamespace(num_inference_steps=5))


def test_decoding_clears_stale_eta_in_job_snapshot():
    job = Job(id="test", kind="generate", payload={})
    job.emit({"phase": "denoise", "eta_seconds": 30})
    job.emit({"phase": "decoding", "eta_seconds": None})
    assert job.to_dict()["eta_seconds"] is None


def test_job_events_carry_start_time_for_live_native_elapsed_clock():
    job = Job(id="test", kind="generate", payload={}, status="running", started_at=123.5)
    job.emit({"phase": "resolving"})
    assert job.events[-1]["started_at"] == 123.5
    job.emit({"phase": "denoise", "step": 1})
    assert job.events[-1]["started_at"] == 123.5


def test_last_preview_remains_readable_after_completion(client, monkeypatch):
    import time
    from pathlib import Path
    from refract_backend import jobs

    scheduled = []

    class DeferredCleanup:
        def __init__(self, delay, function, args=(), kwargs=None):
            self.delay, self.function, self.args, self.kwargs = delay, function, args, kwargs or {}
            self.daemon = False
            scheduled.append(self)

        def start(self):
            pass  # explicitly exercised below, without waiting for wall-clock timers

    monkeypatch.setattr(jobs.threading, "Timer", DeferredCleanup)
    response = client.post("/api/jobs", json={"kind": "generate", "payload": {"prompt": "a vase", "steps": 1, "preview_interval": 1}})
    assert response.status_code == 202
    job = response.json()
    deadline = time.monotonic() + 10
    while job["status"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.05)
        job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "done"
    preview = Path(job["preview_path"])
    assert preview.is_file()
    with Image.open(preview) as image:
        assert image.size == (512, 512)
    served = client.get("/api/files", params={"path": str(preview), "token": "test-token"})
    assert served.status_code == 200
    assert len(scheduled) == 1 and scheduled[0].delay == 5
    scheduled[0].function(*scheduled[0].args, **scheduled[0].kwargs)
    assert not preview.parent.exists()


def test_new_generations_default_to_live_per_step_previews():
    assert GenerateRequest(prompt="a vase").preview_interval == 1
