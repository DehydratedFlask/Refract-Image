"""API tests that run the whole pipeline with the mock runner.

The mock is what makes these possible: the queue, progress events, reference copying,
Library bookkeeping, file streaming and replay are all exercised for real, and only the
tensor maths is swapped out.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from PIL import Image


def wait_for(client, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def make_reference(tmp_path: Path, name: str = "ref.png") -> Path:
    path = tmp_path / name
    Image.new("RGB", (256, 256), (200, 40, 90)).save(path)
    return path


def submit_generate(client, **overrides) -> dict:
    payload = {
        "prompt": "a test image",
        "steps": 6,
        "preview_interval": 2,
        "width": 512,
        "height": 512,
        "seed": 11,
    }
    payload.update(overrides)
    response = client.post("/api/jobs", json={"kind": "generate", "payload": payload})
    assert response.status_code == 202, response.text
    return response.json()


def test_health_requires_the_token(client, token):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health", headers={"X-Refract-Token": "nope"}).status_code == 401
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["mock"] is True


def test_system_reports_machine_facts(client):
    body = client.get("/api/system").json()
    assert body["total_ram_gb"] > 0
    assert body["free_disk_gb"] > 0
    assert body["architecture"]
    assert "models_dir" in body and "outputs_dir" in body


def test_sources_include_every_model_path(client):
    body = client.get("/api/sources").json()
    ids = {source["id"] for source in body["sources"]}
    assert ids == {"mlx-q4", "upstream-q4", "flux2-klein-4b", "prepared", "custom"}
    assert "disk" in body
    mlx = next(s for s in body["sources"] if s["id"] == "mlx-q4")
    assert mlx["repo_id"] == "abenzerps/Qwen-Image-2.1-Uncensored-GGUF"


def test_encoder_endpoint_keeps_families_separate(client):
    body = client.get("/api/encoders").json()
    families = {entry["family"] for entry in body["encoders"]}
    assert families == {"qwen21", "flux2"}
    # No key may be offered to both families: that is the substitution that cannot work.
    keys = [entry["key"] for entry in body["encoders"]]
    assert len(keys) == len(set(keys))
    assert "note" in body


def test_generation_job_produces_an_image_and_a_library_row(client, tmp_path, isolated_dirs):
    reference = make_reference(tmp_path)
    job = submit_generate(client, reference_paths=[str(reference)])
    assert job["status"] in ("queued", "running")

    finished = wait_for(client, job["id"])
    assert finished["status"] == "done", finished
    assert len(finished["outputs"]) == 1
    output = Path(finished["outputs"][0])
    assert output.is_file() and output.stat().st_size > 0
    assert output.parent == (isolated_dirs / "outputs").resolve()
    assert finished["width"] == 512 and finished["height"] == 512

    # Progress events must be monotonic so the UI can render a bar without reordering.
    seqs = [event["seq"] for event in finished["events"]]
    assert seqs == sorted(seqs)
    assert any(event.get("phase") == "denoise" for event in finished["events"])
    steps = [event["step"] for event in finished["events"] if event.get("step")]
    assert max(steps) == 6
    assert any(event.get("preview_path") for event in finished["events"])

    library = client.get("/api/library").json()["items"]
    assert len(library) == 1
    assert library[0]["id"] == job["id"]
    assert library[0]["status"] == "done"
    assert library[0]["references"] == [str(reference)]
    assert library[0]["params"]["prompt"] == "a test image"
    assert library[0]["seeds"] == finished["seeds"]


def test_generation_honours_the_chosen_output_folder(client, tmp_path):
    """The folder the user picks is where the file lands — not the default outputs dir."""
    chosen = tmp_path / "somewhere" / "else"
    job = submit_generate(client, output_dir=str(chosen))
    finished = wait_for(client, job["id"])
    assert finished["status"] == "done", finished

    output = Path(finished["outputs"][0])
    assert output.is_file()
    assert output.parent == chosen
    # It must not also be written to the service's own outputs folder.
    assert not (tmp_path / "outputs").exists()


def test_a_chosen_output_folder_stays_readable_and_deletable(client, tmp_path, token):
    """A folder outside every default root still works: viewed in the Library, and deleted."""
    chosen = tmp_path / "elsewhere"  # not under home, not the app's own tree
    job = submit_generate(client, output_dir=str(chosen))
    finished = wait_for(client, job["id"])
    assert finished["status"] == "done", finished
    image = Path(finished["outputs"][0])

    served = client.get("/api/files", params={"path": str(image), "token": token})
    assert served.status_code == 200, served.text
    assert len(served.content) > 0

    # "Delete the PNG too" has to reach into the chosen folder, not only its own tree.
    client.delete(f"/api/library/{job['id']}", params={"delete_files": True})
    assert not image.exists()


def test_a_strangers_file_is_still_not_served(client, tmp_path, token):
    """The allowance is scoped to what this service wrote, not to any path."""
    stranger = tmp_path / "not-ours.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(stranger)
    response = client.get("/api/files", params={"path": str(stranger), "token": token})
    assert response.status_code == 403


def test_generation_into_an_impossible_folder_fails_clearly(client, tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder")
    job = submit_generate(client, output_dir=str(blocker / "sub"))
    finished = wait_for(client, job["id"])
    assert finished["status"] == "failed"
    assert "Cannot save images to" in (finished["error"] or "")
    assert finished["outputs"] == []


def test_reference_images_are_copied_into_the_job(client, tmp_path, isolated_dirs):
    reference = make_reference(tmp_path)
    job = wait_for(client, submit_generate(client, reference_paths=[str(reference)])["id"])
    assert job["status"] == "done"


def test_missing_reference_fails_the_job_clearly(client):
    job = submit_generate(client, reference_paths=["/nope/definitely-missing.png"])
    finished = wait_for(client, job["id"])
    assert finished["status"] == "failed"
    assert "reference image not found" in (finished["error"] or "")


def test_invalid_parameters_are_rejected_before_a_job_starts(client):
    response = client.post("/api/jobs", json={"kind": "generate", "payload": {"prompt": "x", "width": 1000}})
    assert response.status_code == 422
    assert "multiple of 32" in json.dumps(response.json())


def test_cancelling_a_running_job(client):
    job = submit_generate(client, steps=99, preview_interval=0)
    time.sleep(0.4)
    response = client.post(f"/api/jobs/{job['id']}/cancel")
    assert response.status_code == 200
    finished = wait_for(client, job["id"], timeout=20)
    assert finished["status"] == "cancelled"
    library = client.get("/api/library").json()["items"]
    assert library[0]["status"] == "cancelled"


def test_events_endpoint_streams_for_the_token_holder(client, token):
    job = wait_for(client, submit_generate(client, steps=3, preview_interval=0)["id"])
    with client.stream("GET", f"/api/jobs/{job['id']}/events", params={"token": token}) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    assert "data: " in body
    assert '"final": true' in body
    assert client.get(f"/api/jobs/{job['id']}/events", params={"token": "wrong"}).status_code == 401


def test_library_search_favorite_and_delete(client, tmp_path):
    job = wait_for(client, submit_generate(client, prompt="a lonely lighthouse")["id"])
    assert client.get("/api/library", params={"search": "lighthouse"}).json()["count"] == 1
    assert client.get("/api/library", params={"search": "zeppelin"}).json()["count"] == 0

    assert client.post(f"/api/library/{job['id']}/favorite", json={"favorite": True}).json()["favorite"] is True
    assert client.get("/api/library", params={"favorites": True}).json()["count"] == 1

    output = Path(job["outputs"][0])
    client.delete(f"/api/library/{job['id']}", params={"delete_files": True})
    assert client.get("/api/library").json()["count"] == 0
    assert not output.exists()


def test_random_seed_is_recorded_for_exact_replay(client):
    first = wait_for(client, submit_generate(client, seed=None, steps=1)["id"])
    recorded = client.get("/api/library").json()["items"][0]
    assert recorded["seeds"] == first["seeds"]
    replayed = wait_for(client, client.post(f"/api/library/{first['id']}/replay").json()["id"])
    assert replayed["seeds"] == first["seeds"]


def test_replay_reuses_recorded_parameters(client, tmp_path):
    first = wait_for(client, submit_generate(client, steps=3, preview_interval=0, seed=99)["id"])
    response = client.post(f"/api/library/{first['id']}/replay")
    assert response.status_code == 202
    replayed = wait_for(client, response.json()["id"])
    assert replayed["status"] == "done"
    assert replayed["seeds"] == [99]


def test_file_endpoint_serves_outputs_but_refuses_arbitrary_paths(client, token):
    job = wait_for(client, submit_generate(client, steps=3, preview_interval=0)["id"])
    output = job["outputs"][0]
    served = client.get("/api/files", params={"path": output, "token": token})
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/png")

    blocked = client.get("/api/files", params={"path": "/etc/hosts", "token": token})
    assert blocked.status_code == 403
    assert client.get("/api/files", params={"path": output, "token": "wrong"}).status_code == 401


def test_validate_reports_an_incompatible_file(client, tmp_path):
    bogus = tmp_path / "bogus.safetensors"
    bogus.write_bytes(b"not safetensors")
    report = client.post("/api/validate", json={"path": str(bogus), "component": "transformer"}).json()
    assert report["ok"] is False
    assert report["errors"]


def test_original_reference_size_is_the_default(client, tmp_path):
    reference = tmp_path / "portrait.png"
    Image.new("RGB", (333, 517)).save(reference)
    finished = wait_for(client, submit_generate(client, reference_paths=[str(reference)], width=None, height=None)["id"])
    assert finished["status"] == "done", finished
    with Image.open(finished["outputs"][0]) as image:
        assert image.size == (333, 517)
    assert (finished["width"], finished["height"]) == (333, 517)


def test_delete_also_removes_metadata_but_not_neighbour_files(client, tmp_path, token):
    job = wait_for(client, submit_generate(client, output_dir=str(tmp_path / "chosen"))["id"])
    output = Path(job["outputs"][0])
    sidecar = output.with_suffix(".metadata.json")
    sidecar.write_text("{}")
    neighbour = output.parent / "unrelated.png"
    Image.new("RGB", (8, 8)).save(neighbour)
    assert client.get("/api/files", params={"path": str(neighbour), "token": token}).status_code == 403
    response = client.delete(f"/api/library/{job['id']}", params={"delete_files": True})
    assert response.status_code == 200
    assert not output.exists() and not sidecar.exists()
    assert neighbour.exists()


def test_cannot_delete_an_active_task(client):
    job = submit_generate(client, steps=99)
    assert client.delete(f"/api/library/{job['id']}", params={"delete_files": True}).status_code == 409
    client.post(f"/api/jobs/{job['id']}/cancel")
    wait_for(client, job["id"])


def test_jobs_listing_includes_recent_runs(client):
    submit_generate(client, steps=3, preview_interval=0)
    jobs = client.get("/api/jobs").json()["jobs"]
    assert len(jobs) >= 1
    assert jobs[0]["kind"] == "generate"
