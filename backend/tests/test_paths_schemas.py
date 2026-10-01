from __future__ import annotations

from pathlib import Path

import pytest

from refract_backend import paths
from refract_backend.schemas import GenerateRequest


def test_env_overrides_redirect_every_directory(isolated_dirs):
    assert paths.app_data_dir() == (isolated_dirs / "data").resolve()
    assert paths.models_dir() == (isolated_dirs / "data" / "models").resolve()
    assert paths.outputs_dir() == (isolated_dirs / "outputs").resolve()
    assert paths.jobs_dir() == (isolated_dirs / "data" / "jobs").resolve()


def test_dir_size_and_human_bytes(isolated_dirs):
    target = isolated_dirs / "data" / "blob"
    target.mkdir(parents=True)
    (target / "a.bin").write_bytes(b"x" * 1024)
    (target / "b.bin").write_bytes(b"y" * 1024)
    assert paths.dir_size_bytes(target) == 2048
    assert paths.human_bytes(2048) == "2.0 KB"
    assert paths.human_bytes(0) == "0 B"


def test_hf_repo_cache_dir_maps_repo_ids(isolated_dirs):
    assert paths.hf_repo_cache_dir("owner/name").name == "models--owner--name"


def test_reference_count_is_capped():
    with pytest.raises(ValueError) as excinfo:
        GenerateRequest(prompt="p", reference_paths=[f"/tmp/{i}.png" for i in range(11)])
    assert "at most 10" in str(excinfo.value)


def test_dimensions_must_be_multiples_of_32():
    with pytest.raises(ValueError) as excinfo:
        GenerateRequest(prompt="p", width=1000)
    assert "multiple of 32" in str(excinfo.value)
    assert GenerateRequest(prompt="p", width=1024, height=768).width == 1024


def test_guidance_below_one_is_rejected():
    with pytest.raises(ValueError):
        GenerateRequest(prompt="p", guidance=0.5)


def test_output_resolution_whitelist():
    with pytest.raises(ValueError) as excinfo:
        GenerateRequest(prompt="p", output_resolution=1000)
    assert "output resolution must be one of" in str(excinfo.value)


def test_seed_list_prefers_explicit_seeds():
    assert GenerateRequest(prompt="p", seed=7).seed_list() == [7]
    assert GenerateRequest(prompt="p", seeds=[1, 2, 3]).seed_list() == [1, 2, 3]
    assert GenerateRequest(prompt="p").seed_list() == [-1]


def test_reference_paths_are_trimmed_of_blanks():
    request = GenerateRequest(prompt="p", reference_paths=["  /tmp/a.png ", ""])
    assert request.reference_paths == ["/tmp/a.png"]


def test_metadata_excludes_paths_but_keeps_params():
    request = GenerateRequest(prompt="p", reference_paths=["/tmp/a.png"], output_dir="/tmp/out")
    metadata = request.to_metadata()
    assert "output_dir" not in metadata
    assert metadata["prompt"] == "p"
    assert metadata["steps"] == 40


def test_jobs_dir_is_created_under_app_data(isolated_dirs):
    paths.ensure_dirs()
    assert Path(paths.jobs_dir()).is_dir()
    assert Path(paths.outputs_dir()).is_dir()
