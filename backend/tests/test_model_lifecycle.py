"""Model switching releases whole pipelines without interrupting an active image."""
from __future__ import annotations

import gc
import threading
import weakref

import pytest

from refract_backend import sysinfo
from refract_backend.runner import MfluxRunner
from refract_backend.schemas import GenerateRequest


class Component:
    pass


class Pipeline:
    def __init__(self):
        self.transformer = Component()
        self.text_encoder = Component()
        self.vae = Component()
        self.callbacks = self  # exercise a callback/model reference cycle


@pytest.fixture()
def runner(monkeypatch):
    monkeypatch.setattr(sysinfo, "clear_cache", gc.collect)
    runner = MfluxRunner()
    monkeypatch.setattr(runner, "_construct", lambda *_: Pipeline())
    def generate(request, reporter, cancel_event, job_dir):
        runner.build_model(request.model_path, request.quantize, request.model_source)
        return {"outputs": []}
    monkeypatch.setattr(runner, "_generate", generate)
    return runner


def generate(runner, source="prepared", path="/models/a"):
    return runner.generate(GenerateRequest(prompt="test", model_source=source, model_path=path), lambda _: None, threading.Event())


def test_idle_switch_releases_transformer_encoder_vae_and_callbacks(runner):
    generate(runner)
    refs = [weakref.ref(getattr(runner._model, name)) for name in ("transformer", "text_encoder", "vae")]
    pipeline = weakref.ref(runner._model)
    result = runner.select_model("custom", "/models/b")
    assert result == {"unloaded": True, "deferred": False}
    assert runner._model is None and runner._key is None
    assert pipeline() is None
    assert all(ref() is None for ref in refs)


def test_same_selection_keeps_one_warm_model(runner):
    generate(runner)
    model = weakref.ref(runner._model)
    assert runner.select_model("prepared", "/models/a") == {"unloaded": False, "deferred": False}
    generate(runner)
    assert runner._model is model()


def test_switch_during_generation_defers_until_the_image_finishes(runner, monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    model_ref = []
    def active_generate(request, reporter, cancel_event, job_dir):
        runner.build_model(request.model_path, request.quantize, request.model_source)
        model_ref.append(weakref.ref(runner._model))
        entered.set()
        assert finish.wait(5)
        assert runner._model is model_ref[0]()
        return {"outputs": ["finished.png"]}
    monkeypatch.setattr(runner, "_generate", active_generate)
    results = []
    worker = threading.Thread(target=lambda: results.append(generate(runner)))
    worker.start()
    try:
        assert entered.wait(5)
        assert runner.select_model("custom", "/models/b") == {"unloaded": False, "deferred": True}
        assert model_ref[0]() is not None
    finally:
        finish.set()
        worker.join(5)
    assert not worker.is_alive()
    assert results == [{"outputs": ["finished.png"]}]
    assert runner._model is None and model_ref[0]() is None


def test_queued_old_model_is_not_left_warm_when_another_is_selected(runner):
    runner.select_model("custom", "/models/b")
    generate(runner)  # a previously queued request still runs with its saved settings
    assert runner._model is None
    generate(runner, "custom", "/models/b")
    assert runner._model is not None


def test_changed_weights_are_gone_before_constructing_the_next_pipeline(runner, monkeypatch):
    generate(runner)
    old = weakref.ref(runner._model)
    def construct(*_):
        assert old() is None
        return Pipeline()
    monkeypatch.setattr(runner, "_construct", construct)
    generate(runner, "custom", "/models/b")
    assert runner._model is not None


def test_failed_generation_drops_its_pipeline(runner, monkeypatch):
    def fail(request, *_):
        runner.build_model(request.model_path, request.quantize, request.model_source)
        raise RuntimeError("failed")
    monkeypatch.setattr(runner, "_generate", fail)
    with pytest.raises(RuntimeError, match="failed"):
        generate(runner)
    assert runner._model is None and not runner._running


def test_selection_api_is_authenticated_and_validated(client):
    assert client.post("/api/models/select", json={"model_source": "prepared"}).status_code == 200
    assert client.post("/api/models/select", json={"model_source": "unknown"}).status_code == 422
    assert client.post("/api/models/select", json={"model_source": "custom", "model_path": []}).status_code == 422
    client.headers.clear()
    assert client.post("/api/models/select", json={"model_source": "prepared"}).status_code == 401


def test_preparation_releases_warm_generation_before_it_runs(isolated_dirs, monkeypatch):
    from refract_backend.jobs import Job, JobQueue
    class WarmRunner:
        loaded = True
        def release(self):
            self.loaded = False
    warm = WarmRunner()
    queue = JobQueue(runner=warm)
    def prepare(job):
        assert not warm.loaded
    monkeypatch.setattr(queue, "_run_prepare", prepare)
    job = Job(id="prepare-test", kind="prepare", payload={"source_id": "prepared"})
    queue._execute(job)
    assert job.status == "done"


def test_idle_switch_releases_real_mlx_allocations(monkeypatch):
    mx = pytest.importorskip("mlx.core")
    if not mx.metal.is_available():
        pytest.skip("Metal is required for the allocation check")
    sysinfo.clear_cache()
    baseline = mx.get_active_memory()
    class TinyPipeline:
        def __init__(self):
            self.transformer = mx.random.uniform(shape=(512, 512))
            self.text_encoder = mx.random.uniform(shape=(512, 512))
            self.vae = mx.random.uniform(shape=(512, 512))
            mx.eval(self.transformer, self.text_encoder, self.vae)
    runner = MfluxRunner()
    monkeypatch.setattr(runner, "_construct", lambda *_: TinyPipeline())
    def tiny_generate(request, *_):
        runner.build_model(request.model_path, request.quantize, request.model_source)
        return {"outputs": []}
    monkeypatch.setattr(runner, "_generate", tiny_generate)
    generate(runner)
    assert mx.get_active_memory() >= baseline + 3 * 512 * 512 * 4
    runner.select_model("custom", "/models/b")
    assert mx.get_active_memory() <= baseline + 4096
    assert mx.get_cache_memory() == 0


def test_failed_preparation_releases_its_partial_model(isolated_dirs, monkeypatch):
    from refract_backend.jobs import Job, JobQueue
    class WarmRunner:
        loaded = True
        def release(self):
            self.loaded = False
    warm = WarmRunner()
    queue = JobQueue(runner=warm)
    def fail(job):
        assert not warm.loaded
        warm.loaded = True
        raise RuntimeError("preparation failed")
    monkeypatch.setattr(queue, "_run_prepare", fail)
    job = Job(id="failed-prepare", kind="prepare", payload={"source_id": "prepared"})
    queue._execute(job)
    assert job.status == "failed" and not warm.loaded
