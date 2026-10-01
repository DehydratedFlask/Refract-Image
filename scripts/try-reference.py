#!/usr/bin/env python3
"""One real reference edit, driven through the app's own job pipeline.

Unlike ``scripts/smoke.py`` (which talks to mflux directly), this goes through the same
``JobQueue`` → ``MfluxRunner`` path the running app uses, so it also proves the pieces the
UI depends on: the reference is copied into the job directory, the output lands in the
chosen ``--output-dir``, its ``.json`` sidecar is written, and a Library row is recorded.
It then re-opens the file it wrote and reports the real dimensions, so "it produced a PNG"
is checked rather than assumed.

    .runtime/bin/python scripts/try-reference.py \\
        --reference ~/Downloads/Luffy.jpeg \\
        --prompt "keep the character; move the scene to a starlit night" \\
        --output-dir .refract/outputs/luffy-test --steps 8 --resolution 512

Add ``--mock`` to exercise the plumbing without loading the model.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

DONE_STATES = ("done", "failed", "cancelled")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one reference-guided generation")
    parser.add_argument("--reference", action="append", default=[], help="reference image (repeatable, up to 10)")
    parser.add_argument("--prompt", default="keep the character and pose; make it a starlit night scene")
    parser.add_argument("--output-dir", default=None, help="where the PNG goes (default: the app's outputs dir)")
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--resolution", type=int, default=512)
    parser.add_argument("--guidance", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--model-source", default="mlx-q4")
    parser.add_argument("--mock", action="store_true", help="use the placeholder runner (no model load)")
    args = parser.parse_args(argv)

    # Before anything imports huggingface_hub (mflux downloads through it), so every cache
    # it opens is the one under the app's data root.
    from refract_backend.paths import configure_environment, ensure_dirs, outputs_dir, storage_report

    configure_environment()
    ensure_dirs()

    from refract_backend.jobs import JobQueue
    from refract_backend.library import Library
    from refract_backend.schemas import GenerateRequest

    references = [Path(raw).expanduser() for raw in args.reference]
    missing = [path for path in references if not path.is_file()]
    if missing:
        for path in missing:
            print(f"reference not found: {path}", file=sys.stderr)
        return 2

    # Absolute, so the Library row it records keeps resolving after the process exits.
    target = Path(args.output_dir).expanduser().resolve() if args.output_dir else outputs_dir()
    storage = storage_report()
    print(f"data root    {storage['data_root']}  ({'external' if storage['external'] else 'internal disk'})")
    print(f"output dir   {target}{'  (the app default)' if args.output_dir is None else '  (chosen)'}")
    print(f"references   {len(references)}")
    for path in references:
        print(f"  {path}")
    print(f"prompt       {args.prompt}")
    print(f"run          {args.resolution}px · {args.steps} steps · guidance {args.guidance} · seed {args.seed}")

    if args.mock:
        from refract_backend.mock_runner import MockRunner

        runner = MockRunner()
    else:
        from refract_backend.runner import MfluxRunner

        print("\nloading the model — this is the slow part")
        runner = MfluxRunner()

    request = GenerateRequest(
        prompt=args.prompt,
        reference_paths=[str(path) for path in references],
        steps=args.steps,
        output_resolution=args.resolution,
        guidance=args.guidance,
        seed=args.seed,
        model_source=args.model_source,
        preview_interval=0,
        save_metadata=True,
        output_dir=str(target),
    )

    library = Library()
    queue = JobQueue(runner, library)
    job = queue.submit_generate(request)
    print(f"\njob          {job.id}")

    started = time.time()
    last = None
    while job.status not in DONE_STATES:
        current = f"{job.phase:<10} {job.message or ''}".rstrip()
        if current != last:
            step = f"  step {job.step}/{job.total_steps}" if job.total_steps else ""
            print(f"[{time.time() - started:6.1f}s] {current}{step}", flush=True)
            last = current
        time.sleep(0.4)

    elapsed = time.time() - started
    print(f"\nstatus       {job.status} in {elapsed:.1f}s")
    if job.error:
        print(f"error        {job.error}", file=sys.stderr)
    print(f"peak memory  {job.peak_memory_gb if job.peak_memory_gb is not None else '?'} GB")

    ok = job.status == "done" and bool(job.outputs)
    print("\noutputs")
    for raw in job.outputs:
        path = Path(raw)
        if not path.is_file():
            print(f"  MISSING  {path}")
            ok = False
            continue
        print(f"  {path}  {path.stat().st_size / 1e6:.2f} MB  {_dimensions(path)}")
        # mflux writes the sidecar as ``<name>.metadata.json`` next to the PNG.
        sidecar = path.with_suffix(".metadata.json")
        if sidecar.is_file():
            metadata = json.loads(sidecar.read_text())
            keys = ", ".join(sorted(metadata)[:6])
            print(f"    sidecar {sidecar.name} ({len(metadata)} fields: {keys}…)")
        else:
            print("    sidecar missing")
        if path.parent.resolve() != target.resolve():
            print(f"    WARNING written to {path.parent}, not the chosen {target}")
            ok = False

    if not job.outputs:
        print("  (none)")

    rows = _library_rows(library, job.id)
    print(f"\nlibrary      {len(rows)} row(s) for this job")
    for row in rows:
        outputs = json.loads(row["outputs_json"] or "[]")
        references = json.loads(row["references_json"] or "[]")
        print(
            f"  {row['status']}  {row['width']}×{row['height']}  "
            f"{len(outputs)} output(s)  {len(references)} reference(s)"
        )

    print("\nOK" if ok else "\nFAILED")
    return 0 if ok else 1


def _dimensions(path: Path) -> str:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return f"{image.size[0]}×{image.size[1]} {image.mode}"
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return f"unreadable ({exc})"


def _library_rows(library, job_id: str) -> list[dict]:
    cursor = library._conn.execute("SELECT * FROM generations WHERE id = ?", (job_id,))
    return [dict(row) for row in cursor.fetchall()]


if __name__ == "__main__":
    raise SystemExit(main())
