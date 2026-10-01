"""Export the first-run setup catalog as JSON.

The setup window has to offer a model choice *before* the runtime exists — it is what the
runtime is being installed for — so it cannot ask a running service what is on offer. It reads
this file instead, which the app build generates from `SOURCES`.

One source of truth, generated rather than copied: the labels, sizes and notes a new user is
shown are the same strings the running app will use, and they cannot drift.
"""

from __future__ import annotations

import json
import sys

from ..model_store import SOURCES

#: Only the two families a new user is offered. `prepared` and `custom` mean nothing before
#: anything is downloaded, and `upstream-q4` is a 33 GB fallback nobody should be handed first.
OFFERED = ("mlx-q4", "flux2-klein-4b")


def catalog() -> dict:
    return {
        "runtime_bytes": 3_500_000_000,
        "choices": [
            {
                "id": source_id,
                "label": SOURCES[source_id].label,
                "detail": SOURCES[source_id].detail,
                "notes": list(SOURCES[source_id].notes),
                "approx_bytes": SOURCES[source_id].approx_bytes,
                "install_job": SOURCES[source_id].install_job,
                "default_steps": SOURCES[source_id].default_steps,
                "family": SOURCES[source_id].family,
            }
            for source_id in OFFERED
            if source_id in SOURCES
        ],
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = json.dumps(catalog(), indent=2)
    if argv:
        with open(argv[0], "w", encoding="utf-8") as handle:
            handle.write(out + "\n")
        print(f"wrote {argv[0]}")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())