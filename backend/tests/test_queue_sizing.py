from __future__ import annotations

import time
from pathlib import Path

from PIL import Image

from refract_backend import sysinfo
from refract_backend.jobs import JobQueue
from refract_backend.library import Library
from refract_backend.schemas import GenerateRequest
from refract_backend.runner import reference_output_size, inference_size


def wait(job):
    deadline = time.monotonic() + 10
    while job.status not in ("done", "failed", "cancelled"):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    return job


def test_installed_memory_uses_macos_binary_units(monkeypatch):
    monkeypatch.setattr(sysinfo, "_sysctl", lambda name: str(48 * 1024**3))
    assert sysinfo.total_ram_gb() == 48


def test_portable_memory_fallback_uses_same_units(monkeypatch):
    monkeypatch.setattr(sysinfo, "_sysctl", lambda name: None)
    monkeypatch.setattr(sysinfo, "os_sysconf_pages", lambda: 48 * 1024**3 // 4096)
    monkeypatch.setattr(sysinfo, "os_page_size", lambda: 4096)
    assert sysinfo.total_ram_gb() == 48


def test_single_reference_original_pixels_and_explicit_override(tmp_path):
    reference = tmp_path / "ref.png"
    Image.new("RGB", (1001, 667)).save(reference)
    request = GenerateRequest(prompt="edit", reference_paths=[str(reference)])
    assert reference_output_size(request) == (1001, 667)
    assert inference_size((1001, 667)) == (992, 672)
    assert reference_output_size(request.model_copy(update={"width": 512})) is None
    assert reference_output_size(request.model_copy(update={"match_reference_size": False})) is None
    assert reference_output_size(request.model_copy(update={"reference_paths": [str(reference)] * 2})) is None


def test_queue_is_fifo_snapshots_references_and_cancels_pending(isolated_dirs, tmp_path):
    import threading

    gate = threading.Event()
    entered = threading.Event()
    prompts = []
    pixels = []

    class Runner:
        def generate(self, request, **kwargs):
            prompts.append(request.prompt)
            if request.prompt == "first":
                entered.set()
                assert gate.wait(5)
            if request.reference_paths:
                with Image.open(request.reference_paths[0]) as image:
                    pixels.append(image.size)
            return {"outputs": [], "width": 256, "height": 256}

    library = Library(tmp_path / "queue.db")
    queue = JobQueue(Runner(), library)
    try:
        first = queue.submit_generate(GenerateRequest(prompt="first"))
        assert entered.wait(5)
        reference = tmp_path / "ref.png"
        Image.new("RGB", (300, 400)).save(reference)
        second = queue.submit_generate(GenerateRequest(prompt="second", reference_paths=[str(reference)]))
        cancelled = queue.submit_generate(GenerateRequest(prompt="cancelled"))
        last = queue.submit_generate(GenerateRequest(prompt="last"))
        assert second.status == "queued"
        assert library.get(second.id)["status"] == "queued"
        Image.new("RGB", (600, 800)).save(reference)
        assert queue.cancel(cancelled.id)
        assert library.get(cancelled.id)["status"] == "cancelled"
        assert cancelled.events[-1]["status"] == "cancelled"
        assert not cancelled.job_dir.exists()
        gate.set()
        wait(last)
        assert prompts == ["first", "second", "last"]
        assert pixels == [(300, 400)]
        assert second.started_at >= first.finished_at
        assert last.started_at >= second.finished_at
    finally:
        gate.set()
        queue.shutdown()
        library.close()


def test_failure_does_not_stop_queue(isolated_dirs):
    class Runner:
        def generate(self, request, **kwargs):
            if request.prompt == "fail":
                raise ValueError("test failure")
            return {"outputs": []}

    queue = JobQueue(Runner())
    try:
        failed = queue.submit_generate(GenerateRequest(prompt="fail"))
        good = queue.submit_generate(GenerateRequest(prompt="good"))
        assert wait(failed).status == "failed"
        assert wait(good).status == "done"
    finally:
        queue.shutdown()
