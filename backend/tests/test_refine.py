"""Refining a result: using one generated image as the input to the next run.

Refinement is a frontend affordance, but it leans on three backend guarantees, and each has
failed in a different way in the past:

* the result is a real file that must survive being snapshotted as a reference, even though
  it is a Refract-written PNG rather than something the user picked;
* it must be *readable*, or the reference strip and comparison view show a blank tile for
  the very image the user is refining;
* the run must stay filed under the same project, or a refinement chain scatters across
  projects and the Library filter stops meaning anything.
"""

from __future__ import annotations

import time
from pathlib import Path


def _generate(client, prompt: str, project_id: str | None = None, references: list[str] | None = None) -> dict:
    payload = {"prompt": prompt, "steps": 1, "model_source": "mlx-q4"}
    if project_id:
        payload["project_id"] = project_id
    if references:
        payload["reference_paths"] = references
    job = client.post("/api/jobs", json={"kind": "generate", "payload": payload}).json()

    deadline = time.monotonic() + 30
    while job["status"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.05)
        job = client.get(f"/api/jobs/{job['id']}").json()
    return job


def test_a_result_becomes_the_reference_for_the_next_run(client):
    first = _generate(client, "a lighthouse at dusk")
    output = first["outputs"][0]
    assert Path(output).is_file()

    # The result is fed straight back in as reference 1, which is what refinement does.
    second = _generate(client, "make the sky more dramatic", references=[output])

    assert second["status"] == "done"
    # The reference snapshot copied the result into the job's own scratch directory rather
    # than pointing at the live file, so a later delete cannot break a queued run.
    assert second["references"] == [output]
    assert second["result"]["outputs"]


def test_a_result_is_readable_back_over_the_api(client):
    """The reference strip and comparison view both load it through /api/files."""
    output = _generate(client, "a still life")["outputs"][0]
    response = client.get("/api/files", params={"path": output, "token": "test-token"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_refinement_stays_in_the_same_project(client):
    project = client.post("/api/projects", json={"name": "Refine chain"}).json()

    first = _generate(client, "a red barn", project_id=project["id"])
    second = _generate(client, "add a fence", project_id=project["id"], references=first["outputs"])

    library = client.get(f"/api/library?project_id={project['id']}").json()
    assert {item["id"] for item in library["items"]} == {first["id"], second["id"]}
    assert len(client.get("/api/library").json()["items"]) == len(library["items"])


def test_a_refinement_run_records_the_result_it_came_from(client):
    """Its own metadata still holds the new instruction, not the one being refined."""
    first = _generate(client, "a red barn")
    second = _generate(client, "add a fence", references=first["outputs"])

    stored = client.get("/api/library").json()["items"]
    by_id = {item["id"]: item for item in stored}
    assert by_id[second["id"]]["prompt"] == "add a fence"
    assert by_id[second["id"]]["references"] == first["outputs"]
    assert by_id[first["id"]]["prompt"] == "a red barn"


def test_replay_of_a_refinement_keeps_both_the_prompt_and_its_references(client):
    first = _generate(client, "a red barn")
    second = _generate(client, "add a fence", references=first["outputs"])

    replayed = client.post(f"/api/library/{second['id']}/replay").json()
    assert replayed["payload"]["prompt"] == "add a fence"
    assert replayed["payload"]["reference_paths"] == first["outputs"]