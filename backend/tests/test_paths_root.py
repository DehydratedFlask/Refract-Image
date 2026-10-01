"""Where the app keeps its data.

These rules decide whether a 9 GB download lands on the internal disk or on the external
one, so they are pinned down rather than left to inspection.
"""

from __future__ import annotations

from pathlib import Path

from refract_backend import paths


def test_explicit_root_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("REFRACT_ROOT", str(tmp_path / "chosen"))
    monkeypatch.setenv("REFRACT_DATA_DIR", str(tmp_path / "ignored"))
    assert paths.data_root() == (tmp_path / "chosen").resolve()


def test_data_dir_is_honoured_without_a_root(monkeypatch, tmp_path):
    monkeypatch.delenv("REFRACT_ROOT", raising=False)
    monkeypatch.setenv("REFRACT_DATA_DIR", str(tmp_path / "data"))
    assert paths.data_root() == (tmp_path / "data").resolve()


def test_every_directory_hangs_off_the_root(monkeypatch, tmp_path):
    for name in (
        "REFRACT_MODELS_DIR",
        "REFRACT_JOBS_DIR",
        "REFRACT_OUTPUT_DIR",
        "REFRACT_LIBRARY_DB",
        "REFRACT_HF_CACHE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("REFRACT_ROOT", str(tmp_path / "root"))
    root = (tmp_path / "root").resolve()

    assert paths.models_dir() == root / "models"
    assert paths.jobs_dir() == root / "jobs"
    assert paths.outputs_dir() == root / "outputs"
    assert paths.library_db_path() == root / "library.db"
    # Weights are the biggest thing the app writes, so they live under the root as well.
    assert paths.hf_cache_dir() == root / "hf-home" / "hub"


def test_external_checkout_keeps_data_beside_it(monkeypatch, tmp_path):
    """The rule that keeps this project off the internal disk."""
    monkeypatch.delenv("REFRACT_ROOT", raising=False)
    monkeypatch.delenv("REFRACT_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "repo_root", lambda: tmp_path / "checkout")
    monkeypatch.setattr(paths, "is_on_internal_disk", lambda _path: False)
    assert paths.data_root() == (tmp_path / "checkout" / paths.DATA_DIR_NAME).resolve()


def test_internal_checkout_uses_application_support(monkeypatch, tmp_path):
    monkeypatch.delenv("REFRACT_ROOT", raising=False)
    monkeypatch.delenv("REFRACT_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "repo_root", lambda: tmp_path / "checkout")
    monkeypatch.setattr(paths, "is_on_internal_disk", lambda _path: True)
    assert paths.data_root() == Path.home() / "Library" / "Application Support" / "Refract"


def test_no_checkout_found_still_answers(monkeypatch):
    monkeypatch.delenv("REFRACT_ROOT", raising=False)
    monkeypatch.delenv("REFRACT_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "repo_root", lambda: None)
    assert paths.data_root() == Path.home() / "Library" / "Application Support" / "Refract"


def test_this_checkout_is_recognised(monkeypatch):
    """The repository really is found, and really is not on the boot volume."""
    monkeypatch.delenv("REFRACT_REPO_ROOT", raising=False)
    root = paths.repo_root()
    assert root is not None
    assert (root / "backend" / "refract_backend").is_dir()
    assert (root / ".runtime").is_dir() or (root / "scripts" / "bootstrap.sh").is_file()


def test_volume_root_walks_up_to_a_mount_point(tmp_path):
    volume = paths.volume_root(tmp_path / "a" / "b")
    assert volume.is_dir()
    assert paths.os.path.ismount(volume) or volume == Path("/")


def test_unknown_path_is_treated_as_internal(monkeypatch, tmp_path):
    """Conservative default: if the volume cannot be read, assume it is not an external one."""
    monkeypatch.setattr(paths.os, "stat", _raise_oserror)
    assert paths.is_on_internal_disk(tmp_path / "missing") is True


def test_configure_environment_points_the_hub_at_the_root(monkeypatch, tmp_path):
    monkeypatch.setenv("REFRACT_ROOT", str(tmp_path / "root"))
    for name in ("HF_HOME", "HF_HUB_CACHE", "HF_XET_CACHE"):
        monkeypatch.delenv(name, raising=False)

    exported = paths.configure_environment()

    root = (tmp_path / "root").resolve()
    assert exported["HF_HOME"] == str(root / "hf-home")
    assert exported["HF_HUB_CACHE"] == str(root / "hf-home" / "hub")
    assert paths.os.environ["HF_HOME"] == exported["HF_HOME"]


def test_configure_environment_never_overrides_a_deliberate_choice(monkeypatch, tmp_path):
    monkeypatch.setenv("REFRACT_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "elsewhere"))
    assert paths.configure_environment()["HF_HOME"] == str(tmp_path / "elsewhere")


def test_storage_report_names_the_volume(monkeypatch, tmp_path):
    monkeypatch.setenv("REFRACT_ROOT", str(tmp_path / "root"))
    report = paths.storage_report()
    assert report["data_root"] == str((tmp_path / "root").resolve())
    assert report["volume"]
    assert isinstance(report["external"], bool)
    assert report["models_dir"].endswith("models")


def _raise_oserror(*_args, **_kwargs):
    raise OSError("unreadable")
