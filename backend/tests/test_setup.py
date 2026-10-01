"""First-run setup: what a brand-new machine is told, and what it can install.

The setup window reads a generated catalog rather than asking the service, because there is no
service until the runtime it is installing exists. That makes the catalog's *shape* the
contract between the build and the native window, and these tests are what keep a new field
from being added to one side only.
"""

from __future__ import annotations

import json

from refract_backend.model_store import SOURCES, list_sources
from refract_backend.tools import export_setup

#: What the native window reads out of each entry. Mirrors `ModelChoice` in SetupWindow.swift;
#: a rename on one side and not the other would show a blank model name to a new user.
CATALOG_FIELDS = {"id", "label", "detail", "notes", "approx_bytes", "install_job"}


def test_the_catalog_offers_exactly_the_two_families():
    assert [choice["id"] for choice in export_setup.catalog()["choices"]] == ["mlx-q4", "flux2-klein-4b"]


def test_every_catalog_entry_carries_what_the_window_reads():
    for choice in export_setup.catalog()["choices"]:
        assert CATALOG_FIELDS <= set(choice), f"{choice.get('id')} is missing fields the setup window reads"
        assert choice["label"].strip()
        assert choice["detail"].strip()
        assert choice["approx_bytes"] > 0


def test_the_catalog_survives_a_round_trip_through_json():
    """It is written to disk and read back by JSONSerialization, not passed in memory."""
    text = json.dumps(export_setup.catalog())
    assert json.loads(text)["choices"]


def test_flux2_is_offered_with_the_job_that_actually_installs_it():
    """The catalog is the only place a new user learns a model is staged rather than fetched."""
    choice = next(c for c in export_setup.catalog()["choices"] if c["id"] == "flux2-klein-4b")
    assert choice["install_job"] == "prepare-klein"


def test_the_catalog_and_the_sources_agree_on_install_job():
    for choice in export_setup.catalog()["choices"]:
        assert choice["install_job"] == SOURCES[choice["id"]].install_job


def test_every_offered_source_is_reachable_from_the_api():
    """A model the setup window offers must also be a real source the app can switch to."""
    offered = {choice["id"] for choice in export_setup.catalog()["choices"]}
    assert offered <= {source["id"] for source in list_sources()}


def test_the_setup_endpoint_describes_a_fresh_machine(client):
    payload = client.get("/api/setup").json()
    assert payload["choices"]
    assert isinstance(payload["ready"], bool)
    assert payload["free_disk_bytes"] > 0
    assert payload["runtime_ready"] is True


def test_a_machine_with_nothing_staged_is_not_ready(client, monkeypatch):
    monkeypatch.setattr("refract_backend.setup.list_sources", lambda: [
        {"id": "mlx-q4", "label": "Q", "detail": "d", "notes": [], "approx_download_bytes": 1,
         "available": False, "install_job": "download", "size_bytes": 0},
        {"id": "flux2-klein-4b", "label": "K", "detail": "d", "notes": [], "approx_download_bytes": 1,
         "available": False, "install_job": "prepare-klein", "size_bytes": 0},
    ])
    payload = client.get("/api/setup").json()
    assert payload["ready"] is False
    assert payload["installed"] == []


def test_prepare_klein_is_an_accepted_job_kind(client, isolated_dirs, monkeypatch):
    """A new user's Flux2 install depends on this job kind existing at all.

    The staging itself is stubbed: it fetches ~16 GB from the Hub, which is not what this
    test is about and would make the suite depend on the network.
    """
    import time

    from refract_backend.jobs import JOB_KINDS
    from refract_backend.tools import prepare_klein

    assert "prepare-klein" in JOB_KINDS

    seen: list[str] = []
    monkeypatch.setattr(
        prepare_klein,
        "stage_klein",
        lambda encoder, progress=None: (
            seen.append(encoder),
            {"prepared": True, "path": "/tmp/flux2-klein-4b", "name": "flux2-klein-4b",
             "size_bytes": 1, "encoder": encoder, "encoder_label": "Ablated", "manifest": {}},
        )[1],
    )

    job = client.post("/api/jobs", json={"kind": "prepare-klein", "payload": {"encoder": "ablated"}})
    assert job.status_code == 202

    deadline = time.monotonic() + 20
    record = job.json()
    while record["status"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.05)
        record = client.get(f"/api/jobs/{record['id']}").json()
    assert record["status"] == "done", record.get("error")
    assert seen == ["ablated"]
    assert record["result"]["name"] == "flux2-klein-4b"


def test_klein_availability_comes_from_its_own_staged_pack(client, isolated_dirs):
    """Not from the generic model scan, which would report it ready on any stray directory."""
    from refract_backend.paths import models_dir

    entry = next(s for s in list_sources() if s["id"] == "flux2-klein-4b")
    assert entry["available"] is False

    staged = models_dir() / "flux2-klein-4b"
    (staged / "transformer").mkdir(parents=True)
    assert next(s for s in list_sources() if s["id"] == "flux2-klein-4b")["available"] is True