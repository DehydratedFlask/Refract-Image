"""The local HTTP API.

Bound to loopback and gated on a token minted per launch, because anything reachable on
127.0.0.1 on macOS is reachable by every process the user runs. The token rides in a header
(`X-Refract-Token`) or, for `EventSource`, which cannot set headers, in a query parameter.
"""

from __future__ import annotations

import json
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from . import __version__, encoders, ingest, model_store, setup, sysinfo
from .jobs import JobQueue, encode_sse
from .library import Library
from .mock_runner import MockRunner
from .paths import app_data_dir, hf_cache_dir, jobs_dir, models_dir, outputs_dir, projects_dir
from .projects import ProjectError, ProjectStore
from .avatars import AvatarError, AvatarStore
from .runner import MfluxRunner
from .schemas import GenerateRequest, InstallRequest, ValidateRequest

MAX_FILE_BYTES = 256 * 1024 * 1024


class AppState:
    def __init__(self, token: str, force_mock: bool = False) -> None:
        self.token = token
        self.started_at = time.time()
        self.mock = force_mock or not sysinfo.runner_available()
        self.runner = MockRunner() if self.mock else MfluxRunner()
        self.library = Library()
        self.projects = ProjectStore(self.library.connection, projects_dir(), self.library.lock)
        self.avatars = AvatarStore(self.library.connection, app_data_dir() / "avatars", self.library.lock)
        self.queue = JobQueue(runner=self.runner, library=self.library)


def create_app(token: str, force_mock: bool = False) -> FastAPI:
    state = AppState(token=token, force_mock=force_mock)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        # Shut the worker down before the process goes away, so a cancelled job's
        # reference copies and preview frames do not outlive it.
        state.queue.shutdown()
        state.library.close()
        state.projects.close()

    app = FastAPI(title="Refract Image", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.refract = state

    # The page reaches this API from whatever loopback origin it was served from — the app's own
    # static server, or vite on 1420 in development — so origins stay open and the token is what
    # actually authorises a caller. Nothing here is reachable off loopback in the first place.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def require_token(request: Request) -> None:
        supplied = (
            request.headers.get("x-refract-token")
            or request.query_params.get("token")
            or (request.headers.get("authorization", "").removeprefix("Bearer ").strip())
        )
        if supplied != state.token:
            raise HTTPException(status_code=401, detail="invalid or missing Refract Image token")

    guard = Depends(require_token)

    # ---- meta -------------------------------------------------------------
    @app.get("/api/health", dependencies=[guard])
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": __version__,
            "mock": state.mock,
            "runner_ready": sysinfo.runner_available(),
            "runner_note": sysinfo.runner_unavailable_reason(),
            "mflux_version": sysinfo.mflux_version(),
            "mflux_commit": sysinfo.mflux_commit(),
            "mlx_version": sysinfo.mlx_version(),
            "uptime_seconds": round(time.time() - state.started_at, 1),
            "busy": state.queue.busy,
        }

    @app.get("/api/system", dependencies=[guard])
    def system() -> dict[str, Any]:
        payload = sysinfo.system_info().to_dict()
        payload.update(
            {
                "mlx_active_memory_gb": sysinfo.active_memory_gb(),
                "mlx_peak_memory_gb": sysinfo.peak_memory_gb(),
                "busy": state.queue.busy,
                "data_dir": str(app_data_dir()),
            }
        )
        return payload

    # ---- models -----------------------------------------------------------
    @app.get("/api/sources", dependencies=[guard])
    def sources() -> dict[str, Any]:return {"sources": model_store.list_sources(), "disk": model_store.disk_report()}

    @app.get("/api/setup", dependencies=[guard])
    def setup_report() -> dict[str, Any]:
        return setup.describe_setup()

    @app.get("/api/encoders", dependencies=[guard])
    def list_encoders() -> dict[str, Any]:
        return encoders.describe_encoders()

    # ---- avatars ----------------------------------------------------------
    @app.get("/api/avatars", dependencies=[guard])
    def list_avatars() -> dict[str, Any]:
        return {"avatars": state.avatars.list()}

    @app.post("/api/avatars", dependencies=[guard], status_code=201)
    def create_avatar(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return state.avatars.save(body)
        except AvatarError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/avatars/{avatar_id}", dependencies=[guard])
    def update_avatar(avatar_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return state.avatars.save(body, avatar_id)
        except AvatarError as exc:
            raise HTTPException(status_code=404 if "not found" in str(exc) else 400, detail=str(exc)) from exc

    @app.delete("/api/avatars/{avatar_id}", dependencies=[guard])
    def delete_avatar(avatar_id: str) -> dict[str, Any]:
        try:
            state.avatars.delete(avatar_id)
        except AvatarError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": avatar_id}

    # ---- projects ---------------------------------------------------------
    def _project_payload(project: dict[str, Any]) -> dict[str, Any]:
        """A project plus the counts the sidebar shows, computed in one place."""
        counts = state.projects.counts(project["id"])
        project["generation_count"] = sum(counts.values())
        project["running_count"] = counts.get("queued", 0) + counts.get("running", 0)
        return project

    @app.get("/api/projects", dependencies=[guard])
    def list_projects() -> dict[str, Any]:
        return {
            "projects": [_project_payload(project) for project in state.projects.list()],
            "storage": state.projects.size_report(),
        }

    @app.post("/api/projects", dependencies=[guard], status_code=201)
    def create_project(body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = body or {}
        try:
            project = state.projects.create(
                name=payload.get("name"),
                session=payload.get("session"),
                prompt=payload.get("prompt"),
            )
        except ProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _project_payload(project)

    @app.get("/api/projects/{project_id}", dependencies=[guard])
    def get_project(project_id: str) -> dict[str, Any]:
        project = state.projects.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        return _project_payload(project)

    @app.put("/api/projects/{project_id}", dependencies=[guard])
    def update_project(project_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Autosave target: the whole session, or just a new name."""
        payload = body or {}
        try:
            if "session" not in payload and "references" not in payload and "session_id" not in payload:
                project = state.projects.rename(project_id, payload.get("name", ""))
            else:
                project = state.projects.save_session(
                    project_id,
                    session=payload.get("session"),
                    references=payload.get("references"),
                    name=payload.get("name"),
                    session_id=payload.get("session_id"),
                )
        except ProjectError as exc:
            raise HTTPException(status_code=404 if "no project" in str(exc) else 400, detail=str(exc)) from exc
        return _project_payload(project)

    @app.post("/api/projects/{project_id}/sessions", dependencies=[guard], status_code=201)
    def create_project_session(project_id: str) -> dict[str, Any]:
        try:
            return _project_payload(state.projects.create_session(project_id))
        except ProjectError as exc:
            raise HTTPException(status_code=404 if "no project" in str(exc) else 400, detail=str(exc)) from exc

    @app.delete("/api/projects/{project_id}", dependencies=[guard])
    def delete_project(project_id: str) -> dict[str, Any]:
        try:
            return state.projects.delete(project_id)
        except ProjectError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/validate", dependencies=[guard])
    def validate(payload: ValidateRequest) -> dict[str, Any]:
        report = ingest.analyze(payload.path, component=payload.component)
        if payload.component == "transformer" and payload.base_model_path and report.get("ok"):
            report["verification"] = ingest.verify_loads(payload.base_model_path)
        return report

    @app.post("/api/models/select", dependencies=[guard])
    def select_model(payload: dict[str, Any]) -> dict[str, bool]:
        source_id = payload.get("model_source")
        if source_id not in model_store.SOURCES:
            raise HTTPException(status_code=422, detail="unknown model source")
        model_path = payload.get("model_path")
        if model_path is not None and not isinstance(model_path, str):
            raise HTTPException(status_code=422, detail="model_path must be a string or null")
        return state.runner.select_model(source_id, model_path)

    @app.post("/api/models/delete", dependencies=[guard])
    def delete_model(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return model_store.delete_path(payload.get("path", ""), confirm=bool(payload.get("confirm")))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ---- jobs -------------------------------------------------------------
    @app.post("/api/jobs", dependencies=[guard], status_code=202)
    def create_job(body: dict[str, Any]) -> dict[str, Any]:
        kind = body.get("kind")
        payload = body.get("payload") or {}
        if kind == "generate":
            try:
                request = GenerateRequest(**payload)
            except Exception as exc:
                detail = [
                    {"loc": list(err.get("loc", [])), "msg": str(err.get("msg", ""))}
                    for err in getattr(exc, "errors", lambda: [])()
                ] or [{"loc": [], "msg": str(exc)}]
                raise HTTPException(status_code=422, detail=detail) from exc
            try:
                with state.library.lock:
                    request = state.avatars.resolve(request)
                    return state.queue.submit_generate(request).to_dict()
            except AvatarError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if kind == "install":
            InstallRequest(**payload)
        if kind == "validate":
            ValidateRequest(**payload)
        if kind in ("download", "prepare") and not payload.get("source_id"):
            raise HTTPException(status_code=422,detail=[{"loc": ["source_id"], "msg": "required"}])
        try:
            job = state.queue.submit(kind, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return job.to_dict()

    @app.get("/api/jobs", dependencies=[guard])
    def list_jobs(limit: int = 50) -> dict[str, Any]:
        return {"jobs": [job.to_dict() for job in state.queue.list(limit=limit)]}

    @app.get("/api/jobs/{job_id}", dependencies=[guard])
    def get_job(job_id: str) -> dict[str, Any]:
        job = state.queue.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job.to_dict(include_events=True)

    @app.post("/api/jobs/{job_id}/cancel", dependencies=[guard])
    def cancel_job(job_id: str) -> dict[str, Any]:
        cancelled = state.queue.cancel(job_id)
        job = state.queue.get(job_id)
        return {"cancelled": cancelled, "job": job.to_dict() if job else None}

    @app.get("/api/jobs/{job_id}/events")
    def job_events(job_id: str, request: Request, since: int = 0) -> StreamingResponse:
        # EventSource cannot send headers, so this one route accepts the token as a query
        # parameter and validates it itself.
        if request.query_params.get("token") != state.token:
            raise HTTPException(status_code=401, detail="invalid or missing Refract Image token")
        if state.queue.get(job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")

        def stream():
            cursor = since
            idle = 0.0
            while True:
                events = state.queue.wait_for_events(job_id, since=cursor, timeout=1.0)
                if events:
                    for event in events:
                        cursor = max(cursor, event["seq"])
                        yield encode_sse(event)
                    idle = 0.0
                else:
                    idle += 1.0
                    if idle >= 15:  # keep-alive comment; also detects a dead client
                        yield ": ping\n\n"
                        idle = 0.0
                job = state.queue.get(job_id)
                if job is None or job.status in ("done", "failed", "cancelled"):
                    final = state.queue.wait_for_events(job_id, since=cursor, timeout=0.2)
                    for event in final:
                        cursor = max(cursor, event["seq"])
                        yield encode_sse(event)
                    yield encode_sse({"phase": job.phase if job else "done", "final": True, "job_id": job_id})
                    return

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---- library ----------------------------------------------------------
    @app.get("/api/library", dependencies=[guard])
    def library(
        search: str | None = None,
        favorites: bool = False,
        model_source: str | None = None,
        project_id: str | None = None,
        project_session_id: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> dict[str, Any]:
        items = state.library.list(
            limit=limit,
            offset=offset,
            search=search,
            favorites_only=favorites,
            model_source=model_source,
            project_id=project_id,
            project_session_id=project_session_id,
        )
        return {"items": items, "count": len(items)}

    @app.post("/api/library/{job_id}/favorite", dependencies=[guard])
    def favorite(job_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        favorite_value = bool((body or {}).get("favorite", True))
        state.library.set_favorite(job_id, favorite_value)
        return {"id": job_id, "favorite": favorite_value}

    @app.delete("/api/library/{job_id}", dependencies=[guard])
    def delete_item(job_id: str, delete_files: bool = False) -> dict[str, Any]:
        item = state.library.get(job_id)
        if item is None:
            raise HTTPException(status_code=404, detail="not found")
        job = state.queue.get(job_id)
        if (job and job.status in ("queued", "running")) or item["status"] in ("queued", "running"):
            raise HTTPException(status_code=409, detail="Cancel the task and wait for it to finish before deleting it")
        removed: list[str] = []
        if delete_files:
            for raw in item.get("outputs", []):
                path = Path(raw)
                try:
                    # Either the service's own outputs tree, or a folder the user chose and
                    # this service has written to — deleting the PNG has to work there too.
                    if path.is_file() and _is_own_output(path, state):
                        path.unlink()
                        removed.append(str(path))
                    for sidecar in (path.with_suffix(".metadata.json"), path.with_suffix(".png.metadata.json")):
                        if sidecar.is_file():
                            sidecar.unlink()
                            removed.append(str(sidecar))
                except OSError as exc:
                    raise HTTPException(status_code=409, detail=f"Could not delete {path}: {exc}") from exc
        state.library.delete(job_id)
        return {"deleted": job_id, "removed_files": removed}

    @app.post("/api/library/{job_id}/replay", dependencies=[guard], status_code=202)
    def replay(job_id: str) -> dict[str, Any]:
        item = state.library.get(job_id)
        if item is None:
            raise HTTPException(status_code=404, detail="not found")
        params = dict(item.get("params") or {})
        # References recorded on the original run are reused only if they still exist; a
        # missing one is reported by the job rather than silently dropped into a
        # different composition.
        params["reference_paths"] = list(item.get("references") or [])
        params["seed"] = (item.get("seeds") or [None])[0]
        params["seeds"] = item.get("seeds") or None
        try:
            request = GenerateRequest(**params)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"cannot replay this entry: {exc}") from exc
        return state.queue.submit_generate(request).to_dict()

    @app.post("/api/uploads")
    async def uploads(request: Request, files: list[UploadFile] = File(...)) -> dict[str, Any]:
        # Used when the UI runs in a browser, where a dropped file has no filesystem path.
        if request.query_params.get("token") != state.token:
            raise HTTPException(status_code=401, detail="invalid or missing Refract Image token")
        if len(files) > 10:
            raise HTTPException(status_code=413, detail="at most 10 files per upload")
        destination = jobs_dir() / "uploads"
        destination.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        for upload in files:
            suffix = Path(upload.filename or "upload.png").suffix.lower() or ".png"
            if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"):
                raise HTTPException(status_code=415, detail=f"unsupported image type {suffix}")
            target = destination / f"{uuid.uuid4().hex[:12]}-{Path(upload.filename or 'upload').stem[:40]}{suffix}"
            payload = await upload.read()
            if len(payload) > 64 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="image is larger than 64 MB")
            target.write_bytes(payload)
            saved.append(str(target))
        return {"paths": saved}

    # ---- files ------------------------------------------------------------
    @app.get("/api/files")
    def serve_file(path: str = Query(...), token: str = Query(...)) -> FileResponse:
        # Images are streamed over HTTP rather than exposed through the webview's asset
        # protocol so that the same UI code works in a browser during development.
        if token != state.token:
            raise HTTPException(status_code=401, detail="invalid or missing Refract Image token")
        target = Path(path).expanduser().resolve()
        if not target.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        if not _is_readable(target, state):
            raise HTTPException(status_code=403, detail="path is outside the folders Refract Image may read")
        if target.stat().st_size > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="file is too large to stream")
        media_type, _ = mimetypes.guess_type(target.name)
        return FileResponse(
            target,
            media_type=media_type or "application/octet-stream",
            headers={"Cache-Control": "no-store"},
        )

    return app


def _is_readable(target: Path, state: AppState) -> bool:
    """Allow the app's own directories, plus the usual places a user keeps images."""
    home = Path.home()
    allowed_roots = [
        outputs_dir(),
        jobs_dir(),
        models_dir(),
        hf_cache_dir(),
        home / "Pictures",
        home / "Desktop",
        home / "Downloads",
        home / "Documents",
        home / "Movies",
        app_data_dir(),
    ]
    for root in allowed_roots:
        try:
            target.relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            continue
    images_dir = home / "Pictures"
    if str(target).startswith(str(images_dir)):
        return True
    # Anything this service actually wrote is readable, wherever the user sent it: the
    # destination is chosen at generation time, so it cannot be in a static list.
    if _is_own_output(target, state):
        return True
    # Reference images registered by any job are readable wherever they live.
    for job in state.queue.list(limit=200):
        for reference in job.references:
            if Path(reference).expanduser().resolve() == target:
                return True
    return False


def _folders_refract_wrote(state: AppState) -> set[Path]:
    """Folders this service has written results into, wherever the user chose to put them.

    The Library is the record, rather than the in-memory job list, because it survives a
    restart — otherwise images in a chosen folder would become unreadable when the app is
    relaunched.
    """
    folders: set[Path] = set()
    try:
        outputs = state.library.all_output_paths()
    except Exception:  # noqa: BLE001 - a broken index must not take image viewing down
        return folders
    for raw in outputs:
        try:
            folders.add(Path(raw).expanduser().resolve().parent)
        except (OSError, ValueError):
            continue
    return folders


def _is_own_output(path: Path, state: AppState) -> bool:
    """True when ``path`` is a file this service wrote during an earlier run."""
    if not path.is_file():
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return any(Path(raw).expanduser().resolve() == resolved for raw in state.library.all_output_paths())


def default_token() -> str:
    import secrets

    return secrets.token_urlsafe(24)


def json_default(value: Any) -> str:
    return json.dumps(value, default=str)
