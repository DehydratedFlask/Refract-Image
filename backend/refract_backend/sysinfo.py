"""Machine facts the app needs to pick safe defaults: RAM, disk, chip, MLX memory.

These numbers drive real decisions in the UI (default resolution, whether low-memory mode
is worth pre-enabling, whether a download even fits), so they are reported rather than
guessed.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass

from .paths import data_root, dir_size_bytes, hf_cache_dir, models_dir, outputs_dir, storage_report, volume_root


def _sysctl(name: str) -> str | None:
    try:
        out = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def total_ram_gb() -> float:
    raw = _sysctl("hw.memsize")
    if raw and raw.isdigit():
        # macOS reports installed RAM in binary units (48 * 1024**3 bytes).
        return round(int(raw) / 1024**3, 1)
    try:  # portable fallback
        return round(os_sysconf_pages() * os_page_size() / 1024**3, 1)
    except Exception:
        return 0.0


def os_sysconf_pages() -> int:
    import os

    return os.sysconf("SC_PHYS_PAGES")


def os_page_size() -> int:
    import os

    return os.sysconf("SC_PAGE_SIZE")


def peak_memory_gb() -> float:
    """MLX's peak allocation so far, in GB. 0.0 when MLX is not importable."""
    try:
        import mlx.core as mx

        for getter in (
            lambda: mx.get_peak_memory(),
            lambda: mx.metal.get_peak_memory(),
        ):
            try:
                return round(getter() / 10**9, 2)
            except Exception:
                continue
    except ImportError:
        pass
    return 0.0


def active_memory_gb() -> float:
    try:
        import mlx.core as mx

        return round(mx.get_active_memory() / 10**9, 2)
    except Exception:
        return 0.0


def reset_peak_memory() -> None:
    try:
        import mlx.core as mx

        mx.reset_peak_memory()
    except Exception:
        pass


def clear_cache() -> None:
    """gc + MLX buffer cache drop. Called between jobs so long sessions stay flat."""
    import gc

    try:
        import mlx.core as mx
    except ImportError:
        gc.collect()
        return
    try:
        mx.synchronize()
    except Exception:
        pass
    gc.collect()
    try:
        mx.clear_cache()
    except Exception:
        pass


def set_cache_limit_gb(value: float | None) -> None:
    if value is None:
        return
    try:
        import mlx.core as mx

        mx.set_cache_limit(int(value * 1000**3))
    except Exception:
        pass


def mflux_version() -> str:
    try:
        import importlib.metadata as md

        return md.version("mflux")
    except Exception:
        return "not installed"


def mflux_commit() -> str | None:
    try:
        import importlib.metadata as md

        for dist in md.distributions():
            if dist.metadata["Name"] != "mflux":
                continue
            direct = dist.read_text("direct_url.json")
            if direct:
                import json

                payload = json.loads(direct)
                return payload.get("vcs_info", {}).get("commit_id")
    except Exception:
        pass
    return None


def mlx_version() -> str:
    try:
        import importlib.metadata as md

        return md.version("mlx")
    except Exception:
        return "not installed"


def runner_available() -> bool:
    """True when the real MLX inference stack can be imported."""
    try:
        from mflux.models.qwen21.reference import QwenImage21Edit  # noqa: F401

        return True
    except Exception:
        return False


def runner_unavailable_reason() -> str | None:
    if runner_available():
        return None
    try:
        import mflux  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on the machine
        return f"mflux is not importable: {exc}"
    return (
        "the installed mflux release has no Qwen-Image-2.1 reference-editing pipeline; "
        "install mflux from its main branch (scripts/bootstrap.sh does this)"
    )


@dataclass
class SystemInfo:
    chip: str
    architecture: str
    macos_version: str
    python_version: str
    total_ram_gb: float
    free_disk_gb: float
    mlx_version: str
    mflux_version: str
    mflux_commit: str | None
    runner_ready: bool
    runner_note: str | None
    data_root: str
    data_volume: str
    data_is_external: bool
    models_dir: str
    outputs_dir: str
    hf_cache_dir: str
    models_size_bytes: int
    hf_cache_size_bytes: int

    def to_dict(self) -> dict:
        return asdict(self)


def system_info() -> SystemInfo:
    # Free space is measured on the volume the models are stored on: that is the disk a
    # large download can actually fail to fit in, and it is not the boot disk when the
    # project lives on an external drive.
    root = data_root()
    usage = shutil.disk_usage(str(volume_root(root)))
    return SystemInfo(
        chip=_sysctl("machdep.cpu.brand_string") or platform.processor() or "unknown",
        architecture=platform.machine(),
        macos_version=platform.mac_ver()[0] or "unknown",
        python_version=platform.python_version(),
        total_ram_gb=total_ram_gb(),
        free_disk_gb=round(usage.free / 10**9, 1),
        mlx_version=mlx_version(),
        mflux_version=mflux_version(),
        mflux_commit=mflux_commit(),
        runner_ready=runner_available(),
        runner_note=runner_unavailable_reason(),
        data_root=str(root),
        data_volume=str(volume_root(root)),
        data_is_external=bool(storage_report()["external"]),
        models_dir=str(models_dir()),
        outputs_dir=str(outputs_dir()),
        hf_cache_dir=str(hf_cache_dir()),
        models_size_bytes=dir_size_bytes(models_dir()),
        hf_cache_size_bytes=dir_size_bytes(hf_cache_dir()),
    )
