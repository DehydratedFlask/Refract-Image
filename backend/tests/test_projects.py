"""Projects: the persistence behind "put this on hold and come back to it".

The interesting behaviour is all in what is *not* obvious from the API shape:

* an unknown field in a saved session is dropped rather than persisted, so a project written
  by a newer build cannot smuggle a field an older one would misread;
* saving the same references twice copies nothing the second time, because the copies live
  inside the project and are recognised by path;
* the user's original images are never moved or deleted, only ever read;
* deleting a project unfiles its results but never deletes them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from refract_backend.paths import projects_dir


@pytest.fixture()
def image(tmp_path: Path) -> Path:
    from PIL import Image

    path = tmp_path / "original.png"
    Image.new("RGB", (32, 32), (200, 40, 40)).save(path)
    return path


def test_no_projects_at_the_start(client):
    payload = client.get("/api/projects").json()
    assert payload["projects"] == []
    assert payload["storage"]["total_size_bytes"] == 0


def test_creating_a_project_gives_it_an_id_and_a_name(client):
    project = client.post("/api/projects", json={"name": "Album covers"}).json()
    assert project["id"]
    assert project["name"] == "Album covers"
    assert project["references"] == []
    assert project["generation_count"] == 0


def test_a_long_name_is_capped_and_whitespace_collapsed(client):
    project = client.post("/api/projects", json={"name": "  Golden   hour\n  edit  "}).json()
    assert project["name"] == "Golden hour edit"
    long = client.post("/api/projects", json={"name": "x" * 200}).json()
    assert len(long["name"]) == 60


def test_an_untitled_project_still_gets_a_name(client):
    assert client.post("/api/projects", json={}).json()["name"]


def test_a_session_round_trips_exactly(client):
    project = client.post("/api/projects", json={"name": "Portraits"}).json()
    session = {"prompt": "warmer light", "steps": 32, "width": 768, "model_source": "flux2-klein-4b"}
    client.put(f"/api/projects/{project['id']}", json={"session": session})

    loaded = client.get(f"/api/projects/{project['id']}").json()
    for key, value in session.items():
        assert loaded["session"][key] == value


def test_unknown_session_fields_are_dropped(client):
    project = client.post("/api/projects", json={"name": "P"}).json()
    saved = client.put(f"/api/projects/{project['id']}", json={"session": {"prompt": "hi", "from_the_future": 1}}).json()
    assert "from_the_future" not in saved["session"]
    assert saved["session"]["prompt"] == "hi"


def test_references_are_copied_into_the_project(client, image):
    project = client.post("/api/projects", json={"name": "Copies"}).json()
    saved = client.put(f"/api/projects/{project['id']}", json={"references": [str(image)]}).json()

    stored = saved["references"]
    assert len(stored) == 1
    assert Path(stored[0]).is_file()
    # Under the project's own directory, and no longer the original path.
    assert projects_dir() in Path(stored[0]).parents
    assert stored[0] != str(image)
    # The user's file is read, never moved.
    assert image.is_file()


def test_saving_the_same_references_again_copies_nothing(client, image):
    project = client.post("/api/projects", json={"name": "Idempotent"}).json()
    first = client.put(f"/api/projects/{project['id']}", json={"references": [str(image)]}).json()
    again = client.put(f"/api/projects/{project['id']}", json={"references": first["references"]}).json()
    assert again["references"] == first["references"]


def test_removing_a_reference_deletes_only_its_copy(client, image):
    second = image.with_name("second.png")
    from PIL import Image

    Image.new("RGB", (32, 32), (0, 0, 200)).save(second)

    project = client.post("/api/projects", json={"name": "Two"}).json()
    both = client.put(f"/api/projects/{project['id']}", json={"references": [str(image), str(second)]}).json()
    assert len(both["references"]) == 2

    one = client.put(f"/api/projects/{project['id']}", json={"references": both["references"][:1]}).json()
    dropped = Path(both["references"][1])
    assert not dropped.exists()
    assert Path(one["references"][0]).is_file()
    # Both originals survive.
    assert image.is_file() and second.is_file()


def test_a_missing_reference_copy_is_reported(client, image):
    project = client.post("/api/projects", json={"name": "Vanishing"}).json()
    saved = client.put(f"/api/projects/{project['id']}", json={"references": [str(image)]}).json()
    Path(saved["references"][0]).unlink()
    assert client.get(f"/api/projects/{project['id']}").json()["missing_references"] == saved["references"]


def test_a_non_image_reference_is_skipped(client, tmp_path):
    document = tmp_path / "notes.txt"
    document.write_text("hello")
    project = client.post("/api/projects", json={"name": "Text"}).json()
    assert client.put(f"/api/projects/{project['id']}", json={"references": [str(document)]}).json()["references"] == []


def test_renaming_collapses_whitespace(client):
    project = client.post("/api/projects", json={"name": "Before"}).json()
    renamed = client.put(f"/api/projects/{project['id']}", json={"name": "  After   name "}).json()
    assert renamed["name"] == "After name"


def test_renaming_does_not_erase_the_session(client):
    project = client.post("/api/projects", json={"name": "Keeps"}).json()
    client.put(f"/api/projects/{project['id']}", json={"session": {"prompt": "still here", "steps": 12}})
    renamed = client.put(f"/api/projects/{project['id']}", json={"name": "Renamed"}).json()
    assert renamed["session"]["prompt"] == "still here"
    assert renamed["session"]["steps"] == 12


def test_saving_one_half_leaves_the_other_alone(client, image):
    project = client.post("/api/projects", json={"name": "Both halves"}).json()
    client.put(f"/api/projects/{project['id']}", json={"session": {"prompt": "kept"}, "references": [str(image)]})
    after = client.put(f"/api/projects/{project['id']}", json={"session": {"prompt": "changed"}}).json()
    assert after["references"], "a session-only save must not clear the stored references"

    after_refs = client.put(f"/api/projects/{project['id']}", json={"references": []}).json()
    assert after_refs["session"]["prompt"] == "changed"


def test_deleting_removes_the_project_and_its_copies(client, image):
    project = client.post("/api/projects", json={"name": "Doomed"}).json()
    saved = client.put(f"/api/projects/{project['id']}", json={"references": [str(image)]}).json()
    directory = projects_dir() / project["id"]

    assert client.delete(f"/api/projects/{project['id']}").json() == {"deleted": project["id"]}
    assert not directory.exists()
    assert not Path(saved["references"][0]).exists()
    assert image.is_file(), "the user's original is not the project's to delete"


def test_deleting_a_project_keeps_its_generations(client):
    """A result is the user's work; losing the project's name must not lose the image."""
    project = client.post("/api/projects", json={"name": "Keeper"}).json()
    job = client.post(
        "/api/jobs",
        json={"kind": "generate", "payload": {"prompt": "a cat", "steps": 1, "project_id": project["id"]}},
    ).json()

    client.delete(f"/api/projects/{project['id']}")

    item = client.get(f"/api/library?search=a cat").json()["items"][0]
    assert item["id"] == job["id"]
    assert item["project_id"] is None


def test_an_unknown_project_is_a_404(client):
    assert client.get("/api/projects/nope").status_code == 404
    assert client.put("/api/projects/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/projects/nope").status_code == 404


def test_the_library_can_be_filtered_to_one_project(client):
    first = client.post("/api/projects", json={"name": "One"}).json()
    client.post("/api/projects", json={"name": "Two"}).json()
    client.post("/api/jobs", json={"kind": "generate", "payload": {"prompt": "in one", "steps": 1, "project_id": first["id"]}})
    client.post("/api/jobs", json={"kind": "generate", "payload": {"prompt": "in two", "steps": 1}})
    client.post("/api/jobs", json={"kind": "generate", "payload": {"prompt": "in one too", "steps": 1, "project_id": first["id"]}})

    prompts = [item["prompt"] for item in client.get(f"/api/library?project_id={first['id']}").json()["items"]]
    assert sorted(prompts) == ["in one", "in one too"]

    assert len(client.get("/api/library").json()["items"]) == 3


def test_project_counts_track_their_generations(client):
    project = client.post("/api/projects", json={"name": "Counted"}).json()
    client.post("/api/jobs", json={"kind": "generate", "payload": {"prompt": "one", "steps": 1, "project_id": project["id"]}})

    listed = {entry["id"]: entry for entry in client.get("/api/projects").json()["projects"]}
    assert listed[project["id"]]["generation_count"] == 1


def test_the_list_and_the_detail_carry_the_same_fields(client):
    """The UI holds projects from both responses and renders them the same way, so a field
    present in one and absent in the other is a shape mismatch waiting to throw."""
    client.post("/api/projects", json={"name": "Shape"})
    listed = client.get("/api/projects").json()["projects"][0]
    detail = client.get(f"/api/projects/{listed['id']}").json()
    assert set(listed) == set(detail)


def test_a_listed_project_reports_its_missing_references(client, image):
    project = client.post("/api/projects", json={"name": "Listed"}).json()
    saved = client.put(f"/api/projects/{project['id']}", json={"references": [str(image)]}).json()
    Path(saved["references"][0]).unlink()
    listed = {entry["id"]: entry for entry in client.get("/api/projects").json()["projects"]}
    assert listed[project["id"]]["missing_references"] == saved["references"]


def test_storage_reports_the_bytes_held_by_references(client, image):
    project = client.post("/api/projects", json={"name": "Sized"}).json()
    client.put(f"/api/projects/{project['id']}", json={"references": [str(image)]})
    storage = client.get("/api/projects").json()["storage"]
    assert storage["total_size_bytes"] > 0
    assert storage["per_project_bytes"][project["id"]] > 0


def test_a_database_written_before_projects_still_opens(tmp_path):
    """The migration path: an existing library.db gains a column rather than an error."""
    import sqlite3

    from refract_backend.library import Library

    db = tmp_path / "library.db"
    legacy = sqlite3.connect(db)
    legacy.execute(
        """
        CREATE TABLE generations (
            id TEXT PRIMARY KEY, created_at REAL NOT NULL, prompt TEXT NOT NULL,
            negative_prompt TEXT, params_json TEXT NOT NULL, references_json TEXT NOT NULL DEFAULT '[]',
            outputs_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL, duration_s REAL,
            peak_memory_gb REAL, model_source TEXT, model_path TEXT, quantize INTEGER,
            width INTEGER, height INTEGER, seeds_json TEXT NOT NULL DEFAULT '[]',
            favorite INTEGER NOT NULL DEFAULT 0, error TEXT
        )
        """
    )
    legacy.execute("INSERT INTO generations (id, created_at, prompt, params_json, status) VALUES ('old', 1, 'kept', '{}', 'done')")
    legacy.commit()
    legacy.close()

    library = Library(db)

    assert library.get("old")["prompt"] == "kept"
    assert "project_id" in {key for key in library.get("old")}
    # Opening it twice must not trip over its own migration.
    library.close()
    assert Library(db).get("old")["id"] == "old"

def test_sessions_preserve_independent_drafts_and_references(client, image):
    project = client.post("/api/projects", json={"name": "Sessions"}).json()
    url = f"/api/projects/{project['id']}"
    first = client.put(url, json={"session": {"prompt": "first", "steps": 12}, "references": [str(image)]}).json()
    first_id = first["active_session_id"]
    first_reference = first["references"][0]
    second = client.post(url + "/sessions").json()
    second_id = second["active_session_id"]
    assert second_id != first_id
    assert second["session"]["prompt"] == ""
    assert second["session"]["steps"] == 12
    assert second["references"] == []
    assert Path(first_reference).is_file()
    client.put(url, json={"session_id": second_id, "session": {"prompt": "second", "steps": 20}, "references": [str(image)]})
    restored = client.put(url, json={"session_id": first_id}).json()
    assert restored["session"] == {"prompt": "first", "steps": 12}
    assert restored["references"] == [first_reference]
    sessions = {entry["id"]: entry for entry in restored["sessions"]}
    assert sessions[second_id]["session"]["prompt"] == "second"
    assert sessions[second_id]["references"] != restored["references"]
    client.put(url, json={"references": []})
    assert Path(sessions[second_id]["references"][0]).is_file()
    assert client.put(url, json={"session_id": "../nope"}).status_code == 400


def test_legacy_projects_gain_a_session_without_losing_work(tmp_path):
    import sqlite3
    from refract_backend.projects import ProjectStore, SCHEMA
    db = tmp_path / "legacy.db"
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    connection.execute("INSERT INTO projects (id, name, created_at, updated_at, session_json, references_json) VALUES ('p1', 'Legacy', 1, 1, ?, '[]')", ('{"prompt":"kept", "steps":17}',))
    connection.commit()
    store = ProjectStore(connection, tmp_path / "projects")
    migrated = store.require("p1")
    assert migrated["sessions"][0]["session"] == {"prompt": "kept", "steps": 17}
    store.create_session("p1")
    store.close()
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    reopened = ProjectStore(connection, tmp_path / "projects")
    assert len(reopened.require("p1")["sessions"]) == 2
    assert reopened.save_session("p1", session_id="s1")["session"]["prompt"] == "kept"
    reopened.close()


def test_generation_remembers_its_project_session(client):
    project = client.post("/api/projects", json={"name": "Filed"}).json()
    session = client.post(f"/api/projects/{project['id']}/sessions").json()
    session_id = session["active_session_id"]
    job = client.post("/api/jobs", json={"kind": "generate", "payload": {
        "prompt": "session result", "steps": 1, "project_id": project["id"], "project_session_id": session_id,
    }}).json()
    assert job["payload"]["project_session_id"] == session_id
    item = client.get(f"/api/library?project_id={project['id']}").json()["items"][0]
    assert item["params"]["project_session_id"] == session_id
