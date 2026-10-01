"""Tests for staging a third-party 4-bit checkpoint into an mflux-loadable directory.

These cover the parts that decide what a staged checkpoint looks like on disk: relabelling a
legacy export, keeping a quantised layer's tensors in one shard, and never writing through a
hard link into the download cache.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import mlx.core as mx
import pytest

from refract_backend import ingest


SMALL_TRANSFORMER = {
    "modulation.layers.1.weight": (64, 64),
    "time_text_embed.timestep_embedder.linear_1.weight": (64, 64),
    "img_in.weight": (64, 64),
}


@pytest.fixture()
def small_transformer(monkeypatch):
    monkeypatch.setattr(
        ingest,
        "expected_module_keys",
        lambda component="transformer", config=None: dict(SMALL_TRANSFORMER),
    )
    return SMALL_TRANSFORMER


def test_transformer_key_renames_legacy_subtrees():
    assert ingest.transformer_key("modulation.0.weight") == "modulation.layers.1.weight"
    assert (
        ingest.transformer_key("time_text_embed.linear_1.scales")
        == "time_text_embed.timestep_embedder.linear_1.scales"
    )
    assert (
        ingest.transformer_key("time_text_embed.linear_2.biases")
        == "time_text_embed.timestep_embedder.linear_2.biases"
    )
    # Everything already in the current layout passes through untouched.
    assert ingest.transformer_key("img_in.weight") == "img_in.weight"
    assert ingest.transformer_key("txt_in.text_norm.weight") == "txt_in.text_norm.weight"


def test_split_by_size_keeps_each_quantised_layer_together():
    """A layer's weight/scales/biases must never land in different shards.

    Split across files, a reader finds a packed weight without its scales and cannot
    dequantise it — which is exactly how the dense-layer repair corrupted a checkpoint.
    """
    arrays = {}
    for index in range(40):
        base = f"transformer_blocks.{index}.attn.to_q"
        arrays[f"{base}.weight"] = mx.zeros((512, 64), dtype=mx.uint32)
        arrays[f"{base}.scales"] = mx.zeros((512, 8), dtype=mx.bfloat16)
        arrays[f"{base}.biases"] = mx.zeros((512, 8), dtype=mx.bfloat16)

    shards = ingest._split_by_size(arrays, 1)  # force one shard per group

    assert len(shards) == 40
    seen = set()
    for shard in shards:
        bases = {key.rsplit(".", 1)[0] for key in shard}
        assert len(bases) == 1, shard.keys()
        for base in bases:
            assert f"{base}.weight" in shard
            assert f"{base}.scales" in shard
            assert f"{base}.biases" in shard
            seen.add(base)
    assert len(seen) == 40


def test_write_json_atomic_does_not_write_through_a_hard_link(tmp_path):
    """Staged files are hard links into the Hugging Face cache's blobs.

    Rewriting one in place would truncate the shared inode and corrupt the cached download
    for every later run; the atomic rename has to swap the directory entry instead.
    """
    blob = tmp_path / "blob"
    blob.write_text("original")
    staged = tmp_path / "staged.json"
    os.link(blob, staged)

    ingest.write_json_atomic(staged, {"metadata": {"quantization_level": "4"}})

    assert blob.read_text() == "original"
    assert json.loads(staged.read_text())["metadata"]["quantization_level"] == "4"
    # The rename breaks the link, so the two are independent files again.
    assert blob.stat().st_ino != staged.stat().st_ino


def test_assemble_transformer_from_file_relabels_filters_and_indexes(tmp_path, small_transformer):
    source = tmp_path / "qwen-image-2.1-UC-MLX-4bit.safetensors"
    mx.save_safetensors(
        str(source),
        {
            # 4-bit affine: 64 input dims -> 64*4/32 = 8 packed uint32 columns, 64/64 = 1 scale.
            "modulation.0.weight": mx.zeros((64, 8), dtype=mx.uint32),
            "modulation.0.scales": mx.zeros((64, 1), dtype=mx.bfloat16),
            "modulation.0.biases": mx.zeros((64, 1), dtype=mx.bfloat16),
            "time_text_embed.linear_1.weight": mx.zeros((64, 64), dtype=mx.bfloat16),
            "time_text_embed.linear_2.weight": mx.zeros((64, 64), dtype=mx.bfloat16),
            "img_in.weight": mx.zeros((64, 64), dtype=mx.bfloat16),
            "junk.weight": mx.zeros((4, 4), dtype=mx.bfloat16),
        },
    )

    target = tmp_path / "transformer"
    result = ingest.assemble_transformer_from_file(target, source)

    assert result["assembled"] is True
    index = json.loads((target / "model.safetensors.index.json").read_text())
    assert index["metadata"]["quantization_level"] == "4"
    assert index["metadata"]["refract_source"] == source.name
    keys = set(index["weight_map"])

    # Relabelled into the current module tree, quantisation triplets intact.
    assert {"modulation.layers.1.weight", "modulation.layers.1.scales", "modulation.layers.1.biases"} <= keys
    assert "time_text_embed.timestep_embedder.linear_1.weight" in keys
    # Tensors the live module tree has no home for are dropped, not invented.
    assert "junk.weight" not in keys
    assert "time_text_embed.linear_2.weight" not in keys
    assert result["dropped"] == 2

    # Every shard the index names is on disk, so mflux will not trip over a missing file.
    assert all((target / name).is_file() for name in set(index["weight_map"].values()))


def test_assemble_transformer_from_file_is_idempotent(tmp_path, small_transformer):
    source = tmp_path / "pack.safetensors"
    mx.save_safetensors(
        str(source),
        {
            "img_in.weight": mx.zeros((64, 64), dtype=mx.bfloat16),
            "modulation.0.weight": mx.zeros((64, 64), dtype=mx.bfloat16),
            "time_text_embed.linear_1.weight": mx.zeros((64, 64), dtype=mx.bfloat16),
        },
    )

    target = tmp_path / "transformer"
    first = ingest.assemble_transformer_from_file(target, source)
    second = ingest.assemble_transformer_from_file(target, source)

    assert first["assembled"] is True
    # Re-running after a later failure must not pay for relabelling 4 GB again.
    assert second["assembled"] is False
    assert second["reason"] == "already relabelled from this file"


def test_assemble_refuses_a_transformer_missing_expected_tensors(tmp_path, small_transformer):
    source = tmp_path / "pack.safetensors"
    mx.save_safetensors(str(source), {"junk.weight": mx.zeros((4, 4), dtype=mx.bfloat16)})

    with pytest.raises(ValueError, match="missing"):
        ingest.assemble_transformer_from_file(tmp_path / "transformer", source)
