"""Models stay warm until another generation replaces them or the app shuts down."""
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
        runner._warm_selection = (request.model_source, request.model_path or None)
        return {"outputs": []}
    monkeypatch.setattr(runner, "_generate", generate)
    return runner


def generate(runner, source="prepared", path="/models/a"):
    return runner.generate(GenerateRequest(prompt="test", model_source=source, model_path=path), lambda _: None, threading.Event())


def test_idle_selection_keeps_transformer_encoder_vae_and_callbacks(runner):
    generate(runner)
    refs = [weakref.ref(getattr(runner._model, name)) for name in ("transformer", "text_encoder", "vae")]
    pipeline = weakref.ref(runner._model)
    result = runner.select_model("custom", "/models/b")
    assert result == {"unloaded": False, "deferred": True}
    assert runner._model is pipeline()
    assert all(ref() is not None for ref in refs)


def test_same_selection_keeps_one_warm_model(runner):
    generate(runner)
    model = weakref.ref(runner._model)
    assert runner.select_model("prepared", "/models/a") == {"unloaded": False, "deferred": False}
    generate(runner)
    assert runner._model is model()


@pytest.mark.parametrize("legacy_low_ram", [False, True])
def test_generation_reuses_all_components_and_clears_job_callbacks(isolated_dirs, monkeypatch, legacy_low_ram):
    from types import SimpleNamespace
    from PIL import Image
    from refract_backend import runner as runner_module

    constructions = []
    class ImagePipeline(Pipeline):
        def generate_image(self, **kwargs):
            return SimpleNamespace(image=Image.new("RGB", (32, 32)), width=32, height=32)

    def construct(*_):
        pipeline = ImagePipeline()
        constructions.append(weakref.ref(pipeline))
        return pipeline

    monkeypatch.setattr(MfluxRunner, "_construct", staticmethod(construct))
    monkeypatch.setattr(runner_module, "resolve_model", lambda *_: runner_module.ResolvedModel(
        "prepared", "test", "/models/a", 4))
    runner = MfluxRunner()
    monkeypatch.setattr(runner, "_save", lambda image, request, target, seed: target / "test.png")
    request = GenerateRequest(prompt="test", model_source="prepared", model_path="/models/a", low_ram=legacy_low_ram)
    runner.generate(request, lambda _: None, threading.Event())
    components = [weakref.ref(getattr(runner._model, name)) for name in ("transformer", "text_encoder", "vae")]
    callbacks = weakref.ref(runner._model.callbacks)
    runner.generate(request.model_copy(update={"project_id": "another-project", "project_session_id": "another-session"}),
                    lambda _: None, threading.Event())
    assert len(constructions) == 1 and runner._model is constructions[0]()
    assert all(ref() is getattr(runner._model, name) for ref, name in zip(components, ("transformer", "text_encoder", "vae")))
    assert callbacks() is None


def test_selection_during_generation_keeps_model_after_the_image_finishes(runner, monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    model_ref = []
    def active_generate(request, reporter, cancel_event, job_dir):
        runner.build_model(request.model_path, request.quantize, request.model_source)
        runner._warm_selection = (request.model_source, request.model_path or None)
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
    assert runner._model is model_ref[0]()


def test_queued_model_stays_warm_until_the_next_model_runs(runner):
    runner.select_model("custom", "/models/b")
    generate(runner)  # a previously queued request still runs with its saved settings
    old = weakref.ref(runner._model)
    assert old() is not None
    generate(runner, "custom", "/models/b")
    assert old() is None
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


def test_failed_generation_keeps_its_loaded_pipeline(runner, monkeypatch):
    def fail(request, *_):
        runner.build_model(request.model_path, request.quantize, request.model_source)
        raise RuntimeError("failed")
    monkeypatch.setattr(runner, "_generate", fail)
    with pytest.raises(RuntimeError, match="failed"):
        generate(runner)
    assert runner._model is not None and not runner._running
    model = weakref.ref(runner._model)
    with pytest.raises(RuntimeError, match="failed"):
        generate(runner)
    assert runner._model is model()


def test_cancelled_generation_keeps_the_loaded_pipeline(runner, monkeypatch):
    from mflux.utils.exceptions import StopImageGenerationException
    generate(runner)
    model = weakref.ref(runner._model)
    def cancel(*_):
        raise StopImageGenerationException("cancelled")
    with monkeypatch.context() as patch:
        patch.setattr(runner, "_generate", cancel)
        with pytest.raises(StopImageGenerationException):
            generate(runner)
    generate(runner)
    assert runner._model is model() and not runner._running


def test_invalid_next_request_keeps_the_previous_model(runner, monkeypatch):
    generate(runner)
    model = weakref.ref(runner._model)
    def fail_before_loading(*_):
        raise ValueError("invalid output directory")
    monkeypatch.setattr(runner, "_generate", fail_before_loading)
    with pytest.raises(ValueError, match="invalid output directory"):
        generate(runner, "custom", "/models/b")
    assert runner._model is model()
    assert runner.select_model("prepared", "/models/a") == {"unloaded": False, "deferred": False}


def test_selection_api_is_authenticated_and_validated(client):
    assert client.post("/api/models/select", json={"model_source": "prepared"}).status_code == 200
    assert client.post("/api/models/select", json={"model_source": "unknown"}).status_code == 422
    assert client.post("/api/models/select", json={"model_source": "custom", "model_path": []}).status_code == 422
    client.headers.clear()
    assert client.post("/api/models/select", json={"model_source": "prepared"}).status_code == 401


def test_preparation_keeps_the_warm_generation_model(isolated_dirs, monkeypatch):
    from refract_backend.jobs import Job, JobQueue
    class WarmRunner:
        loaded = True
        def release(self):
            self.loaded = False
    warm = WarmRunner()
    queue = JobQueue(runner=warm)
    def prepare(job):
        assert warm.loaded
    monkeypatch.setattr(queue, "_run_prepare", prepare)
    job = Job(id="prepare-test", kind="prepare", payload={"source_id": "prepared"})
    queue._execute(job)
    assert job.status == "done" and warm.loaded


def test_selection_keeps_real_mlx_weights_until_shutdown(monkeypatch):
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
        runner._warm_selection = (request.model_source, request.model_path or None)
        return {"outputs": []}
    monkeypatch.setattr(runner, "_generate", tiny_generate)
    generate(runner)
    assert mx.get_active_memory() >= baseline + 3 * 512 * 512 * 4
    runner.select_model("custom", "/models/b")
    assert mx.get_active_memory() >= baseline + 3 * 512 * 512 * 4
    from refract_backend.jobs import JobQueue
    JobQueue(runner).shutdown()
    assert mx.get_active_memory() <= baseline + 4096
    assert mx.get_cache_memory() == 0


def test_failed_preparation_keeps_the_warm_generation_model(isolated_dirs, monkeypatch):
    from refract_backend.jobs import Job, JobQueue
    class WarmRunner:
        loaded = True
        def release(self):
            self.loaded = False
    warm = WarmRunner()
    queue = JobQueue(runner=warm)
    def fail(job):
        assert warm.loaded
        raise RuntimeError("preparation failed")
    monkeypatch.setattr(queue, "_run_prepare", fail)
    job = Job(id="failed-prepare", kind="prepare", payload={"source_id": "prepared"})
    queue._execute(job)
    assert job.status == "failed" and warm.loaded
