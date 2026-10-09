"""Saved subjects and deterministic prompt-to-reference binding."""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path

from PIL import Image

from .schemas import GenerateRequest, MAX_REFERENCES

MENTION = re.compile(r"(?<![\w@])@([a-zA-Z0-9_-]+)")


class AvatarError(ValueError):
    pass


class AvatarStore:
    def __init__(self, connection, root: Path, lock):
        self.db, self.root, self.lock = connection, root, lock
        with lock:
            self.db.execute("""CREATE TABLE IF NOT EXISTS avatars (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, handle TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL, references_json TEXT NOT NULL, updated_at REAL NOT NULL
            )""")
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(avatars)")}
            if "reference_roles_json" not in columns:
                self.db.execute("ALTER TABLE avatars ADD COLUMN reference_roles_json TEXT NOT NULL DEFAULT '[]'")
            self.db.commit()

    def list(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM avatars ORDER BY name COLLATE NOCASE").fetchall()
            return [self._decode(row) for row in rows]

    def _decode(self, row) -> dict:
        value = dict(row)
        value["references"] = json.loads(value.pop("references_json"))
        roles = json.loads(value.pop("reference_roles_json"))
        value["reference_roles"] = roles if len(roles) == len(value["references"]) else ["reference"] * len(value["references"])
        return value

    def save(self, body: dict, avatar_id: str | None = None) -> dict:
        name = body.get("name", "")
        handle = body.get("handle", "")
        description = body.get("description", "")
        references = body.get("references", [])
        roles = body.get("reference_roles", ["reference"] * len(references) if isinstance(references, list) else [])
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            raise AvatarError("Give the avatar a name of 1–60 characters")
        if not isinstance(handle, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}", handle):
            raise AvatarError("Use a handle of 1–40 letters, numbers, hyphens or underscores")
        if not isinstance(description, str) or len(description) > 500:
            raise AvatarError("Keep the description under 500 characters")
        if not isinstance(references, list) or not 1 <= len(references) <= MAX_REFERENCES:
            raise AvatarError("Add 1–10 reference images for this avatar")
        if not all(isinstance(path, str) for path in references):
            raise AvatarError("Reference paths must be strings")
        if not isinstance(roles, list) or len(roles) != len(references) or any(role not in ("face", "body", "reference") for role in roles):
            raise AvatarError("Label each image as a face, body or additional reference")
        with self.lock:
            if avatar_id and not self.db.execute("SELECT id FROM avatars WHERE id=?", (avatar_id,)).fetchone():
                raise AvatarError("avatar not found")
            duplicate = self.db.execute("SELECT id FROM avatars WHERE handle=?", (handle.lower(),)).fetchone()
            if duplicate and duplicate[0] != avatar_id:
                raise AvatarError("That @handle is already in use")
            avatar_id = avatar_id or uuid.uuid4().hex[:12]
            # Stage a complete new image set before replacing an existing profile.
            folder = self.root / avatar_id / uuid.uuid4().hex[:12]
            saved = []
            saved_roles = []
            seen = set()
            try:
                folder.mkdir(parents=True, exist_ok=True)
                for raw, role in zip(references, roles):
                    if raw in seen:
                        continue
                    seen.add(raw)
                    source = Path(raw).expanduser().resolve()
                    with Image.open(source) as image:
                        image.verify()
                    target = folder / f"ref-{len(saved) + 1}{source.suffix.lower()}"
                    shutil.copy2(source, target)
                    saved.append(str(target))
                    saved_roles.append(role)
                now = time.time()
                self.db.execute("""INSERT OR REPLACE INTO avatars
                    (id, name, handle, description, references_json, updated_at, reference_roles_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (avatar_id, name.strip(), handle.lower(), description.strip(), json.dumps(saved), now, json.dumps(saved_roles)))
                self.db.commit()
            except Exception as exc:
                shutil.rmtree(folder, ignore_errors=True)
                if isinstance(exc, AvatarError):
                    raise
                raise AvatarError(f"Could not save avatar images: {exc}") from exc
            # Job submissions use their own snapshots, so old profile copies can go.
            for old in folder.parent.iterdir():
                if old != folder:
                    shutil.rmtree(old, ignore_errors=True)
            return {"id": avatar_id, "name": name.strip(), "handle": handle.lower(),
                    "description": description.strip(), "references": saved, "reference_roles": saved_roles, "updated_at": now}

    def delete(self, avatar_id: str) -> None:
        with self.lock:
            row = self.db.execute("SELECT id FROM avatars WHERE id=?", (avatar_id,)).fetchone()
            if not row:
                raise AvatarError("avatar not found")
            self.db.execute("DELETE FROM avatars WHERE id=?", (avatar_id,))
            self.db.commit()
            shutil.rmtree(self.root / avatar_id, ignore_errors=True)

    def resolve(self, request: GenerateRequest) -> GenerateRequest:
        with self.lock:
            profiles = {avatar["handle"]: avatar for avatar in self.list()}
            references = list(request.reference_paths)
            bindings = {}

            def replace(match):
                handle = match[1].lower()
                if handle not in profiles:
                    raise AvatarError(f"Unknown avatar @{match[1]}. Add it in Avatars or remove the tag")
                if handle not in bindings:
                    avatar = profiles[handle]
                    indices = []
                    for path in avatar["references"]:
                        if path not in references:
                            references.append(path)
                        indices.append(references.index(path) + 1)
                    bindings[handle] = {"id": avatar["id"], "name": avatar["name"],
                                        "handle": handle, "description": avatar["description"], "images": indices,
                                        "reference_roles": avatar["reference_roles"]}
                entry = bindings[handle]
                images = ", ".join(str(index) for index in entry["images"])
                detail = f"; {entry['description']}" if entry["description"] else ""
                for role in ("face", "body"):
                    role_images = [str(index) for index, label in zip(entry["images"], entry["reference_roles"]) if label == role]
                    if role_images:
                        detail += f"; {role} reference images {', '.join(role_images)}"
                return f"{entry['name']} (the subject in reference images {images}{detail})"

            resolved = MENTION.sub(replace, request.prompt)
            if len(references) > MAX_REFERENCES:
                raise AvatarError(f"Your references and tagged avatars total {len(references)} images; the model supports at most 10")
            return request.model_copy(update={"reference_paths": references,
                "resolved_prompt": resolved if bindings else None, "avatar_bindings": list(bindings.values())})
