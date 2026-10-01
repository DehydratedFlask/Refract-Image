"""What a first launch needs to know before it can start.

The native app asks the service one question at launch: *is this machine set up yet?* The
answer decides whether the user meets a setup window or their workspace. It is deliberately
read-only and cheap, because it is asked on every launch including the ones that need no
setup at all.

"Set up" is three independent things, and reporting them separately is the point. A user who
has the runtime but no model is not in the same position as one who has neither, and a user
with a model on a nearly full disk is in trouble even though everything else is present. A
single boolean would hide all three distinctions.
"""

from __future__ import annotations

import shutil
from typing import Any

from .model_store import SOURCES, list_sources
from .paths import data_root, dir_size_bytes, models_dir, storage_report


def disk_free_bytes(path: str | None = None) -> int:
    """Free space where the weights will actually land.

    Resolved by walking up to the closest existing ancestor, because the data root itself is
    only created on first launch and asking about a path that does not exist yet answers 0.
    """
    import os
    from pathlib import Path

    probe = Path(path) if path else data_root()
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            return 0
        probe = parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return 0


def describe_setup() -> dict[str, Any]:
    """A single report the first-run window renders and the app branches on."""
    sources = list_sources()
    #: Only the two families a new user is actually offered. `prepared` and `custom` are
    #: meaningless before anything is downloaded, and `upstream-q4` is a 33 GB fallback
    #: nobody should be handed as a first-run default.
    offered = [source for source in sources if source["id"] in ("mlx-q4", "flux2-klein-4b")]

    ready = [source for source in offered if source["available"]]
    free = disk_free_bytes()

    return {
        # The runtime is the one thing that must exist before this service could even be
        # running, so reaching the endpoint at all means it is present. It is reported for
        # the UI's benefit, not as a condition anyone can act on here.
        "runtime_ready": True,
        "models_ready": len(ready) > 0,
        "ready": len(ready) > 0,
        "free_disk_bytes": free,
        "models_dir": str(models_dir()),
        "data_root": str(data_root()),
        "models_size_bytes": dir_size_bytes(models_dir()),
        "installed": [source["id"] for source in ready],
        "choices": [
            {
                "id": source["id"],
                "label": source["label"],
                "detail": source["detail"],
                "notes": source["notes"],
                "approx_bytes": source["approx_download_bytes"],
                "install_job": source.get("install_job", "download"),
                "available": source["available"],
                "size_bytes": source["size_bytes"],
                "steps": SOURCES[source["id"]].default_steps,
                "family": SOURCES[source["id"]].family,
            }
            for source in offered
        ],
        "storage": storage_report(),
    }