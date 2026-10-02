"""One queue for every long-running thing the app does.

Generating an image, downloading a model, exporting a prepared directory and installing a
custom pack all take seconds to minutes and all need the same three things: live progress,
cancellation, and a result to hand back. So they are all *jobs* on one single-flight
worker, which also enforces the rule that matters on a shared GPU — never two diffusion
pipelines resident at once.

Jobs are also the unit of persistence: the Library row for a generation is written when the
job starts and completed when it ends, so a crash mid-run leaves an honest record.
"""

from __future__ import annotations

import json
import queue
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ingest, model_store, sysinfo
from .library import Library
from .paths import app_data_dir, jobs_dir
from .schemas import GenerateRequest, InstallRequest, ValidateRequest

JOB_KINDS = ("generate", "download", "prepare", "prepare-klein", "install", "validate")


@dataclass
class Job:
    id: str
    kind: str
    payload: dict[str, Any]
    status: str = "queued"
    phase: str = "queued"
    message: str | None = None
    step: int = 0
    total_steps: int = 0
    seeds: list[int] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    preview_path: str | None = None
    references: list[str] = field(default_factory=list)
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    seconds_per_step: float | None = None
    eta_seconds: float | None = None
    elapsed_seconds: float = 0.0
    peak_memory_gb: float | None = None
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    model_source: str | None = None
    model_path: str | None = None
    quantize: int | None = None
    width: int | None = None
    height: int | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    events: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _seq: int = 0
    snapshot_error: str | None = field(default=None, repr=False)
    local_references: list[str] = field(default_factory=list, repr=False)

    @property
    def job_dir(self) -> Path:
        return jobs_dir() / self.id

    def emit(self, event: dict[str, Any]) -> None:
        """Record an event and fold its fields into the job's live state."""
        self._seq += 1
        payload = {**event, "seq": self._seq, "at": time.time(), "status": self.status, "job_id": self.id}
        self.events.append(payload)
        if len(self.events) > 2000:
            del self.events[:500]

        for key in (
            "phase",
            "message",
            "step",
            "total_steps",
            "seeds",
            "preview_path",
            "seconds_per_step",
            "eta_seconds",
            "elapsed_seconds",
            "peak_memory_gb",
            "downloaded_bytes",
            "total_bytes",
            "width",
            "height",
            "outputs",
        ):
            if key in event and event[key] is not None:
                setattr(self, key, event[key])

    def to_dict(self, include_events: bool = False) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "phase": self.phase,
            "message": self.message,
            "step": self.step,
            "total_steps": self.total_steps,
            "seeds": self.seeds,
            "outputs": self.outputs,
            "preview_path": self.preview_path,
            "references": self.references,
            "error": self.error,
            "result": self.result,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "seconds_per_step": self.seconds_per_step,
            "eta_seconds": self.eta_seconds,
            "elapsed_seconds": self.elapsed_seconds,
            "peak_memory_gb": self.peak_memory_gb,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "model_source": self.model_source,
            "model_path": self.model_path,
            "quantize": self.quantize,
            "width": self.width,
            "height": self.height,
            "payload": self.payload,
            "seq": self._seq,
        }
        if include_events:
            payload["events"] = self.events
        return payload


class JobQueue:
    """Serial worker with cancel support and event streaming."""

    def __init__(self, runner: Any, library: Library | None = None) -> None:
        self.runner = runner
        self.library = library
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._pending: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

    # ---- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="refract-worker", daemon=True)
        self._worker.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        for job in self.list(limit=len(self._order) or 1):
            if job.status in ("queued", "running"):
                self.cancel(job.id)
        self._stop.set()
        self._pending.put("__stop__")
        if self._worker:
            self._worker.join(timeout=timeout)

    @property
    def busy(self) -> bool:
        with self._lock:
            return any(job.status == "running" for job in self._jobs.values())

    # ---- submission -------------------------------------------------------
    def submit(self, kind: str, payload: dict[str, Any]) -> Job:
        if kind not in JOB_KINDS:
            raise ValueError(f"unknown job kind {kind!r}")
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, payload=payload)
        job.job_dir.mkdir(parents=True, exist_ok=True)
        return self._enqueue(job)

    def _enqueue(self, job: Job) -> Job:
        # Publish only after payload, reference snapshots and Library row are complete.
        with self._condition:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._condition.notify_all()
            self._pending.put(job.id)
            self.start()
        return job

    def submit_generate(self, request: GenerateRequest) -> Job:
        payload = request.model_dump()
        payload["seeds"] = request.seed_list()
        job = Job(id=uuid.uuid4().hex[:12], kind="generate", payload=payload)
        job.job_dir.mkdir(parents=True, exist_ok=True)
        # Snapshot at submission, not when the worker eventually reaches this task.
        try:
            for index, raw in enumerate(request.reference_paths, start=1):
                source = Path(raw).expanduser()
                if not source.is_file():
                    raise FileNotFoundError(f"reference image not found: {source}")
                reference_dir = app_data_dir() / "avatar-runs" / job.id if request.avatar_bindings else job.job_dir
                reference_dir.mkdir(parents=True, exist_ok=True)
                target = reference_dir / f"ref-{index}{source.suffix.lower() or '.png'}"
                shutil.copy2(source, target)
                job.local_references.append(str(target))
        except OSError as exc:
            job.snapshot_error = str(exc)
        # Avatar profiles may be edited/deleted later; history and replay use the
        # immutable per-job copies rather than the profile's current image files.
        job.references = list(job.local_references if request.avatar_bindings else request.reference_paths)
        job.total_steps = request.steps
        job.model_source = request.model_source
        job.model_path = request.model_path
        job.quantize = request.quantize
        job.seeds = request.seed_list()
        job.width = request.width
        job.height = request.height
        if self.library:
            self.library.record_start(
                job_id=job.id,
                prompt=request.prompt,
                negative_prompt=request.negative_prompt,
                params=request.to_metadata(),
                references=list(job.references),
                seeds=job.seeds,
                model_source=request.model_source,
                model_path=request.model_path,
                quantize=request.quantize,
                project_id=request.project_id,
            )
        return self._enqueue(job)

    # ---- access -----------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> list[Job]:
        with self._lock:
            return [self._jobs[jid] for jid in reversed(self._order[-limit:])]

    def cancel(self, job_id: str) -> bool:
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None or job.status in ("done", "failed", "cancelled"):
                return False
            job.cancel_event.set()
            if job.status == "queued":
                job.status = job.phase = "cancelled"
                job.message = "cancelled before it started"
                job.finished_at = time.time()
                job.emit({"phase": "cancelled", "message": job.message})
                if job.kind == "generate" and self.library:
                    self.library.record_finish(job.id, "cancelled", [])
                shutil.rmtree(job.job_dir, ignore_errors=True)
            self._condition.notify_all()
        return True

    def wait_for_events(self, job_id: str, since: int = 0, timeout: float = 15.0) -> list[dict[str, Any]]:
        """Block briefly for new events (SSE); returns everything newer than `since`."""
        deadline = time.time() + timeout
        with self._condition:
            while True:
                job = self._jobs.get(job_id)
                if job is None:
                    return []
                fresh = [event for event in job.events if event["seq"] > since]
                if fresh or job.status in ("done", "failed", "cancelled"):
                    return fresh
                remaining = deadline - time.time()
                if remaining <= 0:
                    return []
                self._condition.wait(timeout=min(remaining, 1.0))

    def _notify(self) -> None:
        with self._condition:
            self._condition.notify_all()

    # ---- worker -----------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._pending.get(timeout=0.5)
            except queue.Empty:
                continue
            if job_id == "__stop__":
                return
            job = self.get(job_id)
            if job is None or job.status == "cancelled":
                continue
            self._execute(job)

    def _execute(self, job: Job) -> None:
        with self._condition:
            if job.status != "queued" or job.cancel_event.is_set():
                return
            job.status = "running"
            job.started_at = time.time()
            job.emit({"phase": "resolving", "message": "starting"})
            if job.kind == "generate" and self.library:
                self.library.set_status(job.id, "running")
            self._condition.notify_all()

        try:
            # Preparation, installation and validation can construct their own pipeline.
            # Drop all warm generation components before these jobs touch the GPU.
            if job.kind in ("prepare", "prepare-klein", "install", "validate"):
                self.runner.release()
            if job.kind == "generate":
                self._run_generate(job)
            elif job.kind == "download":
                self._run_download(job)
            elif job.kind == "prepare":
                self._run_prepare(job)
            elif job.kind == "prepare-klein":
                self._run_prepare_klein(job)
            elif job.kind == "install":
                self._run_install(job)
            elif job.kind == "validate":
                self._run_validate(job)
            if job.cancel_event.is_set():
                job.status = "cancelled"
                job.phase = "cancelled"
                job.message = job.message or "cancelled"
            else:
                job.status = "done"
                job.phase = "done"
                job.message = job.message or "done"
        except Exception as exc:  # surfaced to the UI verbatim; no traceback swallowing
            # Mock/API errors must not depend on the optional inference stack importing.
            if type(exc).__name__ == "StopImageGenerationException" or job.cancel_event.is_set():
                job.status = "cancelled"
                job.phase = "cancelled"
                job.message = "cancelled by you"
            else:
                job.status = "failed"
                job.phase = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                job.message = job.error
        finally:
            if job.kind in ("prepare", "prepare-klein", "install", "validate"):
                # A failed preparation may have left a partial model on the runner.
                self.runner.release()
            elif job.kind == "generate" and job.status != "done":
                # Error tracebacks have unwound, so their model locals can be collected.
                sysinfo.clear_cache()
            job.finished_at = time.time()
            job.elapsed_seconds = round(job.finished_at - (job.started_at or job.finished_at), 2)
            job.emit(
                {
                    "phase": job.phase,
                    "message": job.message,
                    "elapsed_seconds": job.elapsed_seconds,
                    "peak_memory_gb": job.peak_memory_gb,
                    "outputs": job.outputs,
                }
            )
            if job.kind == "generate" and self.library:
                self.library.record_finish(
                    job_id=job.id,
                    status=job.status,
                    outputs=job.outputs,
                    duration_s=job.elapsed_seconds,
                    peak_memory_gb=job.peak_memory_gb,
                    width=job.width,
                    height=job.height,
                    error=job.error,
                    # The checkpoint the run actually loaded, which a request that named a
                    # source id rather than a path only reveals once it has resolved.
                    model_path=(job.result or {}).get("model_path"),
                    seeds=job.seeds,
                )
            self._notify()
            try:
                shutil.rmtree(job.job_dir, ignore_errors=True)
            except OSError:
                pass

    # ---- kind handlers ----------------------------------------------------
    def _run_generate(self, job: Job) -> None:
        request = GenerateRequest(**job.payload)
        if job.snapshot_error:
            raise FileNotFoundError(job.snapshot_error)
        if job.local_references:
            request = request.model_copy(update={"reference_paths": job.local_references})

        job.peak_memory_gb = None
        outcome = self.runner.generate(
            request=request,
            reporter=job.emit,
            cancel_event=job.cancel_event,
            job_dir=job.job_dir,
        )
        job.outputs = outcome.get("outputs", [])
        job.seeds = outcome.get("seeds", job.seeds)
        job.width = outcome.get("width") or job.width
        job.height = outcome.get("height") or job.height
        job.peak_memory_gb = outcome.get("peak_memory_gb")
        job.quantize = outcome.get("bits", job.quantize)
        job.result = {
            "outputs": job.outputs,
            "seeds": job.seeds,
            "duration_s": outcome.get("duration_s"),
            "peak_memory_gb": job.peak_memory_gb,
            "bits": outcome.get("bits"),
            "model_source": outcome.get("model_source"),
            "model_path": outcome.get("model_path"),
            "width": job.width,
            "height": job.height,
        }

    def _run_download(self, job: Job) -> None:
        source_id = job.payload["source_id"]
        job.model_source = source_id
        outcome = model_store.download_source(source_id, progress=job.emit)
        job.result = outcome
        job.phase = "done"
        job.message = f"{source_id} ready"

    def _run_prepare(self, job: Job) -> None:
        source_id = job.payload["source_id"]
        name = job.payload.get("name")
        job.model_source = source_id
        outcome = model_store.prepare_source(source_id, name=name, progress=job.emit)
        job.result = outcome
        job.phase = "done"
        job.message = f"prepared {Path(outcome['path']).name}"

    def _run_prepare_klein(self, job: Job) -> None:
        """Stage FLUX.2 Klein, which cannot be reached through ``prepare_source``.

        Klein is not a Hugging Face repo source: it is assembled from a base snapshot, a
        tokenizer and a converted encoder, which is why it was a terminal-only tool until
        now. Running it as a job is what lets a first-run setup offer it.
        """
        from .tools.prepare_klein import stage_klein

        job.model_source = "flux2-klein-4b"
        job.emit({"phase": "downloading", "message": "staging FLUX.2 Klein 4B"})
        job.result = stage_klein(
            job.payload.get("encoder", "ablated"),
            progress=lambda message: job.emit({"phase": "downloading", "message": message}),
        )
        job.phase = "done"
        job.message = f"prepared {job.result['name']} with the {job.result['encoder_label']}"

    def _run_install(self, job: Job) -> None:
        request = InstallRequest(**job.payload)
        base = Path(request.base_model_path).expanduser()
        target = model_store.models_dir() / request.name
        job.emit({"phase": "installing", "message": f"installing into {target.name}"})
        outcome = ingest.install(
            source=request.path,
            base_model_path=base,
            target_dir=target,
            quantize=request.quantize,
            progress=job.emit,
        )
        job.result = outcome
        if outcome.get("installed"):
            model_store.write_manifest(
                target,
                {
                    "name": target.name,
                    "origin": f"custom:{Path(request.path).name}",
                    "source_id": "custom",
                    "bits": outcome.get("bits"),
                    "created_at": model_store.utc_now_iso(),
                    "custom_source": request.path,
                },
            )
            job.phase = "done"
            job.message = f"installed {target.name}"
        else:
            job.phase = "failed"
            job.error = outcome.get("reason") or "validation failed"
            job.message = job.error
            # A rejected install must not leave a half-built directory behind.
            shutil.rmtree(target, ignore_errors=True)
            raise RuntimeError(job.error)

    def _run_validate(self, job: Job) -> None:
        request = ValidateRequest(**job.payload)
        job.emit({"phase": "validating", "message": "comparing tensors against the mflux module tree"})
        report = ingest.analyze(request.path, component=request.component)
        job.result = report
        job.phase = "done" if report.get("ok") else "failed"
        job.message = (
            f"{report.get('matched_keys', 0)} tensors match"
            if report.get("ok")
            else "; ".join(report.get("errors", ["incompatible checkpoint"]))
        )
        if not report.get("ok"):
            job.error = job.message


def summarise(job: Job) -> dict[str, Any]:
    """Compact form used by the JSON logs when a job finishes."""
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "phase": job.phase,
        "outputs": len(job.outputs),
        "error": job.error,
    }


def encode_sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"
