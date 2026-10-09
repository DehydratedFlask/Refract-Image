"""Filesystem locations used by the Refract backend.

The app owns a lot of bytes: an 8.9 GB weight cache to start with, plus one prepared model
directory per source and a picture for every generation. None of that belongs on the
machine's internal disk when the app itself lives on an external volume, so the whole data
set hangs off a single root:

    <root>/models       prepared and installed model directories
    <root>/hf-home      Hugging Face's own cache (HF_HOME), i.e. raw downloads
    <root>/jobs         per-job scratch space: copied references, progress previews
    <root>/outputs      generated images
    <root>/library.db   the generation history

The root is chosen in this order:

1. ``REFRACT_ROOT`` — what the scripts and the app's native sidecar pass explicitly, so
   both sides always agree.
2. ``REFRACT_DATA_DIR`` — the original single override, still honoured.
3. ``<checkout>/.refract`` — when the checkout is *not* on the same volume as the home
   folder, i.e. when the project lives on an external drive. Data then travels with the
   project instead of filling the internal disk.
4. ``~/Library/Application Support/Refract`` — the normal macOS location otherwise.

Every individual directory keeps its own override too (``REFRACT_MODELS_DIR``, ...), which
is what the tests use to stay hermetic.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Refract"

DATA_DIR_NAME = ".refract"


def _expand(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return _expand(raw) if raw else default


# --------------------------------------------------------------------------- location
def repo_root() -> Path | None:
    """The checkout this package lives in, when it can be worked out.

    Used only to decide *where* data is kept. ``REFRACT_REPO_ROOT`` overrides; otherwise
    the package's own path is walked upwards looking for the markers the repository has
    (the ``.runtime`` interpreter directory written by ``scripts/bootstrap.sh``).
    """
    raw = os.environ.get("REFRACT_REPO_ROOT")
    if raw:
        return _expand(raw)
    for parent in Path(__file__).resolve().parents:
        if (parent / ".runtime").is_dir() or (parent / "scripts" / "bootstrap.sh").is_file():
            return parent
    return None


def workspace_root() -> Path | None:
    """Application runtime/data root, separate from the optional source-only copy."""
    raw = os.environ.get("REFRACT_WORKSPACE")
    if raw:
        return _expand(raw)
    root = repo_root()
    if root is None:
        return None
    marker = root / ".refract-workspace"
    if marker.is_file():
        relative = marker.read_text().strip()
        if not relative:
            raise ValueError(".refract-workspace must name the application workspace")
        return (root / relative).resolve()
    return root


def _existing_ancestor(path: Path) -> Path:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return probe


def volume_root(path: Path) -> Path:
    """The mount point a path sits on, e.g. ``/Volumes/ExternalDrive``."""
    current = _existing_ancestor(path if path.is_dir() else path.parent)
    if current.is_file():
        current = current.parent
    while current.parent != current and not os.path.ismount(current):
        current = current.parent
    return current


def is_on_internal_disk(path: Path) -> bool:
    """True when ``path`` lives on the same volume as the home folder.

    Volume identity is the device id, not the name or the prefix: an APFS volume mounted
    under ``/Volumes`` has a different ``st_dev`` from the volume holding ``$HOME``, which
    is exactly the difference between "the external SSD" and "the internal disk" — and it
    stays correct for renamed drives, other mount points and symlinked paths.
    """
    try:
        probe = _existing_ancestor(path)
        return os.stat(probe).st_dev == os.stat(Path.home()).st_dev
    except OSError:
        return True


def _default_data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / APP_NAME


def data_root() -> Path:
    """Where models, downloads, jobs, outputs and the library live (see module docstring)."""
    explicit = os.environ.get("REFRACT_ROOT") or os.environ.get("REFRACT_DATA_DIR")
    if explicit:
        return _expand(explicit)
    root = workspace_root()
    if root is not None and not is_on_internal_disk(root):
        return root / DATA_DIR_NAME
    return _default_data_dir()


def app_data_dir() -> Path:
    """Alias kept for callers that want the root rather than a named subdirectory."""
    return data_root()


def models_dir() -> Path:
    """Prepared/installed model directories live here."""
    return _env_path("REFRACT_MODELS_DIR", data_root() / "models")





def projects_dir() -> Path:
    """Per-project working state: one subdirectory each, holding its reference copies.

    Its own directory rather than a field in the Library DB because the references are
    real files with real bytes, and a project you cannot reopen is not a project.
    """
    return _env_path("REFRACT_PROJECTS_DIR", data_root() / "projects")


def jobs_dir() -> Path:
    """Per-job scratch space: copied references, progress previews."""
    return _env_path("REFRACT_JOBS_DIR", data_root() / "jobs")


def library_db_path() -> Path:
    return _env_path("REFRACT_LIBRARY_DB", data_root() / "library.db")


def outputs_dir() -> Path:
    """Where generated images land by default."""
    return _env_path("REFRACT_OUTPUT_DIR", data_root() / "outputs")


class OutputDirError(RuntimeError):
    """Raised when the destination for a run cannot be created or written to."""


def prepare_output_dir(output_dir: str | None) -> Path:
    """The folder a run writes to: the one the user chose, or :func:`outputs_dir`.

    Called *before* the model loads. A chosen folder can be missing, read-only, or a file,
    and finding that out after a five-minute denoise throws the run away — so it is created
    and write-probed here, and the error names the path and the way out.
    """
    target = _expand(output_dir) if output_dir else outputs_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".refract-write-test"
        probe.touch()
        probe.unlink()
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise OutputDirError(
            f"Cannot save images to {target}: {reason}. Choose another folder in Settings, "
            "or clear the field to use the default outputs folder."
        ) from exc
    return target


def hf_home() -> Path:
    """Hugging Face's cache root (``HF_HOME``): downloads, plus its own scratch dirs."""
    raw = os.environ.get("HF_HOME")
    if raw:
        return _expand(raw)
    return _env_path("REFRACT_HF_HOME", data_root() / "hf-home")


def hf_cache_dir() -> Path:
    """The hub cache inside :func:`hf_home`, i.e. where model files are stored."""
    raw = os.environ.get("REFRACT_HF_CACHE")
    if raw:
        return _expand(raw)
    return hf_home() / "hub"


def hf_repo_cache_dir(repo_id: str) -> Path:
    """Cache directory a given repo id maps to, e.g. ``models--mlx-community--Foo``."""
    return hf_cache_dir() / ("models--" + repo_id.replace("/", "--"))


def configure_environment() -> dict[str, str]:
    """Export the variables third-party code reads for its caches.

    ``huggingface_hub`` bakes ``HF_HOME``/``HF_HUB_CACHE`` into its constants at import
    time, and mflux downloads weights through that library, so this has to run before
    anything imports it. It is an explicit call from the entry points rather than a module
    side effect so tests can point the cache at a temporary directory instead.
    """
    exports = {
        "HF_HOME": str(hf_home()),
        "HF_HUB_CACHE": str(hf_cache_dir()),
        "HF_XET_CACHE": str(hf_home() / "xet"),
    }
    for name, value in exports.items():
        os.environ.setdefault(name, value)
    return {name: os.environ[name] for name in exports}


def ensure_dirs() -> None:
    for path in (data_root(), models_dir(), projects_dir(), jobs_dir(), outputs_dir()):
        path.mkdir(parents=True, exist_ok=True)


def storage_report() -> dict[str, object]:
    """Where the app stores things, and whether that is off the internal disk."""
    root = data_root()
    volume = volume_root(root)
    return {
        "data_root": str(root),
        "volume": str(volume),
        "external": not is_on_internal_disk(root),
        "models_dir": str(models_dir()),
        "hf_cache_dir": str(hf_cache_dir()),
        "jobs_dir": str(jobs_dir()),
        "outputs_dir": str(outputs_dir()),
    }


def dir_size_bytes(path: Path) -> int:
    """Recursive size, tolerating files that vanish mid-walk (partial downloads)."""
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def human_bytes(value: int) -> str:
    step = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if step < 1024 or unit == "TB":
            return f"{step:.1f} {unit}" if unit != "B" else f"{int(step)} B"
        step /= 1024
    return f"{step:.1f} TB"
