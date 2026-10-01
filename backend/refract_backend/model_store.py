"""Model sources: what can be run, what is already on disk, and how to prepare more.

Three ways to get an MLX 4-bit Qwen-Image-2.1 onto this machine, all reachable from the
app's Models pane:

* `mlx-q4`      the Uncensored community model: `abenzerps/Qwen-Image-2.1-Uncensored-GGUF`'s
                4-bit MLX transformer (4.0 GB) paired with a 4-bit Qwen3-VL text encoder.
                The GGUF-first repository has no 4-bit encoder of its own, so the encoder
                comes from an mflux-format conversion, and Prepare grafts in the visual
                tower editing needs plus the configs, processor and VAE the upstream base
                model is authoritative for. The staged result is ~11 GB and loads as-is.
                mflux's main branch loads a pre-quantised text encoder; the 0.20.0 release
                does not, which is why this app pins a git revision.
* `upstream-q4` the original `Qwen/Qwen-Image-2.1` (~33 GB, bf16 text encoder) with the
                transformer quantised to 4-bit at load.
* `prepared`    the result of "Prepare": any of the above, written out into a local
                directory so later launches skip downloading (and, for upstream, skip
                re-quantising).
* `custom`      a directory you built with "Install custom weights", i.e. a base model
                whose transformer was replaced by a vetted third-party pack.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .paths import (
    data_root,
    dir_size_bytes,
    hf_cache_dir,
    hf_repo_cache_dir,
    human_bytes,
    models_dir,
    storage_report,
)

ProgressFn = Callable[[dict[str, Any]], None]

# The community 4-bit pack ships quantised weights and nothing else: no per-component
# `config.json`, and mflux refuses to build the module tree without them. Those files are
# architecture descriptions of a few kilobytes, and the pack is a conversion of the same
# upstream weights, so they are taken from the base model and the result is verified against
# the live mflux module tree (see ingest.analyze) before anything runs.
# The community 4-bit pack ships quantised weights and nothing else: no per-component
# `config.json` (mflux refuses to build the module tree without them), no Qwen2-VL
# preprocessor settings, and a VAE saved in an older mflux module layout that main no longer
# accepts (`decoder.conv_in.conv.weight` where it now expects `decoder.conv_in.weight`).
# Everything the pack is missing is kilobytes or, for the VAE, the original dense weights —
# all of it is authoritative in the base model, so a staged checkpoint mixes pack weights for
# the parts it stores well (transformer, text encoder) with base files for the rest.
# The Uncensored 4-bit export is the model this app runs. Its repository is GGUF-first: the
# only MLX 4-bit artefact is a single transformer file, and its text encoders are a 17.5 GB
# bf16 file or a ComfyUI int8 layout mflux cannot read. Refract therefore takes the
# transformer from there and the 4-bit Qwen3-VL text encoder from the mlx-community mflux
# pack (the same upstream encoder, converted for mflux), with the visual tower the encoder
# needs plus every component config, the processor and the VAE from the upstream base model.
#: Where prepare_klein stages FLUX.2, and how a first-run setup recognises it as ready.
KLEIN_STAGED_NAME = "flux2-klein-4b"
UNCENSORED_REPO = "abenzerps/Qwen-Image-2.1-Uncensored-GGUF"
UNCENSORED_TRANSFORMER_FILE = "qwen-image-2.1-UC-MLX-4bit.safetensors"
TEXT_ENCODER_REPO = "mlx-community/Qwen-Image-2.1-mflux-q4"
TEXT_ENCODER_PATTERNS = ["text_encoder/*.safetensors", "text_encoder/*.json"]
BASE_CONFIG_REPO = "Qwen/Qwen-Image-2.1"
BASE_CONFIG_PATTERNS = [
    "model_index.json",
    "scheduler/*.json",
    "*/config.json",
    "vae/*.json",
    "vae/*.safetensors",
    "processor/**",
]
STAGED_PACK_NAME = "qwen-image-2.1-mlx-q4"

# Components that must carry a component config before mflux will build a module tree.
CONFIG_COMPONENTS = ("transformer", "text_encoder", "vae")

WEIGHT_PATTERNS = [
    "vae/*.safetensors",
    "vae/*.json",
    "transformer/*.safetensors",
    "transformer/*.json",
    "text_encoder/*.safetensors",
    "text_encoder/*.json",
    "processor/**",
]


@dataclass
class SourceSpec:
    id: str
    label: str
    detail: str
    repo_id: str | None
    quantize: int | None
    approx_bytes: int
    notes: list[str] = field(default_factory=list)
    kind: str = "repo"  # repo | local
    #: Which mflux model family to instantiate. Qwen-Image and Flux2 take different classes,
    #: different weights and different generate_image signatures, so the runner branches on
    #: this rather than guessing from the source id.
    family: str = "qwen21"
    #: FLUX.2 Klein is a distilled few-step model; its own default is 4 steps, not 40.
    default_steps: int = 40
    #: FLUX.2's edit path has no output_resolution parameter and no negative-prompt support.
    supports_negative_prompt: bool = True
    supports_reference_size_match: bool = True
    #: Which job kind installs this source. FLUX.2 is assembled rather than downloaded, so it
    #: does not go through the generic download/prepare pair.
    install_job: str = "download"


SOURCES: dict[str, SourceSpec] = {
    "mlx-q4": SourceSpec(
        id="mlx-q4",
        label="Qwen-Image-2.1 Uncensored MLX 4-bit",
        detail="The Uncensored 4-bit MLX transformer, with a 4-bit Qwen3-VL text encoder",
        repo_id=UNCENSORED_REPO,
        quantize=None,
        approx_bytes=14_000_000_000,
        notes=[
            "The image model is abenzerps/Qwen-Image-2.1-Uncensored-GGUF's "
            f"{UNCENSORED_TRANSFORMER_FILE} (4.0 GB, MLX 4-bit affine).",
            "That repository has no 4-bit text encoder — only bf16 (17.5 GB) and a ComfyUI "
            f"int8 layout mflux cannot read — so the 4-bit Qwen3-VL encoder comes from "
            f"{TEXT_ENCODER_REPO} and the language model stays 4-bit either way.",
            "Prepare also grafts the Qwen3-VL visual tower (~1.2 GB, dense) from "
            f"{BASE_CONFIG_REPO}: the 4-bit encoder conversion was made with mflux's "
            "text-only graph, and reference editing cannot see the reference images without it.",
        ],
    ),
    "upstream-q4": SourceSpec(
        id="upstream-q4",
        label="Upstream + 4-bit at load",
        detail="Qwen/Qwen-Image-2.1 with the transformer quantised to 4-bit when it loads",
        repo_id="Qwen/Qwen-Image-2.1",
        quantize=4,
        approx_bytes=33_100_000_000,
        notes=[
            "The text encoder stays bf16 (17.5 GB resident) because mflux deliberately "
            "does not quantise it: its weight definition marks that component "
            "skip_quantization with 'quantization causes significant semantic degradation'.",
            "First load re-quantises the 14.2 GB transformer. Use Prepare to write the "
            "quantised result to disk once and skip that work on every later launch.",
        ],
    ),
    "prepared": SourceSpec(
        id="prepared",
        label="Prepared local model",
        detail="A model directory written by Prepare, loaded straight from disk",
        repo_id=None,
        quantize=4,
        approx_bytes=0,
        kind="local",
        notes=[
            "Fastest warm start and the base directory used by custom-weight installs.",
            "Preparing the 4-bit source relabels the transformer, grafts the visual "
            "encoder into the text encoder and writes ~11 GB, so it runs once.",
        ],
    ),
    "flux2-klein-4b": SourceSpec(
        id="flux2-klein-4b",
        label="FLUX.2 Klein 4B (ablated encoder)",
        detail="Flux2 Klein 4B with the Huihui-ablated Qwen3-4B text encoder",
        repo_id=None,
        quantize=None,
        approx_bytes=13_000_000_000,
        kind="local",
        family="flux2",
        install_job="prepare-klein",
        default_steps=4,
        supports_negative_prompt=False,
        supports_reference_size_match=False,
        notes=[
            "A different model family from Qwen-Image-2.1: Flux2 Klein 4B is a distilled "
            "few-step model, so it defaults to 4 steps rather than 40.",
            "Staged by prepare_klein. Its text encoder is Qwen3-4B and text-only, a "
            "different architecture from Qwen-Image's Qwen3-VL.",
            "The ablated encoder removes prompt refusal but adds no visual knowledge, so "
            "per its own card explicit anatomy is out of reach for it.",
        ],
    ),
    "custom": SourceSpec(
        id="custom",
        label="Custom weights",
        detail="A local directory installed from a third-party transformer pack",
        repo_id=None,
        quantize=None,
        approx_bytes=0,
        kind="local",
        notes=[
            "Built by Models > Install custom weights: a base model directory with its "
            "transformer component replaced by a validated pack.",
            "Validation compares every tensor name and shape against the live mflux module "
            "tree and then loads the pack through mflux itself before it is used.",
        ],
    ),
}


# --------------------------------------------------------------------------- inventory
def _complete_snapshot(repo_id: str) -> Path | None:
    """A cached HF snapshot containing every weight subtree, or None."""
    from huggingface_hub import snapshot_download

    try:
        path = snapshot_download(
            repo_id,
            allow_patterns=WEIGHT_PATTERNS,
            local_files_only=True,
            cache_dir=str(hf_cache_dir()),
        )
    except Exception:
        return None
    root = Path(path)
    for subdir in ("transformer", "text_encoder", "vae", "processor"):
        candidate = root / subdir
        if not candidate.is_dir() or not any(candidate.iterdir()):
            return None
    return root


def repo_size_bytes(repo_id: str) -> int:
    """Bytes actually stored for a cached repo.

    Measured on `blobs` rather than on the snapshot directory: snapshots hold symlinks
    into blobs, so sizing the whole cache would count every weight twice.
    """
    cache = hf_repo_cache_dir(repo_id)
    blobs = cache / "blobs"
    return dir_size_bytes(blobs if blobs.is_dir() else cache)


def installed_models() -> list[dict[str, Any]]:
    """Directories under the models dir that look like complete model roots."""
    found = []
    root = models_dir()
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            has_weights = any(
                (entry / sub).is_dir() and any((entry / sub).glob("*.safetensors"))
                for sub in ("transformer", "text_encoder")
            )
            if not has_weights:
                continue
            manifest_path = entry / "refract-manifest.json"
            manifest = {}
            if manifest_path.is_file():
                try:
                    manifest = json.loads(manifest_path.read_text())
                except ValueError:
                    manifest = {}
            found.append(
                {
                    "name": entry.name,
                    "path": str(entry),
                    "size_bytes": dir_size_bytes(entry),
                    "bits": manifest.get("bits"),
                    "created_at": manifest.get("created_at"),
                    "origin": manifest.get("origin"),
                    "customised": bool(manifest.get("custom_source")),
                }
            )
    return found


def list_sources() -> list[dict[str, Any]]:
    specs = []
    installed = {item["path"]: item for item in installed_models()}
    staged = staged_pack_dir()
    for spec in SOURCES.values():
        entry = {
            "id": spec.id,
            "label": spec.label,
            "detail": spec.detail,
            "repo_id": spec.repo_id,
            "approx_download_bytes": spec.approx_bytes,
            "notes": list(spec.notes),
            "available": False,
            "location": None,
            "size_bytes": 0,
            "quantized_bits": spec.quantize,
            "install_job": spec.install_job,
        }
        if spec.kind == "repo" and spec.repo_id:
            if spec.id == "mlx-q4":
                # This source spans three repositories, so its availability is the transformer
                # file plus the text encoder shards, not any one snapshot being complete. And
                # downloaded is not usable: the components have to be staged and verified
                # before mflux will load them at all.
                downloaded = pack_snapshot()
                entry["available"] = downloaded is not None
                entry["location"] = str(downloaded) if downloaded else None
                entry["size_bytes"] = sum(
                    repo_size_bytes(repo)
                    for repo in (UNCENSORED_REPO, TEXT_ENCODER_REPO, BASE_CONFIG_REPO)
                )
                entry["staged"] = staged is not None
                entry["staged_path"] = str(staged) if staged else None
                if staged:
                    try:
                        manifest = json.loads((staged / "refract-manifest.json").read_text())
                    except (OSError, ValueError):
                        manifest = {}
                    provenance = manifest.get("encoder_provenance") or {}
                    if provenance.get("abliterated"):
                        entry["detail"] = "Original Uncensored MLX 4-bit transformer + Heretic-abliterated MLX 4-bit encoder"
                        entry["notes"] = [
                            f"Transformer unchanged: {UNCENSORED_REPO}/{UNCENSORED_TRANSFORMER_FILE}.",
                            "The transformer model card describes original upstream weights without a safety checker, not directional abliteration.",
                            f"Encoder: {provenance.get('repo_id')}@{provenance.get('revision')} — Heretic directional ablation, converted to MLX 4-bit; vision tower preserved.",
                            "No application-level prompt refusal or output safety filter is applied. Abliteration does not guarantee every output.",
                        ]
                    else:
                        entry["notes"].append("Current encoder is stock, not abliterated. The uncensored transformer label does not imply encoder abliteration.")
            else:
                snapshot = _complete_snapshot(spec.repo_id)
                entry["available"] = snapshot is not None
                entry["location"] = str(snapshot) if snapshot else None
                entry["size_bytes"] = repo_size_bytes(spec.repo_id)
        else:
            # Klein is a local source, but its staged directory is not a "model" in the
            # sense installed_models() means — that scan finds whole checkpoints to load.
            # Its availability is its own staged pack, or it would report ready on the
            # strength of some unrelated directory in models/.
            if spec.id == "flux2-klein-4b":
                staged_klein = models_dir() / KLEIN_STAGED_NAME
                ready = staged_klein.is_dir() and (staged_klein / "transformer").is_dir()
                entry["available"] = ready
                entry["location"] = str(staged_klein) if ready else None
                entry["size_bytes"] = dir_size_bytes(staged_klein) if ready else 0
                specs.append(entry)
                continue

            # Read `customised` only for the custom source; subscripting with the conditional
            # expression itself looked up `item[True]` and raised the moment a model existed.
            models = [
                item
                for item in installed.values()
                if (item["customised"] if spec.id == "custom" else True)
            ]
            if spec.id == "prepared":
                models = [item for item in models if not item["customised"]]
            entry["available"] = bool(models)
            entry["location"] = models[0]["path"] if models else None
            entry["size_bytes"] = sum(item["size_bytes"] for item in models)
            entry["installed"] = models
        specs.append(entry)
    return specs


# --------------------------------------------------------------------------- staging
def _hub_download(repo_id: str, patterns: list[str], progress: ProgressFn | None = None, label: str = "") -> Path:
    """A complete local snapshot for `patterns`, reusing the cache when it already is one."""
    from huggingface_hub import snapshot_download

    cache = str(hf_cache_dir())
    try:
        return Path(snapshot_download(repo_id, allow_patterns=patterns, local_files_only=True, cache_dir=cache))
    except Exception:
        pass
    if progress and label:
        progress({"phase": "downloading", "message": f"fetching {label} from {repo_id}"})
    return Path(snapshot_download(repo_id, allow_patterns=patterns, cache_dir=cache))


def _single_file(repo_id: str, filename: str, progress: ProgressFn | None = None, label: str = "") -> Path:
    """One file from the Hub, from the cache when it is already there."""
    from huggingface_hub import hf_hub_download

    cache = str(hf_cache_dir())
    try:
        return Path(hf_hub_download(repo_id, filename, local_files_only=True, cache_dir=cache))
    except Exception:
        pass
    if progress and label:
        progress({"phase": "downloading", "message": f"fetching {label} from {repo_id}"})
    return Path(hf_hub_download(repo_id, filename, cache_dir=cache))


def ensure_uncensored_transformer(progress: ProgressFn | None = None) -> Path:
    """The Uncensored repository's 4-bit MLX transformer file."""
    return _single_file(UNCENSORED_REPO, UNCENSORED_TRANSFORMER_FILE, progress, "the 4-bit transformer")


def text_encoder_snapshot(progress: ProgressFn | None = None) -> Path:
    """The mflux-format 4-bit Qwen3-VL text encoder."""
    return _hub_download(TEXT_ENCODER_REPO, TEXT_ENCODER_PATTERNS, progress, "the 4-bit text encoder")


def pack_snapshot() -> Path | None:
    """True when every weight file the 4-bit source needs is already downloaded."""
    from huggingface_hub import hf_hub_download

    try:
        transformer = hf_hub_download(
            UNCENSORED_REPO, UNCENSORED_TRANSFORMER_FILE, local_files_only=True, cache_dir=str(hf_cache_dir())
        )
    except Exception:
        return None
    try:
        root = text_encoder_snapshot()
    except Exception:
        return None
    if not any((root / "text_encoder").glob("*.safetensors")):
        return None
    return Path(transformer)


def base_config_snapshot(repo_id: str = BASE_CONFIG_REPO, progress: ProgressFn | None = None) -> Path | None:
    """The base model's component configs (a few kilobytes), downloading them if needed."""
    try:
        return _hub_download(repo_id, BASE_CONFIG_PATTERNS, progress, "component configs")
    except Exception:
        return None


BASE_VISUAL_INDEX = "text_encoder/model.safetensors.index.json"


def ensure_base_visual_shards(repo_id: str = BASE_CONFIG_REPO, progress: ProgressFn | None = None) -> list[str]:
    """Download only the base text_encoder shard(s) that carry the Qwen3-VL visual tower.

    The base text encoder is 17.5 GB over four shards, but every `visual.*` tensor lives in
    one of them, so a staged checkpoint needs a few gigabytes rather than all of it.
    """
    index_dir = _hub_download(repo_id, [BASE_VISUAL_INDEX], progress, "the base text_encoder index")
    index = json.loads((index_dir / BASE_VISUAL_INDEX).read_text())
    shards = sorted({shard for key, shard in (index.get("weight_map") or {}).items() if "visual." in key})
    if not shards:
        return []
    _hub_download(repo_id, [f"text_encoder/{shard}" for shard in shards], progress, "the visual encoder")
    return shards


def base_visual_weights(repo_id: str = BASE_CONFIG_REPO, progress: ProgressFn | None = None) -> dict[str, Any]:
    """Load the base checkpoint's Qwen3-VL visual tower, and only that."""
    import mlx.core as mx

    shards = ensure_base_visual_shards(repo_id, progress)
    if not shards:
        return {}
    root = _hub_download(repo_id, [BASE_VISUAL_INDEX])
    weights: dict[str, Any] = {}
    for shard in shards:
        data, _meta = mx.load(str(root / "text_encoder" / shard), return_metadata=True)
        weights.update(dict(data.items()))
        del data
    return weights


def _copy_file(source: Path, target: Path) -> None:
    """Replace `target` with a writable copy of `source`.

    Downloads in the Hugging Face cache are read-only (and `source` is usually a symlink into
    its blob store), so a plain copy2 onto an existing hard link fails and would leave the
    staged file read-only even when it succeeds.
    """
    if target.exists() or target.is_symlink():
        target.unlink()
    shutil.copy2(source, target)
    os.chmod(target, 0o644)


def _link_or_copy(source: Path, target: Path) -> str:
    """Hard link when the two paths share a volume, copy when they do not.

    Links are what make staging cheap: the pack stays in the download cache and the model
    directory is a second set of names for the same bytes, so a "prepared" 4-bit pack costs
    nothing instead of another 9 GB.
    """
    origin = source.resolve()
    try:
        size = origin.stat().st_size
    except OSError:
        size = -1
    if target.is_symlink() or target.is_file():
        if target.exists() and target.stat().st_size == size:
            return "kept"
        target.unlink()
    try:
        os.link(origin, target)
        return "linked"
    except OSError:
        shutil.copy2(origin, target)
        return "copied"


def stage_pack(name: str | None = None, progress: ProgressFn | None = None) -> dict[str, Any]:
    """Materialise the 4-bit source as a complete mflux checkpoint and verify it loads.

    Called from "Prepare" in the Models pane and, for the same source, from the generate
    path. The result is a directory mflux can load as-is: the Uncensored 4-bit transformer
    relabelled into mflux's current module tree, the 4-bit Qwen3-VL text encoder with the
    visual tower grafted in, and the component configs, processor and VAE the upstream base
    model is authoritative for.
    """
    import mlx.core as mx

    from .ingest import (
        analyze,
        assemble_reference_text_encoder,
        assemble_transformer_from_file,
        repair_quantization,
        verify_loads,
    )

    def note(phase: str, message: str) -> None:
        if progress:
            progress({"phase": phase, "message": message})

    target = models_dir() / (name or STAGED_PACK_NAME)
    # Never restage over a deliberately installed encoder (or a complete verified pack).
    if staged_pack_dir(name):
        try:
            manifest = json.loads((target / "refract-manifest.json").read_text())
        except (OSError, ValueError):
            manifest = {}
        if manifest.get("verification"):
            return {**manifest, "path": str(target), "already": True}
    transformer_file = ensure_uncensored_transformer(progress)
    encoder_source = text_encoder_snapshot(progress)
    base = base_config_snapshot(BASE_CONFIG_REPO, progress)
    if base is None:
        raise ValueError(f"{BASE_CONFIG_REPO} is required for the component configs, processor and VAE")

    target.mkdir(parents=True, exist_ok=True)
    components: dict[str, str] = {}

    # Transformer: one 4 GB file, relabelled and re-sharded so mflux's index-driven loader
    # (and the dense-layer repair below, which rewrites shards in place) can work on it.
    transformer_dir = target / "transformer"
    transformer_dir.mkdir(parents=True, exist_ok=True)
    _copy_file(base / "transformer" / "config.json", transformer_dir / "config.json")
    transformer = assemble_transformer_from_file(transformer_dir, transformer_file, progress)
    components["transformer"] = f"{UNCENSORED_REPO} ({UNCENSORED_TRANSFORMER_FILE})"

    # Text encoder: the mflux-format 4-bit shards, hard linked, then completed with the
    # visual tower. The conversion used mflux's text-only graph, so editing cannot see the
    # reference images until those weights are there.
    encoder_dir = target / "text_encoder"
    encoder_dir.mkdir(parents=True, exist_ok=True)
    linked = 0
    for entry in sorted((encoder_source / "text_encoder").iterdir()):
        if entry.is_file() or entry.is_symlink():
            if _link_or_copy(entry, encoder_dir / entry.name) in ("linked", "copied"):
                linked += 1
    _copy_file(base / "text_encoder" / "config.json", encoder_dir / "config.json")
    note("staging", "adding the visual encoder from the base model")
    visual_weights = base_visual_weights(BASE_CONFIG_REPO, progress)
    encoder = assemble_reference_text_encoder(encoder_dir, visual_weights, progress=progress)
    del visual_weights
    mx.clear_cache()
    components["text_encoder"] = f"{TEXT_ENCODER_REPO} (4-bit) + {BASE_CONFIG_REPO} visual encoder"

    # VAE and everything the reference pipeline reads besides weights come from the base
    # model: the pack's VAE is in an older mflux layout and it ships no preprocessor config.
    vae_dir = target / "vae"
    if vae_dir.exists():
        shutil.rmtree(vae_dir)
    vae_dir.mkdir(parents=True, exist_ok=True)
    for entry in sorted((base / "vae").iterdir()):
        if entry.is_file():
            _copy_file(entry, vae_dir / entry.name)
    components["vae"] = BASE_CONFIG_REPO
    for entry in sorted((base / "processor").iterdir()):
        if entry.is_file():
            (target / "processor").mkdir(parents=True, exist_ok=True)
            _copy_file(entry, target / "processor" / entry.name)
    for name_ in ("model_index.json",):
        if (base / name_).is_file():
            _copy_file(base / name_, target / name_)
    if (base / "scheduler").is_dir():
        shutil.copytree(base / "scheduler", target / "scheduler", dirs_exist_ok=True)

    note("repairing", "checking which layers mflux keeps dense at this bit width")
    repaired = {
        component: repair_quantization(target, component=component, progress=progress)
        for component in ("transformer", "text_encoder")
    }

    reports: dict[str, Any] = {}
    for component in ("transformer", "text_encoder"):
        note("verifying", f"checking {component} against the mflux module tree")
        report = analyze(target / component, component=component)
        if not report.get("ok"):
            raise ValueError(
                f"the staged {component} does not match the mflux Qwen-Image-2.1 module tree: "
                f"{report['errors'] or report['shape_mismatches']['examples'][:3]}"
            )
        if not report["quantization"]["quantized"]:
            raise ValueError(f"the staged {component} is not quantised; mflux would load it as dense")
        reports[component] = report

    verification = {
        component: verify_loads(target, component=component) for component in ("transformer", "text_encoder")
    }
    failed = [name_ for name_, value in verification.items() if not value.get("ok")]
    if failed:
        raise ValueError(
            "mflux cannot load the staged checkpoint: "
            + "; ".join(f"{name_}: {verification[name_]['error']}" for name_ in failed)
        )
    report = reports["transformer"]

    manifest = {
        "name": target.name,
        "origin": UNCENSORED_REPO,
        "base_configs_from": BASE_CONFIG_REPO,
        "source_id": "mlx-q4",
        "bits": report["quantization"]["bits"],
        "group_size": report["quantization"]["group_size"],
        "quantized_layers": report["quantization"]["layers"],
        "staged_at": datetime.now(timezone.utc).isoformat(),
        "mflux_version": _mflux_version(),
        "components": components,
        "transformer": transformer,
        # Layers the pack stored quantised but mflux keeps dense, expanded back to bf16.
        "expanded_layers": {n: e["layers"] for n, e in repaired.items() if e["repaired"]},
        "visual_encoder": encoder,
        "verification": {
            n: {
                "bits": v.get("bits"),
                "parameters": v.get("parameters"),
                "module_tree": v.get("module_tree"),
            }
            for n, v in verification.items()
        },
    }
    manifest.update(
        {
            "path": str(target),
            "size_bytes": dir_size_bytes(target),
            "linked_files": linked,
            "bytes_linked": dir_size_bytes(target),
            "staged": True,
            "already": False,
        }
    )
    write_manifest(target, manifest)
    if progress:
        progress({"phase": "done", "message": f"staged {target.name} ({human_bytes(manifest['size_bytes'])})"})
    return manifest


def staged_pack_dir(name: str | None = None) -> Path | None:
    """An already-staged 4-bit checkpoint, or None."""
    target = models_dir() / (name or STAGED_PACK_NAME)
    if not target.is_dir():
        return None
    if not all((target / component / "config.json").is_file() for component in CONFIG_COMPONENTS):
        return None
    if not any((target / "vae").glob("*.safetensors")):
        return None
    # A staged checkpoint whose text encoder is still the text-only one would load and then
    # fail on the first reference image, so it does not count as prepared. The transformer
    # index is checked too: it is what proves the Uncensored file was folded in.
    for component in ("text_encoder", "transformer"):
        index_path = target / component / "model.safetensors.index.json"
        try:
            metadata = json.loads(index_path.read_text()).get("metadata") or {}
        except (OSError, ValueError):
            return None
        if component == "text_encoder" and metadata.get("refract_reference_encoder") != "true":
            return None
        if component == "transformer" and not metadata.get("refract_source"):
            return None
    return target


# --------------------------------------------------------------------------- download
class _DirSizeMonitor:
    """Samples a directory's size so long downloads can report real bytes."""

    def __init__(self, path: Path, total_bytes: int, progress: ProgressFn | None, phase: str, label: str):
        self.path = path
        self.total_bytes = total_bytes
        self.progress = progress
        self.phase = phase
        self.label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_DirSizeMonitor":
        if self.progress is None:
            return self
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        last = -1
        while not self._stop.wait(1.0):
            size = dir_size_bytes(self.path)
            if size == last:
                continue
            last = size
            percent = round(100 * size / self.total_bytes, 1) if self.total_bytes else None
            self.progress(
                {
                    "phase": self.phase,
                    "message": f"{self.label} {human_bytes(size)}"
                    + (f" of {human_bytes(self.total_bytes)}" if self.total_bytes else ""),
                    "downloaded_bytes": size,
                    "total_bytes": self.total_bytes or None,
                    "percent": percent,
                }
            )


def estimate_repo_bytes(repo_id: str, patterns: list[str] | None = None) -> int:
    """Best-effort total size of the files we need, from the Hub API."""
    try:
        from huggingface_hub import HfApi

        patterns = patterns or WEIGHT_PATTERNS
        info = HfApi().model_info(repo_id, files_metadata=True)
        total = 0
        for sibling in info.siblings or []:
            name = sibling.rfilename
            if any(
                name == pattern or name.startswith(pattern.replace("/**", "/").replace("/*", "/"))
                for pattern in patterns
            ):
                total += sibling.size or 0
        return total
    except Exception:
        return 0


def download_source(source_id: str, progress: ProgressFn | None = None) -> dict[str, Any]:
    """Fetch a repo-backed source into the Hugging Face cache (resumable, cached).

    The 4-bit source spans three repositories (transformer, text encoder, base configs and
    visual tower), so "downloaded" has to mean all of them are here — otherwise Prepare would
    still go to the network at the moment the user expects everything to be on disk.
    """
    from huggingface_hub import snapshot_download

    spec = SOURCES.get(source_id)
    if spec is None or not spec.repo_id:
        raise ValueError(f"{source_id!r} is not a downloadable source")

    if source_id == "mlx-q4":
        transformer = ensure_uncensored_transformer(progress)
        encoder = text_encoder_snapshot(progress)
        base_config_snapshot(BASE_CONFIG_REPO, progress)
        ensure_base_visual_shards(BASE_CONFIG_REPO, progress)
        if not any((encoder / "text_encoder").glob("*.safetensors")):
            raise ValueError(f"{TEXT_ENCODER_REPO} did not provide any text_encoder shards")
        return {"downloaded": True, "cached": False, "path": str(transformer)}

    if _complete_snapshot(spec.repo_id):
        return {"downloaded": True, "cached": True, "path": str(_complete_snapshot(spec.repo_id))}

    total = estimate_repo_bytes(spec.repo_id) or spec.approx_bytes
    cache = hf_repo_cache_dir(spec.repo_id)
    if progress:
        progress(
            {
                "phase": "downloading",
                "message": f"downloading {spec.repo_id} ({human_bytes(total)})",
                "total_bytes": total,
            }
        )
    with _DirSizeMonitor(cache, total, progress, "downloading", "downloaded"):
        # cache_dir is passed explicitly rather than left to HF_HOME: the app decides where
        # weights live (see paths.data_root), and the library's own default is the internal
        # disk, which is the one place a 33 GB download must not end up.
        path = snapshot_download(spec.repo_id, allow_patterns=WEIGHT_PATTERNS, cache_dir=str(hf_cache_dir()))
    return {"downloaded": True, "cached": False, "path": str(path)}


# --------------------------------------------------------------------------- prepare
def prepare_source(source_id: str, name: str | None = None, progress: ProgressFn | None = None) -> dict[str, Any]:
    """Materialise a source as a local model directory.

    Downloads if needed, then loads the model and writes it with `save_model()`. The point
    is the *next* launch: a quantised directory loads without re-quantising (upstream-q4)
    and without re-hitting the network (both).
    """
    from .runner import MfluxRunner

    spec = SOURCES.get(source_id)
    if spec is None or spec.kind != "repo":
        raise ValueError(f"{source_id!r} cannot be prepared")

    if source_id == "mlx-q4":
        # Nothing to load and re-save here: the transformer is already 4-bit, so preparing it
        # is a staging step (relabel, graft the visual tower, repair, verify) with no model
        # load and no re-quantisation.
        return stage_pack(name=name, progress=progress)

    target = models_dir() / (name or f"qwen-image-2.1-{source_id}")
    if (target / "transformer").is_dir() and any((target / "transformer").glob("*.safetensors")):
        return {"prepared": True, "already": True, "path": str(target), "size_bytes": dir_size_bytes(target)}

    download_source(source_id, progress)

    if progress:
        progress({"phase": "loading", "message": "loading the model into MLX to export it"})
    runner = MfluxRunner()
    model, bits = runner.build_model(model_path=None, quantize=spec.quantize, source_id=source_id)

    if progress:
        progress({"phase": "saving", "message": "writing the prepared model directory"})
    with _DirSizeMonitor(target, repo_size_bytes(spec.repo_id or "") or spec.approx_bytes, progress, "saving", "written"):
        model.save_model(str(target))

    manifest = {
        "name": target.name,
        "origin": spec.repo_id,
        "source_id": source_id,
        "bits": bits or spec.quantize,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mflux_version": _mflux_version(),
    }
    (target / "refract-manifest.json").write_text(json.dumps(manifest, indent=2))
    runner.release(model)
    if progress:
        progress({"phase": "done", "message": f"prepared {target.name}"})
    return {
        "prepared": True,
        "already": False,
        "path": str(target),
        "size_bytes": dir_size_bytes(target),
        "manifest": manifest,
    }


def write_manifest(directory: Path, payload: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "refract-manifest.json").write_text(json.dumps(payload, indent=2))


def _mflux_version() -> str:
    from .sysinfo import mflux_version

    return mflux_version()


def delete_path(path: str, confirm: bool = False) -> dict[str, Any]:
    """Delete a model directory or a cached repo. Requires explicit confirmation."""
    import shutil

    if not confirm:
        raise ValueError("deletion requires confirm=true")
    target = Path(path).expanduser().resolve()
    models_root = models_dir().resolve()
    hf_root = hf_cache_dir().resolve()
    allowed = str(target).startswith(str(models_root)) or str(target).startswith(str(hf_root))
    if not allowed or target == models_root or target == hf_root:
        raise ValueError(f"refusing to delete {target}: outside the app's model and cache directories")
    if not target.exists():
        return {"deleted": False, "reason": "not found", "path": str(target)}
    size = dir_size_bytes(target)
    shutil.rmtree(target)
    return {"deleted": True, "path": str(target), "freed_bytes": size}


def disk_report() -> dict[str, Any]:
    return {
        "data_root": str(data_root()),
        "models_dir": str(models_dir()),
        "models_size_bytes": dir_size_bytes(models_dir()),
        "hf_cache_dir": str(hf_cache_dir()),
        "hf_cache_size_bytes": dir_size_bytes(hf_cache_dir()),
        "storage": storage_report(),
        "installed": installed_models(),
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sleep_briefly(seconds: float = 0.05) -> None:
    """Small helper the tests use to let the worker thread make progress."""
    time.sleep(seconds)
