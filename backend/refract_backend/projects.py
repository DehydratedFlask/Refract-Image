"""Projects: named, persistent workspaces.

A project is the thing you come back to. It holds a prompt, a reference set and the
settings that were in force when you last touched it, so switching back restores the whole
working state rather than just the words you typed.

Two decisions shape the design.

**References are copied in, not linked.** A path in a project that points at your Desktop
breaks the moment the file moves, and a project that cannot be reopened is not a project.
Each project's images live under ``.refract/projects/<id>/refs`` and the stored session
refers to those copies. Originals are never touched or deleted; a reference removed from the
session has its copy deleted with it.

**The session is snapshotted, not diffed.** Every save writes the whole parameter set. That
is a few hundred bytes, and it means restoring is a single read with no migration logic
whenever the request schema grows a field — unknown keys are dropped, missing ones fall back
to the caller's current defaults, so an old project still opens after an upgrade.

Generations are tagged with the project that produced them (``generations.project_id``), so
the Library can be filtered to one project's work without the project itself having to keep
a list of results.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import threading
import time
from functools import wraps
from pathlib import Path
from typing import Any

from .paths import dir_size_bytes, human_bytes

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    prompt TEXT NOT NULL DEFAULT '',
    session_json TEXT NOT NULL DEFAULT '{}',
    references_json TEXT NOT NULL DEFAULT '[]',
    color TEXT
);
CREATE INDEX IF NOT EXISTS idx_projects_updated ON projects (updated_at DESC);
"""

MAX_NAME = 60
#: The request schema is the contract for what a session may contain. Anything else is
#: dropped on save rather than persisted, so a stale project cannot smuggle a field that a
#: later version would misread.
SESSION_FIELDS = (
    "prompt",
    "negative_prompt",
    "width",
    "height",
    "match_reference_size",
    "output_resolution",
    "steps",
    "guidance",
    "seed",
    "seeds",
    "quantize",
    "use_kv_cache",
    "low_ram",
    "vae_tiling",
    "mlx_cache_limit_gb",
    "preview_interval",
    "model_source",
    "model_path",
    "save_metadata",
    "output_dir",
    "output_name",
)

#: Reference images the app can open. Same list the upload route enforces.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp")


def synchronized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


def normalize_name(raw: str | None, fallback: str = "Untitled project") -> str:
    """A single line, trimmed, length-capped. Names are shown in a narrow sidebar."""
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    return (text[:MAX_NAME] or fallback)


def sanitize_session(raw: Any) -> dict[str, Any]:
    """Keep only known, JSON-safe session fields."""
    if not isinstance(raw, dict):
        return {}
    session: dict[str, Any] = {}
    for key in SESSION_FIELDS:
        if key not in raw:
            continue
        value = raw[key]
        if value is None or isinstance(value, (str, int, float, bool)):
            session[key] = value
        elif isinstance(value, list):
            session[key] = [item for item in value if isinstance(item, (str, int, float, bool, type(None)))]
    return session


class ProjectError(RuntimeError):
    """A project could not be created, saved or removed."""


class ProjectStore:
    """Projects table plus the files each one owns.

    The connection is the Library's, not a second one to the same file: both tables live in
    ``library.db``, and two writers on one SQLite file lock each other out.
    """

    def __init__(self, connection: sqlite3.Connection, root: Path, lock: threading.RLock | None = None) -> None:
        self._lock = lock or threading.RLock()
        self._conn = connection
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        for statement in SCHEMA.split(";"):
            if statement.strip():
                self._conn.execute(statement)
        self._conn.commit()

    # ---- paths ------------------------------------------------------------
    def project_dir(self, project_id: str) -> Path:
        return self.root / project_id

    def refs_dir(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "refs"

    # ---- reads ------------------------------------------------------------
    @synchronized
    def _row(self, project_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return self._to_project(row) if row else None

    def _to_project(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["session"] = json.loads(item.pop("session_json") or "{}")
        item["references"] = json.loads(item.pop("references_json") or "[]")
        item["reference_count"] = len(item["references"])
        # Reported here as well as in get(), so every project the UI holds has the same
        # shape: a list response that omits a field the detail response carries will
        # eventually be rendered by something that expects it.
        item["missing_references"] = [path for path in item["references"] if not Path(path).is_file()]
        return item

    def get(self, project_id: str) -> dict[str, Any] | None:
        project = self._row(project_id)
        if project is None:
            return None
        # A reference whose copy has gone missing is reported rather than silently restored:
        # the UI shows the gap instead of pretending the image is still there.
        project["missing_references"] = [
            path for path in project["references"] if not Path(path).is_file()
        ]
        return project

    def require(self, project_id: str) -> dict[str, Any]:
        project = self.get(project_id)
        if project is None:
            raise ProjectError(f"no project with id {project_id!r}")
        return project

    @synchronized
    def list(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [self._to_project(row) for row in rows]

    def counts(self, project_id: str) -> dict[str, int]:
        """How many generations belong to this project, by status."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM generations WHERE project_id = ? GROUP BY status",
                (project_id,),
            ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    # ---- writes -----------------------------------------------------------
    @synchronized
    def create(self, name: str | None = None, session: Any = None, prompt: str | None = None) -> dict[str, Any]:
        existing = [row["id"] for row in self._conn.execute("SELECT id FROM projects").fetchall()]
        if len(existing) >= 200:
            raise ProjectError("There are already 200 projects. Delete one before making another.")

        index = 1
        project_id = f"p{index}"
        while project_id in existing:
            index += 1
            project_id = f"p{index}"

        now = time.time()
        clean = sanitize_session(session)
        self._conn.execute(
            """
            INSERT INTO projects (id, name, created_at, updated_at, prompt, session_json, references_json)
            VALUES (?, ?, ?, ?, ?, ?, '[]')
            """,
            (
                project_id,
                normalize_name(name, f"Project {len(existing) + 1}"),
                now,
                now,
                str(prompt if prompt is not None else clean.get("prompt", ""))[:4000],
                json.dumps(clean),
            ),
        )
        self._conn.commit()
        return self.require(project_id)

    @synchronized
    def rename(self, project_id: str, name: str) -> dict[str, Any]:
        if self._row(project_id) is None:
            raise ProjectError(f"no project with id {project_id!r}")
        self._conn.execute(
            "UPDATE projects SET name = ?, updated_at = ? WHERE id = ?",
            (normalize_name(name), time.time(), project_id),
        )
        self._conn.commit()
        return self.require(project_id)

    @synchronized
    def save_session(
        self,
        project_id: str,
        session: Any = None,
        references: list[str] | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Write the whole working state. Both halves are optional and independent."""
        current = self.require(project_id)
        clean = sanitize_session(session) if session is not None else current["session"]
        if references is None:
            stored = list(current["references"])
        else:
            stored = self._sync_references(project_id, references)

        now = time.time()
        self._conn.execute(
            """
            UPDATE projects
               SET name = ?, updated_at = ?, prompt = ?, session_json = ?, references_json = ?
             WHERE id = ?
            """,
            (
                normalize_name(name) if name else current["name"],
                now,
                str(clean.get("prompt", current["prompt"]))[:4000],
                json.dumps(clean),
                json.dumps(stored),
                project_id,
            ),
        )
        self._conn.commit()
        return self.require(project_id)

    def _sync_references(self, project_id: str, incoming: list[str]) -> list[str]:
        """Copy new references in, keep known ones, drop the copies that were removed.

        Idempotent: saving the same list twice copies nothing the second time, because an
        image already inside this project's own refs directory is recognised by path.
        """
        root = self.refs_dir(project_id)
        root.mkdir(parents=True, exist_ok=True)
        keep: list[str] = []
        seen: set[str] = set()

        for raw in list(incoming or [])[:10]:
            source = Path(str(raw)).expanduser()
            if not source.is_file():
                continue
            try:
                resolved = source.resolve()
            except OSError:
                continue
            # Already ours: the session was restored from this project, or it was saved before.
            if root.resolve() == resolved.parent or root.resolve() in resolved.parents:
                if str(resolved) not in seen and resolved.is_file():
                    seen.add(str(resolved))
                    keep.append(str(resolved))
                continue
            if str(resolved) in seen:
                continue
            if source.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            target = root / f"{len(keep) + 1:02d}{source.suffix.lower()}"
            try:
                # copy2 rather than move: the original is the user's file and stays theirs.
                shutil.copy2(resolved, target)
            except OSError as exc:
                raise ProjectError(f"Could not save the reference image {source.name}: {exc}") from exc
            seen.add(str(target))
            keep.append(str(target))

        # Delete copies this session no longer refers to, but only ones under our own refs
        # directory: a path the caller supplied from elsewhere is not ours to remove.
        for stale in root.glob("*"):
            if stale.is_file() and str(stale) not in seen:
                try:
                    stale.unlink()
                except OSError:
                    pass
        return keep

    @synchronized
    def delete(self, project_id: str) -> dict[str, Any]:
        if self._row(project_id) is None:
            raise ProjectError(f"no project with id {project_id!r}")
        # Generations survive: the images and their metadata are the user's, and losing the
        # project's name must not lose the results. They are simply unfiled.
        self._conn.execute("UPDATE generations SET project_id = NULL WHERE project_id = ?", (project_id,))
        self._conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        self._conn.commit()
        shutil.rmtree(self.project_dir(project_id), ignore_errors=True)
        return {"deleted": project_id}

    # ---- reporting --------------------------------------------------------
    def size_report(self) -> dict[str, Any]:
        """Total and per-project bytes on disk, for the projects view."""
        with self._lock:
            rows = self._conn.execute("SELECT id FROM projects").fetchall()
        per_project: dict[str, int] = {}
        for row in rows:
            refs = self.refs_dir(row["id"])
            if refs.is_dir():
                per_project[row["id"]] = dir_size_bytes(refs)
        total = sum(per_project.values())
        return {
            "dir": str(self.root),
            "total_size_bytes": total,
            "total_size_human": human_bytes(total),
            "per_project_bytes": per_project,
        }

    @synchronized
    def close(self) -> None:
        self._conn.close()