from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from refract_backend.app import create_app
from test_api import make_reference, submit_generate, wait_for


def avatar(client, paths, **overrides):
    body = {"name": "Alex", "handle": "alex", "description": "short dark hair", "references": [str(path) for path in paths]}
    body.update(overrides)
    response = client.post("/api/avatars", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_avatar_copies_images_and_persists_across_restart(client, tmp_path, token):
    source = make_reference(tmp_path)
    saved = avatar(client, [source])
    source.unlink()
    assert Path(saved["references"][0]).is_file()
    assert client.get("/api/files", params={"path": saved["references"][0], "token": token}).status_code == 200
    with TestClient(create_app(token, force_mock=True)) as second:
        second.headers.update({"X-Refract-Token": token})
        assert second.get("/api/avatars").json()["avatars"] == [saved]


def test_tagged_generation_preserves_prompt_and_binds_scene_and_subject(client, tmp_path):
    scene = make_reference(tmp_path, "scene.png")
    saved = avatar(client, [make_reference(tmp_path, "face.png"), make_reference(tmp_path, "profile.png")])
    prompt = "Add @Alex to the scene; keep @alex's face and make the light warmer"
    job = submit_generate(client, prompt=prompt, reference_paths=[str(scene)], steps=1)
    assert job["payload"]["prompt"] == prompt
    assert len(job["references"]) == 3
    assert len(job["payload"]["avatar_bindings"]) == 1
    assert job["payload"]["avatar_bindings"][0]["images"] == [2, 3]
    assert "reference images 2, 3" in job["payload"]["resolved_prompt"]
    assert "@" not in job["payload"]["resolved_prompt"]
    assert client.delete(f"/api/avatars/{saved['id']}").status_code == 200
    finished = wait_for(client, job["id"])
    assert finished["status"] == "done"
    assert all(Path(path).is_file() for path in finished["references"])
    replay = client.post(f"/api/library/{job['id']}/replay")
    assert replay.status_code == 202, replay.text
    assert replay.json()["payload"]["resolved_prompt"] == job["payload"]["resolved_prompt"]
    assert wait_for(client, replay.json()["id"])["status"] == "done"
    entry = next(item for item in client.get("/api/library").json()["items"] if item["id"] == job["id"])
    assert entry["prompt"] == prompt


def test_multiple_avatar_order_and_reference_limit(client, tmp_path):
    avatar(client, [make_reference(tmp_path, "a.png")])
    avatar(client, [make_reference(tmp_path, "b.png")], name="Dog", handle="dog")
    job = submit_generate(client, prompt="@dog beside @alex and @dog", steps=1)
    assert [binding["handle"] for binding in job["payload"]["avatar_bindings"]] == ["dog", "alex"]
    refs = [str(make_reference(tmp_path, f"scene-{i}.png")) for i in range(9)]
    response = client.post("/api/jobs", json={"kind": "generate", "payload": {"prompt": "@dog and @alex", "reference_paths": refs}})
    assert response.status_code == 422
    assert "11 images" in response.text


def test_profile_edit_is_atomic_and_does_not_change_queued_reference_copies(client, tmp_path):
    source = make_reference(tmp_path, "old.png")
    saved = avatar(client, [source])
    job = submit_generate(client, prompt="@alex at the beach", steps=1)
    replacement = tmp_path / "new.png"
    Image.new("RGB", (256, 256), (10, 240, 10)).save(replacement)
    body = {**saved, "name": "Alex revised", "references": [str(replacement)]}
    updated = client.put(f"/api/avatars/{saved['id']}", json=body)
    assert updated.status_code == 200
    assert Image.open(job["references"][0]).getpixel((0, 0)) == (200, 40, 90)
    body["references"] = ["/missing-image.png"]
    assert client.put(f"/api/avatars/{saved['id']}", json=body).status_code == 400
    assert client.get("/api/avatars").json()["avatars"] == [updated.json()]


@pytest.mark.parametrize("patch", [{"handle": "bad name"}, {"name": ""}, {"references": []}, {"description": "x" * 501}])
def test_invalid_profiles_are_rejected(client, tmp_path, patch):
    body = {"name": "Alex", "handle": "alex", "references": [str(make_reference(tmp_path))], **patch}
    assert client.post("/api/avatars", json=body).status_code == 400


def test_duplicate_handles_missing_tags_and_auth(client, tmp_path):
    source = make_reference(tmp_path)
    avatar(client, [source])
    assert client.post("/api/avatars", json={"name": "Other", "handle": "ALEX", "references": [str(source)]}).status_code == 400
    assert client.get("/api/avatars", headers={"X-Refract-Token": "wrong"}).status_code == 401
    assert client.delete("/api/avatars/missing").status_code == 404
    response = client.post("/api/jobs", json={"kind": "generate", "payload": {"prompt": "@missing in a room"}})
    assert response.status_code == 422 and "Unknown avatar" in response.text
    job = submit_generate(client, prompt="Put contact@example.com on a sign", steps=1)
    assert job["payload"]["resolved_prompt"] is None
