"""SQLite index of every generation, so the Library can replay any of them exactly.

One row per job (not per image): a job's outputs and the parameters that produced them
belong together, and replay means sending the same parameters back. The row stores the
sidecar metadata verbatim, including the model source and revision, because that is what
makes a result reproducible months later.

``project_id`` tags a run with the project it was made in. It is nullable and deliberately
not a foreign key: deleting a project unfiles its results but never deletes them, and an
SQLite-level cascade would be an easy way to lose a user's images by accident.
"""

from __future__ import annotations

import json
import sqlite3
import time
import threading
from functools import wraps
from pathlib import Path
from typing import Any

from .paths import library_db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS generations (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    prompt TEXT NOT NULL,
    negative_prompt TEXT,
    params_json TEXT NOT NULL,
    references_json TEXT NOT NULL DEFAULT '[]',
    outputs_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    duration_s REAL,
    peak_memory_gb REAL,
    model_source TEXT,
    model_path TEXT,
    quantize INTEGER,
    width INTEGER,
    height INTEGER,
    seeds_json TEXT NOT NULL DEFAULT '[]',
    favorite INTEGER NOT NULL DEFAULT 0,
    project_id TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_generations_created ON generations (created_at DESC);
"""

#: A database created before projects existed has no ``project_id``. CREATE TABLE IF NOT
#: EXISTS leaves such a table untouched, so the column is added separately — and before the
#: index below, which would otherwise fail against a column that is not there yet.
MIGRATIONS = (
    "ALTER TABLE generations ADD COLUMN project_id TEXT",
)

#: Applied after the migrations, so every column an index names is guaranteed to exist.
INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_generations_project ON generations (project_id, created_at DESC)",
)


def synchronized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


class Library:
    def __init__(self, path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self.path = path or library_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._apply_schema()
        self._conn.commit()

    def _apply_schema(self) -> None:
        """Create the schema, tolerating a database written by an older build."""
        statements = [part.strip() for part in SCHEMA.split(";") if part.strip()]
        statements += list(MIGRATIONS)
        statements += list(INDEXES)
        for statement in statements:
            # ALTER TABLE ADD COLUMN is not idempotent, so a column that already exists is
            # left alone rather than raising and taking the app down on upgrade.
            try:
                self._conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc):
                    raise

    @property
    def lock(self) -> threading.RLock:
        """The write lock, shared with any other store on this connection."""
        return self._lock

    @property
    def connection(self) -> sqlite3.Connection:
        """The live connection, so another store can share it rather than opening a second
        one to the same file — two writers on one SQLite file is a locking hazard."""
        return self._conn

    # ---- writes -----------------------------------------------------------
    @synchronized
    def record_start(
        self,
        job_id: str,
        prompt: str,
        negative_prompt: str | None,
        params: dict,
        references: list[str],
        seeds: list[int],
        model_source: str,
        model_path: str | None,
        quantize: int | None,
        project_id: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO generations (
                id, created_at, prompt, negative_prompt, params_json, references_json,
                outputs_json, status, model_source, model_path, quantize, seeds_json, project_id
            ) VALUES (?, ?, ?, ?, ?, ?, '[]', 'queued', ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                time.time(),
                prompt,
                negative_prompt,
                json.dumps(params),
                json.dumps(references),
                model_source,
                model_path,
                quantize,
                json.dumps(seeds),
                project_id,
            ),
        )
        self._conn.commit()

    @synchronized
    def record_finish(
        self,
        job_id: str,
        status: str,
        outputs: list[str],
        duration_s: float | None = None,
        peak_memory_gb: float | None = None,
        width: int | None = None,
        height: int | None = None,
        error: str | None = None,
        model_path: str | None = None,
        seeds: list[int] | None = None,
    ) -> None:
        # model_path arrives only once the run resolved its source: a `mlx-q4` request
        # carries no path, the staged checkpoint it actually loaded is only known then, and
        # recording it is what makes "which weights produced this file?" answerable later.
        self._conn.execute(
            """
            UPDATE generations
               SET status = ?, outputs_json = ?, duration_s = ?, peak_memory_gb = ?,
                   width = ?, height = ?, error = ?, model_path = COALESCE(?, model_path),
                   seeds_json = COALESCE(?, seeds_json)
             WHERE id = ?
            """,
            (
                status,
                json.dumps(outputs),
                duration_s,
                peak_memory_gb,
                width,
                height,
                error,
                model_path,
                json.dumps(seeds) if seeds is not None else None,
                job_id,
            ),
        )
        self._conn.commit()

    @synchronized
    def set_status(self, job_id: str, status: str) -> None:
        self._conn.execute("UPDATE generations SET status = ? WHERE id = ?", (status, job_id))
        self._conn.commit()

    @synchronized
    def set_favorite(self, job_id: str, favorite: bool) -> None:
        self._conn.execute("UPDATE generations SET favorite = ? WHERE id = ?", (1 if favorite else 0, job_id))
        self._conn.commit()

    @synchronized
    def delete(self, job_id: str) -> None:
        self._conn.execute("DELETE FROM generations WHERE id = ?", (job_id,))
        self._conn.commit()

    # ---- reads ------------------------------------------------------------
    def _row_to_item(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key in ("params", "references", "outputs", "seeds"):
            item[key] = json.loads(item.pop(f"{key}_json", "[]" if key != "params" else "{}"))
        item["favorite"] = bool(item["favorite"])
        return item

    @synchronized
    def list(
        self,
        limit: int = 500,
        offset: int = 0,
        search: str | None = None,
        favorites_only: bool = False,
        model_source: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses, args = [], []
        if search:
            clauses.append("prompt LIKE ?")
            args.append(f"%{search}%")
        if favorites_only:
            clauses.append("favorite = 1")
        if model_source:
            clauses.append("model_source = ?")
            args.append(model_source)
        if project_id:
            clauses.append("project_id = ?")
            args.append(project_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM generations {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    @synchronized
    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM generations WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_item(row) if row else None

    @synchronized
    def all_output_paths(self) -> list[str]:
        paths: list[str] = []
        for row in self._conn.execute("SELECT outputs_json FROM generations").fetchall():
            paths.extend(json.loads(row["outputs_json"]))
        return paths

    @synchronized
    def close(self) -> None:
        self._conn.close()
