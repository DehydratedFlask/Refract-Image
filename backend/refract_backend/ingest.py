"""Inspection and installation of third-party checkpoints (the "custom weights" path).

A community quant such as `qwen-image-2.1-UC-MLX-4bit.safetensors` is a bare file: no
config, no index, no quantisation metadata. mflux cannot load that even when every tensor
is correct, because it decides between two on-disk layouts by reading safetensors
`__metadata__`, and it reads a mflux-format checkpoint's shards *through* its
`model.safetensors.index.json`.

So this module does three things, in order, and never skips the third:

1. `analyze()` reads only the safetensors headers and compares tensor names, dtypes and
   shapes against the live mflux module tree. Key prefixes (`transformer.`, `model.`,
   `diffusion_model.`) and mflux's own `modulation.1` -> `modulation.layers.1` rename are
   normalised, and quantised layers have their bit width and group size solved from their
   packed shapes rather than assumed.
2. `install()` rewrites the header (adding the quantisation metadata mflux looks for) and
   copies the tensor payload byte-for-byte — no dequantise/requantise round trip, so the
   pack you point at is the pack you run.
3. `verify_loads()` then loads it back through mflux's own `WeightLoader` +
   `WeightApplier` and reports the resulting parameter count. A pack that fails here is
   reported as a failure with mflux's message, and the caller falls back to a source that
   is known to work.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
from pathlib import Path
from typing import Any, Iterable

# Tensor names that exist in older text-only exports but are deterministic, non-learned
# buffers in the current one; mflux drops them, so the comparison must too.
IGNORED_SUFFIXES = (
    "time_text_embed.time_proj.freqs",
    "pos_embed.cos_tables.0",
    "pos_embed.cos_tables.1",
    "pos_embed.cos_tables.2",
    "pos_embed.sin_tables.0",
    "pos_embed.sin_tables.1",
    "pos_embed.sin_tables.2",
)

# Wrappers seen in the wild around the same tensor set.
STRIPPABLE_PREFIXES = ("model.diffusion_model.", "diffusion_model.", "transformer.", "model.")

QUANT_SUFFIXES = (".scales", ".biases")


# --------------------------------------------------------------------------- headers
def read_header(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Return (metadata, {tensor: {dtype, shape, nbytes}}) without touching tensor data."""
    with path.open("rb") as handle:
        raw_len = handle.read(8)
        if len(raw_len) != 8:
            raise ValueError(f"{path.name} is too short to be a safetensors file")
        header_len = struct.unpack("<Q", raw_len)[0]
        if header_len <= 0 or header_len > 200_000_000:
            raise ValueError(f"{path.name} has an implausible safetensors header ({header_len} bytes)")
        raw_header = handle.read(header_len)
    try:
        header = json.loads(raw_header.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"{path.name} has an unreadable safetensors header: {exc}") from exc

    metadata = header.pop("__metadata__", {}) or {}
    tensors: dict[str, dict[str, Any]] = {}
    for name, info in header.items():
        if not isinstance(info, dict) or "shape" not in info:
            continue
        shape = tuple(int(dim) for dim in info["shape"])
        start, end = (int(v) for v in info["data_offsets"])
        tensors[name] = {
            "dtype": str(info.get("dtype", "?")),
            "shape": shape,
            "nbytes": end - start,
        }
    return metadata, tensors


def _safetensors_files(path: Path) -> list[Path]:
    if path.is_file():
        if path.suffix != ".safetensors":
            raise ValueError(f"{path.name} is not a .safetensors file")
        return [path]
    if not path.is_dir():
        raise ValueError(f"{path} does not exist")
    files = sorted(p for p in path.rglob("*.safetensors") if not p.name.startswith("._"))
    if not files:
        raise ValueError(f"no .safetensors files found under {path}")
    return files


def scan(path: Path) -> dict[str, Any]:
    """Merge the headers of every safetensors file at `path` into one view."""
    files = _safetensors_files(path)
    tensors: dict[str, dict[str, Any]] = {}
    metadata: dict[str, Any] = {}
    duplicates: list[str] = []
    for file in files:
        file_metadata, file_tensors = read_header(file)
        metadata.update(file_metadata)
        for name, info in file_tensors.items():
            if name in tensors:
                duplicates.append(name)
            tensors[name] = {**info, "file": file.name}
    return {
        "source": str(path),
        "files": [str(f) for f in files],
        "metadata": metadata,
        "tensors": tensors,
        "duplicate_tensors": duplicates,
        "total_bytes": sum(info["nbytes"] for info in tensors.values()),
    }


# --------------------------------------------------------------------------- naming
def normalize_key(key: str) -> tuple[str, str | None]:
    """Map a checkpoint key onto mflux's module tree naming.

    Returns (normalized, stripped_prefix).
    """
    stripped = None
    for prefix in STRIPPABLE_PREFIXES:
        if key.startswith(prefix):
            candidate = key[len(prefix) :]
            stripped = prefix
            key = candidate
            break
    if key.startswith("modulation.1."):
        key = key.replace("modulation.1.", "modulation.layers.1.", 1)
    return key, stripped


def _is_ignored(key: str) -> bool:
    return any(key.endswith(suffix) for suffix in IGNORED_SUFFIXES)


# --------------------------------------------------------------------------- expectations
def component_config(path: str | Path) -> dict[str, Any] | None:
    """The `<component>/config.json` that describes this checkpoint, if it is there.

    The reference-editing pipeline builds its module tree from the component config, so that
    is the tree a pack has to match. A bare third-party `.safetensors` file has no config, and
    then the text-to-image class (which needs none) stands in — it describes the same
    architecture, so it is still a real comparison.
    """
    candidate = Path(path).expanduser()
    candidate = candidate / "config.json" if candidate.is_dir() else candidate.parent / "config.json"
    if not candidate.is_file():
        return None
    try:
        payload = json.loads(candidate.read_text())
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def transformer_module(config: dict[str, Any] | None = None):
    """A live, randomly initialised transformer: the reference class when a config is known."""
    if config is None:
        from mflux.models.qwen21.model.qwen21_transformer.qwen21_transformer import Qwen21Transformer

        return Qwen21Transformer()

    from mflux.models.qwen21.reference.model.qwen_image21_transformer.transformer import QwenImage21Transformer

    return QwenImage21Transformer(config)


def component_module(component: str, config: dict[str, Any] | None = None):
    """The module tree a component's checkpoint has to match."""
    if component == "transformer":
        return transformer_module(config)
    if component == "text_encoder":
        if config is None:
            from mflux.models.qwen21.model.qwen21_text_encoder.qwen21_text_encoder import Qwen21TextEncoder

            return Qwen21TextEncoder()

        from mflux.models.qwen21.reference.model.qwen_image21_text_encoder.text_encoder import (
            QwenImage21TextEncoder,
        )

        return QwenImage21TextEncoder(config)
    raise ValueError(f"unsupported component {component!r}")


def expected_module_keys(
    component: str = "transformer", config: dict[str, Any] | None = None
) -> dict[str, tuple[int, ...]]:
    """Flatten the live mflux module tree so validation compares against real shapes.

    This is the only place that touches mflux at import time in this module, so it is
    called lazily and its import error is surfaced as data rather than raised.
    """
    from mlx.utils import tree_flatten

    return {name: tuple(param.shape) for name, param in tree_flatten(component_module(component, config).parameters())}


# --------------------------------------------------------------------------- text encoder
TEXT_ENCODER_PREFIX = "language_model."


def text_encoder_transforms():
    """mflux's own key/value transforms for the reference text encoder.

    Taken from the live weight definition rather than reimplemented, so the staged keys can
    never drift from the loader that has to read them.
    """
    from mflux.models.qwen21.reference.weights.qwen_image21_weight_definition import (
        QwenImage21WeightDefinition,
    )

    return QwenImage21WeightDefinition.text_key, QwenImage21WeightDefinition.text_weight


def reference_text_encoder_key(raw: str, *, from_base: bool) -> str | None:
    """Map a checkpoint key onto the reference module tree (`language_model.*`/`visual.*`).

    The two sources spell the same tensors differently. The base checkpoint is Hugging Face
    layout (`model.language_model.*`, `model.visual.*`, plus an `lm_head` the diffusion model
    never uses), which is what mflux's `text_key`/`text_weight` are written for. The community
    pack was saved from mflux's text-only graph, so its keys are bare module names
    (`layers.0...`) that belong under `language_model.` and carry no visual tower at all.
    """
    text_key, _ = text_encoder_transforms()
    if from_base:
        return text_key(raw)
    key = raw if raw.startswith((TEXT_ENCODER_PREFIX, "visual.")) else TEXT_ENCODER_PREFIX + raw
    return text_key(key)


def _expected_text_encoder_keys(component: Path) -> dict[str, tuple[int, ...]]:
    return expected_module_keys("text_encoder", component_config(component))


def _accepts(expected: dict[str, tuple[int, ...]], key: str) -> bool:
    """Whether the module tree has a home for `key`.

    Quantised linears carry their own `scales`/`biases` beside a `weight` that is already in
    the expected set, so those two suffixes are kept even though only `weight` is listed.
    Everything else the checkpoint happens to store — `lm_head`, the pooled `norm.weight` the
    reference language model dropped, deterministic `inv_freq` buffers — is not.
    """
    if key in expected:
        return True
    for suffix in (".scales", ".biases"):
        if key.endswith(suffix) and key[: -len(suffix)] + ".weight" in expected:
            return True
    return False


def write_json_atomic(path: Path, payload: Any) -> None:
    """Write a json file by rename, never in place.

    Staged directories hard-link the Hugging Face cache's blobs, so a file here can share an
    inode with the cached download. `write_text` would truncate and rewrite that shared inode
    and silently corrupt the cache for every future run; `os.replace` swaps the directory
    entry instead, leaving the blob alone.
    """
    temporary = path.with_name(f".{path.name}.write")
    temporary.write_text(json.dumps(payload, indent=2))
    os.replace(temporary, path)


_EVAL_BATCH_BYTES = 192 * 1024 * 1024


def _materialise(arrays: dict[str, Any]) -> None:
    """Force lazy arrays (transposes, dequantised layers) in bounded GPU batches.

    Saving a multi-gigabyte dict in one go builds a single enormous Metal command buffer and
    the driver kills it with a GPU timeout; a few hundred megabytes at a time does not.
    """
    import mlx.core as mx

    batch: list[Any] = []
    pending = 0
    for array in arrays.values():
        batch.append(array)
        pending += getattr(array, "nbytes", 0)
        if pending >= _EVAL_BATCH_BYTES:
            mx.eval(*batch)
            batch, pending = [], 0
    if batch:
        mx.eval(*batch)


_QUANT_SUFFIXES = (".weight", ".scales", ".biases")


def _split_by_size(arrays: dict[str, Any], target_bytes: int) -> list[dict[str, Any]]:
    """Split one component's tensors across shards of roughly `target_bytes` each.

    A quantised layer's `weight`, `scales` and `biases` always travel together: split across
    two files, the reader finds a packed weight with no scales (or scales with no biases) and
    cannot dequantise it, which is how a "repair" step turns into a corrupt checkpoint.
    """
    groups: dict[str, list[str]] = {}
    for key in arrays:
        base = key
        for suffix in _QUANT_SUFFIXES:
            if key.endswith(suffix):
                base = key[: -len(suffix)]
                break
        groups.setdefault(base, []).append(key)

    shards: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    pending = 0
    for keys in groups.values():
        for key in keys:
            current[key] = arrays[key]
            pending += getattr(arrays[key], "nbytes", 0)
        if pending >= target_bytes:
            shards.append(current)
            current, pending = {}, 0
    if current:
        shards.append(current)
    return shards


def _write_mflux_shards(
    component: Path,
    shards: dict[str, dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, str]:
    """Write `shards` as an mflux-format checkpoint and return the new weight map.

    Scratch names end in `.safetensors` — MLX appends that suffix when it is missing — and are
    renamed into place. Every shard carries the metadata, so whichever one sorts first is a
    truthful answer to "what quantisation is this".
    """
    import mlx.core as mx

    weight_map: dict[str, str] = {}
    for name in sorted(shards):
        arrays = shards[name]
        if not arrays:
            continue
        temporary = component / f"{name}.write.safetensors"
        _materialise(arrays)
        mx.save_safetensors(str(temporary), arrays, metadata=dict(metadata))
        temporary.replace(component / name)
        for key in arrays:
            weight_map[key] = name
        del arrays
        mx.clear_cache()
    for stale in component.glob("*.safetensors"):
        if stale.name not in weight_map.values():
            stale.unlink()
    return weight_map


def assemble_reference_text_encoder(
    directory: str | Path,
    base_weights: dict[str, Any],
    progress=None,
) -> dict[str, Any]:
    """Make the staged text encoder a complete reference one.

    The community pack was converted with mflux's text-only graph: its encoder holds the
    Qwen3-VL language model alone, under bare names, and none of the visual tower editing
    needs to read a reference image. This puts the language model where the reference module
    expects it (`language_model.*`, still 4-bit) and grafts the visual weights in from the
    base checkpoint — the only dense part, ~1.2 GB, because the base never quantised them.
    """
    import mlx.core as mx

    _, text_weight = text_encoder_transforms()
    component = Path(directory).expanduser()
    index_path = component / "model.safetensors.index.json"
    if not index_path.is_file():
        raise ValueError(f"text_encoder has no weight index: {component}")

    index = json.loads(index_path.read_text())
    metadata = dict(index.get("metadata") or {})
    if metadata.get("refract_reference_encoder") == "true":
        return {"assembled": False, "reason": "already the reference encoder", "visual": 0}

    expected = _expected_text_encoder_keys(component)

    packed: dict[str, Any] = {}
    dropped: list[str] = []
    # Read every shard on disk rather than trusting the index: an index left over from the
    # pack names two files, while a previous partial run may have written more.
    on_disk = sorted(
        path for path in component.glob("*.safetensors") if not path.name.endswith(".write.safetensors")
    )
    for shard in on_disk:
        data, _meta = mx.load(str(shard), return_metadata=True)
        for raw, array in data.items():
            key = reference_text_encoder_key(raw, from_base=False)
            if key is None:
                dropped.append(raw)
            elif _accepts(expected, key):
                packed[key] = array
            else:
                dropped.append(raw)
        del data

    visual: dict[str, Any] = {}
    for raw, array in base_weights.items():
        key = reference_text_encoder_key(raw, from_base=True)
        if key is None or not key.startswith("visual.") or not _accepts(expected, key):
            continue
        visual[key] = text_weight(key, array)
    if not visual:
        raise ValueError(
            "the base checkpoint contributed no visual-encoder tensors; the pack cannot edit "
            "reference images without them"
        )
    # The base weights win: a directory assembled once already carries them, and keeping a
    # second copy under the language model's shards would double 1.2 GB for nothing.
    for key in visual:
        packed.pop(key, None)

    # `.inv_freq` buffers are deterministic and recomputed at load; mflux's own validator
    # skips them for the same reason, so the staged checkpoint does not have to carry them.
    missing = sorted(
        key
        for key in expected
        if key not in packed and key not in visual and not key.endswith(".inv_freq")
    )
    if missing:
        raise ValueError(f"text_encoder is missing {len(missing)} tensor(s): {missing[:8]}")

    if progress:
        progress(
            {
                "phase": "staging",
                "message": f"adding the visual encoder ({len(visual)} tensors) to the text encoder",
            }
        )
    metadata["refract_reference_encoder"] = "true"
    shards: dict[str, dict[str, Any]] = {}
    for position, part in enumerate(_split_by_size(packed, 1024 * 1024 * 1024)):
        shards[f"{position}.safetensors"] = part
    shards[f"{len(shards)}.safetensors"] = visual
    weight_map = _write_mflux_shards(component, shards, metadata)

    index["weight_map"] = weight_map
    index["metadata"] = metadata
    write_json_atomic(index_path, index)
    return {
        "assembled": True,
        "language_model": len(packed),
        "visual": len(visual),
        "dropped": len(dropped),
        "shards": sorted(set(weight_map.values())),
    }


# --------------------------------------------------------------------------- transformer
# The Uncensored 4-bit export names two subtrees the way an older mflux export did. mflux's
# own `_normalize_transformer_weights` already rewrites `modulation.1.`, but this checkpoint
# uses `modulation.0.` (the same tensor — the pack numbers that MLP from zero) and drops the
# `timestep_embedder` segment from the time embedding projections.
_TRANSFORMER_RENAMES = (
    ("modulation.0.", "modulation.layers.1."),
    ("time_text_embed.linear_1.", "time_text_embed.timestep_embedder.linear_1."),
    ("time_text_embed.linear_2.", "time_text_embed.timestep_embedder.linear_2."),
)


def transformer_key(raw: str) -> str:
    """Map an older mflux transformer export's names onto the current module tree."""
    for old, new in _TRANSFORMER_RENAMES:
        if raw.startswith(old):
            return new + raw[len(old) :]
    return raw


def assemble_transformer_from_file(
    directory: str | Path,
    source: str | Path,
    progress=None,
) -> dict[str, Any]:
    """Turn a single-file 4-bit transformer export into an mflux-loadable directory.

    The Uncensored pack ships one 4.0 GB safetensors file with no index and no metadata, in
    mflux's own tensor layout, so it is not a "repo" mflux could read on its own: this renames
    the two older subtrees, keeps exactly the tensors the live module tree has a home for, and
    writes an indexed shard set carrying the 4-bit level. The layers mflux keeps dense but the
    pack quantised are left for `repair_quantization`.
    """
    import mlx.core as mx

    root = Path(directory).expanduser()
    source_path = Path(source).expanduser()
    if not source_path.is_file():
        raise ValueError(f"transformer weights not found: {source_path}")
    root.mkdir(parents=True, exist_ok=True)

    metadata = {
        "quantization_level": "4",
        "mflux_version": _runtime_mflux_version(),
        "refract_source": source_path.name,
    }
    index_path = root / "model.safetensors.index.json"
    if index_path.is_file():
        try:
            existing = json.loads(index_path.read_text())
        except (OSError, ValueError):
            existing = None
        if existing and (existing.get("metadata") or {}).get("refract_source") == source_path.name:
            names = set(existing.get("weight_map", {}).values())
            if names and all((root / name).is_file() for name in names):
                # Relabelling 4 GB of tensors is not free; a rerun after a later step failed
                # should not pay for it again.
                return {
                    "assembled": False,
                    "reason": "already relabelled from this file",
                    "tensors": len(existing.get("weight_map", {})),
                    "shards": sorted(names),
                }

    expected = expected_module_keys("transformer", component_config(root))
    data = mx.load(str(source_path))
    tensors: dict[str, Any] = {}
    dropped: list[str] = []
    for raw, array in data.items():
        key = transformer_key(raw)
        if _accepts(expected, key):
            tensors[key] = array
        else:
            dropped.append(raw)
    del data

    missing = sorted(
        key for key in expected if key not in tensors and not key.endswith((".inv_freq", ".freqs"))
    )
    if missing:
        raise ValueError(f"transformer is missing {len(missing)} tensor(s): {missing[:8]}")

    if progress:
        progress(
            {
                "phase": "staging",
                "message": f"writing the 4-bit transformer ({len(tensors)} tensors)",
            }
        )
    quantised = sum(1 for key in tensors if key.endswith(".scales"))
    shards: dict[str, dict[str, Any]] = {}
    for position, part in enumerate(_split_by_size(tensors, 1024 * 1024 * 1024)):
        shards[f"{position}.safetensors"] = part
    weight_map = _write_mflux_shards(root, shards, metadata)
    write_json_atomic(
        root / "model.safetensors.index.json",
        {"metadata": metadata, "weight_map": weight_map},
    )
    return {
        "assembled": True,
        "tensors": len(tensors),
        "quantized_layers": quantised,
        "dropped": len(dropped),
        "shards": sorted(set(weight_map.values())),
    }


def _runtime_mflux_version() -> str:
    try:
        import mflux

        return getattr(mflux, "__version__", "unknown")
    except Exception:
        return "unknown"


# --------------------------------------------------------------------------- validation
def _infer_quantization(tensors: dict[str, dict[str, Any]], expected: dict[str, tuple[int, ...]]) -> dict[str, Any]:
    """Solve bits/group_size from packed shapes instead of trusting a filename.

    For a quantised linear, MLX stores `weight` as uint32 of width input_dims*bits/32 and
    `scales` of width input_dims/group_size, so both fall out of the shapes. Layers that
    disagree are reported rather than averaged away.
    """
    bits_votes: dict[int, int] = {}
    group_votes: dict[int, int] = {}
    quantized_layers = 0
    oddities: list[str] = []
    candidates = (2, 3, 4, 5, 6, 8)
    groups = (32, 64, 128)

    for name, info in tensors.items():
        if not name.endswith(".scales"):
            continue
        base = name[: -len(".scales")]
        weight = tensors.get(f"{base}.weight")
        dense = expected.get(f"{base}.weight")
        if weight is None or dense is None or not dense:
            continue
        packed_w = weight["shape"][-1]
        input_dims = dense[-1]
        scales_w = info["shape"][-1]
        if not packed_w or not input_dims or not scales_w:
            continue
        ratio = packed_w * 32 / input_dims
        bits = min(candidates, key=lambda b: abs(b - ratio))
        if abs(ratio - bits) > 0.01:
            oddities.append(f"{base}: packed width {packed_w} implies {ratio:.2f} bits")
            continue
        group_ratio = input_dims / scales_w
        group = min(groups, key=lambda g: abs(g - group_ratio))
        if abs(group_ratio - group) > 0.01:
            oddities.append(f"{base}: scales width {scales_w} implies group {group_ratio:.2f}")
            continue
        bits_votes[bits] = bits_votes.get(bits, 0) + 1
        group_votes[group] = group_votes.get(group, 0) + 1
        quantized_layers += 1

    def winner(votes: dict[int, int]) -> int | None:
        if not votes:
            return None
        top = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
        return top[0][0]

    bits = winner(bits_votes)
    return {
        "quantized": quantized_layers > 0,
        "bits": bits,
        "group_size": winner(group_votes),
        "layers": quantized_layers,
        "consistent": bool(bits) and len(bits_votes) == 1 and len(group_votes) <= 1,
        "oddities": oddities[:10],
    }


def analyze(path: str | Path, component: str = "transformer") -> dict[str, Any]:
    """Full compatibility report for a candidate checkpoint. Never writes anything."""
    source = Path(path).expanduser()
    report: dict[str, Any] = {
        "ok": False,
        "source": str(source),
        "component": component,
        "errors": [],
        "warnings": [],
    }
    try:
        scanned = scan(source)
    except (OSError, ValueError) as exc:
        report["errors"].append(str(exc))
        return report

    tensors = scanned["tensors"]
    report["tensor_count"] = len(tensors)
    report["total_bytes"] = scanned["total_bytes"]
    report["files"] = scanned["files"]
    report["stored_metadata"] = {k: v for k, v in scanned["metadata"].items() if k in ("quantization_level", "mflux_version")}
    if scanned["duplicate_tensors"]:
        report["warnings"].append(
            f"{len(scanned['duplicate_tensors'])} tensor name(s) appear in more than one shard"
        )

    config = component_config(source)
    report["module_tree"] = "reference (transformer/config.json)" if config else "text-to-image (no config on disk)"
    try:
        expected = expected_module_keys(component, config)
    except Exception as exc:  # pragma: no cover - only when mflux is unavailable
        report["errors"].append(f"cannot inspect the mflux module tree: {exc}")
        return report

    expected = {name: shape for name, shape in expected.items() if not _is_ignored(name)}
    report["expected_keys"] = len(expected)
    report["quantization"] = _infer_quantization(tensors, expected)
    if report["quantization"]["oddities"]:
        report["warnings"].extend(report["quantization"]["oddities"])

    missing: list[str] = []
    unexpected: list[str] = []
    shape_mismatches: list[str] = []
    matched = 0
    prefixes: set[str] = set()

    for raw_name, info in tensors.items():
        name, stripped = normalize_key(raw_name)
        if stripped:
            prefixes.add(stripped)
        if _is_ignored(name):
            continue
        dtype = info["dtype"]
        shape = info["shape"]

        if name.endswith(QUANT_SUFFIXES):
            base = name.rsplit(".", 1)[0] + ".weight"
            if base not in expected:
                unexpected.append(raw_name)
                continue
            matched += 1
            continue

        if name not in expected:
            unexpected.append(raw_name)
            continue

        want = expected[name]
        if shape == want:
            matched += 1
            continue
        # A packed 4/6/8-bit weight is legitimately narrower than its dense counterpart.
        if dtype in ("U32", "U8", "I32") and len(shape) == len(want):
            ratio = shape[-1] * 32 / want[-1] if want[-1] else 0
            if abs(ratio - round(ratio)) < 0.01 and 2 <= round(ratio) <= 8:
                matched += 1
                continue
        shape_mismatches.append(f"{raw_name}: expected {tuple(want)}, found {tuple(shape)} ({dtype})")

    for name in expected:
        if name not in {normalize_key(raw)[0] for raw in tensors}:
            if name.endswith(".scales") or name.endswith(".biases"):
                continue
            # Deterministic buffers mflux recomputes at load; its own validator skips them
            # when reporting missing weights, so this must too.
            if name.endswith((".inv_freq", ".freqs")):
                continue
            missing.append(name)

    report["matched_keys"] = matched
    report["stripped_prefixes"] = sorted(prefixes)
    report["missing"] = {"count": len(missing), "examples": missing[:8]}
    report["unexpected"] = {"count": len(unexpected), "examples": unexpected[:8]}
    report["shape_mismatches"] = {"count": len(shape_mismatches), "examples": shape_mismatches[:8]}
    report["coverage"] = round(matched / len(expected), 4) if expected else 0.0
    report["ok"] = not missing and not unexpected and not shape_mismatches

    if not report["ok"]:
        report["errors"].append(
            f"{len(missing)} missing, {len(unexpected)} unexpected and "
            f"{len(shape_mismatches)} shape-mismatched tensors against the mflux "
            f"Qwen-Image-2.1 transformer"
        )
    if report["ok"] and not report["quantization"]["quantized"]:
        report["warnings"].append(
            "every tensor is unquantised, so this pack will be quantised at load time "
            "instead of being used as-is"
        )
    return report


# --------------------------------------------------------------------------- writing
def _rewrite_header(source: Path, target: Path, metadata: dict[str, str]) -> int:
    """Copy a safetensors file, adding `__metadata__` without decoding any tensor.

    Returned is the tensor count. Data offsets are relative to the payload, which is
    copied verbatim, so nothing needs re-encoding even though the header length changes.
    """
    with source.open("rb") as handle:
        header_len = struct.unpack("<Q", handle.read(8))[0]
        header = json.loads(handle.read(header_len).decode("utf-8"))
        payload_start = 8 + header_len

    header["__metadata__"] = {**(header.get("__metadata__") or {}), **metadata}
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    encoded += b" " * ((-len(encoded)) % 8)

    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, target.open("wb") as dst:
        dst.write(struct.pack("<Q", len(encoded)))
        dst.write(encoded)
        src.seek(payload_start)
        shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)
    return sum(1 for key, value in header.items() if isinstance(value, dict) and "shape" in value)


def install(
    source: str | Path,
    base_model_path: str | Path,
    target_dir: str | Path,
    quantize: int | None = 4,
    progress=None,
) -> dict[str, Any]:
    """Build a complete model directory from a base model plus a replacement transformer.

    The base contributes the text encoder, VAE, processor and configs; only the
    transformer subdirectory is replaced, and it is cleared first — a leftover q8 tail
    beside a q4 save is exactly the mixed-precision mess mflux warns about.
    """
    from .paths import dir_size_bytes

    source_path = Path(source).expanduser()
    base_path = Path(base_model_path).expanduser()
    target = Path(target_dir).expanduser()

    if not base_path.is_dir():
        raise ValueError(f"base model directory not found: {base_path}")

    report = analyze(source_path, component="transformer")
    if not report.get("ok"):
        return {"installed": False, "validation": report, "reason": "validation failed"}

    bits = report["quantization"]["bits"] or quantize
    if bits is None:
        return {
            "installed": False,
            "validation": report,
            "reason": "could not infer a quantisation level; the pack looks unquantised",
        }

    def note(message: str) -> None:
        if progress:
            progress({"phase": "installing", "message": message})

    note(f"copying base model components from {base_path.name}")
    target.mkdir(parents=True, exist_ok=True)
    for entry in base_path.iterdir():
        if entry.name in ("transformer", ".cache"):
            continue
        destination = target / entry.name
        if entry.is_dir():
            shutil.copytree(entry, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, destination)

    transformer_dir = target / "transformer"
    if transformer_dir.exists():
        shutil.rmtree(transformer_dir)
    transformer_dir.mkdir(parents=True, exist_ok=True)

    files = _safetensors_files(source_path)
    weight_map: dict[str, str] = {}
    metadata = {
        "quantization_level": str(bits),
        "mflux_version": "refract-ingest",
        "refract_source": source_path.name,
    }
    for index, file in enumerate(files):
        shard_name = f"{index}.safetensors"
        note(f"writing {shard_name} from {file.name}")
        _rewrite_header(file, transformer_dir / shard_name, metadata)
        _meta, tensors = read_header(file)
        for name in tensors:
            weight_map[name] = shard_name

    index_payload = {
        "metadata": {"quantization_level": str(bits), "mflux_version": "refract-ingest"},
        "weight_map": weight_map,
    }
    (transformer_dir / "model.safetensors.index.json").write_text(json.dumps(index_payload, indent=2))

    note("verifying the pack loads through mflux")
    verified = verify_loads(target)
    return {
        "installed": bool(verified["ok"]),
        "validation": report,
        "verification": verified,
        "target": str(target),
        "bits": bits,
        "size_bytes": dir_size_bytes(target),
        "reason": None if verified["ok"] else verified.get("error"),
    }


# --------------------------------------------------------------------------- repair
_QUANTIZED_SUFFIXES = (".weight", ".scales", ".biases")


def _bits_and_group(packed_shape: tuple[int, ...], scales_shape: tuple[int, ...], module) -> tuple[int, int] | None:
    """Solve bit width and group size from the packed shapes, not from a filename."""
    dense = getattr(getattr(module, "weight", None), "shape", None)
    if not dense or len(packed_shape) != 2 or len(scales_shape) != 2:
        return None
    input_dims = int(dense[-1])
    if not input_dims or not scales_shape[-1] or not packed_shape[-1]:
        return None
    bits = packed_shape[-1] * 32 / input_dims
    group = input_dims / scales_shape[-1]
    if abs(bits - round(bits)) > 1e-6 or abs(group - round(group)) > 1e-6:
        return None
    bits, group = int(round(bits)), int(round(group))
    if bits not in (2, 3, 4, 5, 6, 8) or group not in (32, 64, 128):
        return None
    return bits, group


def dense_only_layers(model_dir: str | Path, component: str = "transformer") -> list[dict[str, Any]]:
    """Checkpoint layers that are stored quantised but that mflux keeps dense at load.

    mflux's own predicate refuses to quantise the timestep-conditioning path — anything under
    `modulation`, `time_text_embed` or `norm_out` — and the 4-bit community pack quantised it
    anyway. Loading such a checkpoint fails mflux's post-load validation with the scales and
    biases as "unexpected" tensors, because the module it built is dense. Rather than patch
    mflux's judgement out, the packed tensors for exactly those layers are expanded back to
    bf16; every layer mflux *would* quantise is left untouched.

    The list comes from the live predicate, so it can never drift from the mflux version in
    the runtime.
    """
    from mlx import nn
    from mlx.utils import tree_flatten

    from mflux.models.qwen21.reference.weights.qwen_image21_weight_definition import (
        QwenImage21WeightDefinition,
    )

    root = Path(model_dir).expanduser()
    directory = root / component
    if not directory.is_dir():
        return []

    config = component_config(directory)
    module = component_module(component, config)
    predicate = QwenImage21WeightDefinition.quantization_predicate
    leaves = dict(tree_flatten(module.leaf_modules(), is_leaf=lambda item: isinstance(item, nn.Module)))

    tensors = scan(directory)["tensors"]
    candidates: list[dict[str, Any]] = []
    for name, info in tensors.items():
        if not name.endswith(".scales"):
            continue
        path = name[: -len(".scales")]
        target = leaves.get(path)
        if target is not None and predicate(path, target):
            continue
        packed = tensors.get(f"{path}.weight")
        if packed is None or target is None:
            continue
        geometry = _bits_and_group(packed["shape"], info["shape"], target)
        if geometry is None:
            continue
        candidates.append(
            {
                "path": path,
                "shard": packed.get("file"),
                "bits": geometry[0],
                "group_size": geometry[1],
                "dense_shape": tuple(int(dim) for dim in target.weight.shape),
            }
        )
    return candidates


def repair_quantization(
    model_dir: str | Path,
    component: str = "transformer",
    progress=None,
) -> dict[str, Any]:
    """Expand the layers mflux keeps dense, in place, keeping everything else 4-bit.

    Shards are rewritten whole (mflux loads every shard its index names, so stale tensors
    inside an untouched file would still be read), which is why the work is reported: on an
    external drive it is a few gigabytes of I/O, once, during Prepare.
    """
    import mlx.core as mx

    root = Path(model_dir).expanduser()
    directory = root / component
    index_path = directory / "model.safetensors.index.json"
    if not index_path.is_file():
        return {"repaired": 0, "layers": [], "reason": "no index; nothing to repair"}

    candidates = dense_only_layers(root, component)
    if not candidates:
        return {"repaired": 0, "layers": [], "reason": "already dense where mflux needs it"}

    by_shard: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        shard = candidate["shard"] or "0.safetensors"
        by_shard.setdefault(shard, []).append(candidate)

    index = json.loads(index_path.read_text())
    weight_map = index.get("weight_map") or {}
    expanded: list[str] = []

    for shard, layers in sorted(by_shard.items()):
        if progress:
            progress(
                {
                    "phase": "repairing",
                    "message": f"expanding {len(layers)} layer(s) mflux keeps dense in {shard}",
                }
            )
        path = directory / shard
        data, metadata = mx.load(str(path), return_metadata=True)
        arrays = dict(data.items())

        for layer in layers:
            name = layer["path"]
            packed = arrays.pop(f"{name}.weight", None)
            scales = arrays.pop(f"{name}.scales", None)
            biases = arrays.pop(f"{name}.biases", None)
            if packed is None or scales is None:
                continue
            dense = mx.dequantize(
                packed,
                scales=scales,
                biases=biases,
                group_size=layer["group_size"],
                bits=layer["bits"],
            )
            dense = dense.astype(mx.bfloat16)
            if tuple(dense.shape) != tuple(layer["dense_shape"]):
                raise ValueError(
                    f"{name}: dequantised to {tuple(dense.shape)}, but mflux expects "
                    f"{tuple(layer['dense_shape'])}"
                )
            arrays[f"{name}.weight"] = dense
            weight_map[f"{name}.weight"] = shard
            weight_map.pop(f"{name}.scales", None)
            weight_map.pop(f"{name}.biases", None)
            expanded.append(name)

        # MLX appends ".safetensors" unless the path already carries that suffix, so the
        # scratch file must end in it too (with_suffix(".safetensors.tmp") produced a
        # second extension and broke the rename).
        temporary = path.with_name(f"{path.stem}.repair.safetensors")
        mx.save_safetensors(str(temporary), arrays, metadata=dict(metadata or {}))
        del arrays, data
        mx.clear_cache()
        temporary.replace(path)

    index["weight_map"] = weight_map
    index.setdefault("metadata", {})
    index["metadata"]["refract_repaired"] = "dense-layers " + str(len(expanded))
    write_json_atomic(index_path, index)
    return {
        "repaired": len(expanded),
        "layers": expanded,
        "shards": sorted(by_shard),
        "reason": None,
    }


def verify_loads(model_dir: str | Path, component: str = "transformer") -> dict[str, Any]:
    """Load the transformer component from `model_dir` with mflux's own loader.

    This is the honest test: it uses the same `WeightLoader` + `WeightApplier` and the same
    post-load validation the reference-editing pipeline uses, against the module tree built
    from this checkpoint's own `transformer/config.json`. A pass here means the pack will
    actually run — including the "unexpected scales/biases" class of failure, which only
    shows up after the quantisation decision is made.
    """
    from mlx.utils import tree_flatten, tree_unflatten

    from mflux.models.common.weights.loading.weight_applier import WeightApplier
    from mflux.models.common.weights.loading.weight_loader import WeightLoader
    from mflux.models.qwen21.qwen21_initializer import Qwen21Initializer

    root = Path(model_dir).expanduser()
    config = component_config(root / component)
    reference = config is not None
    try:
        if reference:
            from mflux.models.qwen21.reference.weights.qwen_image21_weight_definition import (
                QwenImage21WeightDefinition as definition,
            )
        else:
            from mflux.models.qwen21.weights.qwen21_weight_definition import (
                Qwen21WeightDefinition as definition,
            )

        target = next(c for c in definition.get_components() if c.name == component)
        loaded = WeightLoader.load_single_local(target, root)
        supplied = dict(tree_flatten(loaded.components[component]))
        if component == "transformer":
            # Private on purpose: this is mflux's own compatibility shim for pre-consolidation
            # exports, and replicating it here would drift from the loader it must match.
            supplied = Qwen21Initializer._normalize_transformer_weights(supplied)
            loaded.components[component] = tree_unflatten(list(supplied.items()))
        module = component_module(component, config)
        bits = WeightApplier.apply_and_quantize_single(
            weights=loaded, model=module, component=target, quantize_arg=None
        )
        Qwen21Initializer._validate_weights(component, module, supplied)
        parameters = tree_flatten(module.parameters())
        return {
            "ok": True,
            "component": component,
            "bits": bits,
            "tensors": len(supplied),
            "parameters": int(sum(p.size for _name, p in parameters)),
            "stored_quantization_level": loaded.meta_data.quantization_level,
            "module_tree": "reference" if reference else "text-to-image",
        }
    except Exception as exc:
        return {
            "ok": False,
            "component": component,
            "error": f"{type(exc).__name__}: {exc}",
            "module_tree": "reference" if reference else "text-to-image",
        }


def iter_examples(items: Iterable[str], limit: int = 5) -> list[str]:
    return list(items)[:limit]
