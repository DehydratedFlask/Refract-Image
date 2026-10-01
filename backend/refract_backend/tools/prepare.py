"""`python -m refract_backend.tools.prepare` — prepare a model from the terminal.

The app does this from its Models pane; this exists for headless setup, for CI, and for
the case where you would rather watch the download in a terminal.
"""

from __future__ import annotations

import argparse
import json
import sys

from .. import model_store
from ..paths import configure_environment, ensure_dirs, human_bytes, storage_report


def _progress(event: dict) -> None:
    phase = event.get("phase", "")
    message = event.get("message", "")
    total = event.get("total_bytes")
    done = event.get("downloaded_bytes")
    if total and done:
        percent = 100 * done / total
        sys.stdout.write(f"\r  {phase:<12} {percent:5.1f}%  {human_bytes(done)} / {human_bytes(total)}   ")
    elif message:
        sys.stdout.write(f"\r  {phase:<12} {message[:90]:<90}")
    sys.stdout.flush()
    if event.get("phase") == "done":
        sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prepare-model", description="Prepare a Refract Image model locally")
    parser.add_argument("--source", default="mlx-q4", help="source id (see --list)")
    parser.add_argument("--name", default=None, help="directory name under the models folder")
    parser.add_argument("--list", action="store_true", help="show sources and exit")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    # Before anything can import huggingface_hub, so its caches land next to the models.
    configure_environment()
    ensure_dirs()

    if args.list:
        sources = model_store.list_sources()
        if args.json:
            print(json.dumps(sources, indent=2))
            return 0
        for source in sources:
            status = "ready" if source["available"] else "not on disk"
            size = human_bytes(source["size_bytes"] or source["approx_download_bytes"])
            print(f"{source['id']:<14} {status:<12} {size:>10}  {source['label']}")
            if source["location"]:
                print(f"{'':<14} {source['location']}")
        print(f"\nModels: {model_store.disk_report()['models_dir']}")
        storage = storage_report()
        where = "external volume" if storage["external"] else "internal disk"
        print(f"Data:   {storage['data_root']} ({where})")
        return 0

    spec = model_store.SOURCES.get(args.source)
    if spec is None:
        print(f"unknown source {args.source!r}; try --list", file=sys.stderr)
        return 2

    print(f"Preparing {spec.label} ({spec.repo_id or 'local'})")
    try:
        result = model_store.prepare_source(args.source, name=args.name, progress=_progress)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"\nprepare failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    action = "already prepared" if result.get("already") else "prepared"
    print(f"{action}: {result['path']} ({human_bytes(result.get('size_bytes', 0))})")
    print("Switch the app's model source to 'Prepared local model' to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
