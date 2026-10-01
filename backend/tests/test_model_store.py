"""Tests for the Models inventory.

These describe what the Models pane is told, which is also what decides whether the app
offers to download or to prepare: "available" is about bytes on disk, "staged" is about a
checkpoint mflux can actually load.
"""

from __future__ import annotations

import json

import pytest

from refract_backend import model_store, paths


@pytest.fixture()
def data_root(monkeypatch, tmp_path):
    root = tmp_path / "refract"
    monkeypatch.setenv("REFRACT_ROOT", str(root))
    paths.ensure_dirs()
    return root


def _installed_model(root, name="qwen-image-2.1-mlx-q4", origin="abenzerps/Qwen-Image-2.1-Uncensored-GGUF"):
    directory = root / "models" / name
    (directory / "transformer").mkdir(parents=True)
    (directory / "text_encoder").mkdir(parents=True)
    (directory / "transformer" / "0.safetensors").write_bytes(b"x")
    (directory / "text_encoder" / "0.safetensors").write_bytes(b"x")
    (directory / "refract-manifest.json").write_text(
        json.dumps({"bits": 4, "origin": origin, "created_at": "2026-09-30T00:00:00Z"})
    )
    return directory


def test_list_sources_survives_an_installed_model(data_root):
    """A model on disk used to crash the whole inventory with a `KeyError: True`.

    `item["customised" if spec.id == "custom" else True]` subscripted with the conditional
    expression instead of reading the flag, which only raised once a model existed.
    """
    _installed_model(data_root)

    sources = {source["id"]: source for source in model_store.list_sources()}

    assert set(sources) == {"mlx-q4", "upstream-q4", "flux2-klein-4b", "prepared", "custom"}
    assert sources["prepared"]["available"] is True
    assert [item["name"] for item in sources["prepared"]["installed"]] == ["qwen-image-2.1-mlx-q4"]
    # The staged directory is a plain install, not a custom weights directory.
    assert sources["custom"]["available"] is False


def test_a_customised_directory_is_not_offered_as_a_prepared_model(data_root):
    _installed_model(data_root, name="custom-pack")
    manifest = data_root / "models" / "custom-pack" / "refract-manifest.json"
    manifest.write_text(json.dumps({"bits": 4, "custom_source": "third-party"}))

    sources = {source["id"]: source for source in model_store.list_sources()}

    assert sources["prepared"]["available"] is False
    assert sources["custom"]["available"] is True


def test_disk_report_measures_the_data_root(data_root):
    _installed_model(data_root)
    report = model_store.disk_report()
    assert report["data_root"] == str(data_root.resolve())
    assert report["models_size_bytes"] > 0
    # `external` is a property of the volume the root sits on, not of the app: a root beside
    # the checkout on the boot disk is correctly reported as internal.
    assert report["storage"]["external"] is (not paths.is_on_internal_disk(data_root))
