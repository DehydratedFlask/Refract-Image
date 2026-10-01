"""Tests for the custom-weights path.

These exercise the parts that decide whether a third-party pack is accepted, so they use a
monkeypatched expected-key map (the real one is a 7.1B-parameter tree) while keeping the
comparison logic itself untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

import mlx.core as mx
import pytest

from refract_backend import ingest


SMALL_EXPECTED = {
    "img_in_linear.weight": (128, 256),
    "tiny.weight": (64, 64),
}


@pytest.fixture()
def small_tree(monkeypatch):
    monkeypatch.setattr(
        ingest,
        "expected_module_keys",
        lambda component="transformer", config=None: dict(SMALL_EXPECTED),
    )
    return SMALL_EXPECTED


def _write(path: Path, tensors: dict, metadata: dict | None = None) -> Path:
    mx.save_safetensors(str(path), tensors, metadata)
    return path


def test_normalize_key_strips_wrappers_and_renames_modulation():
    assert ingest.normalize_key("transformer.img_in.weight") == ("img_in.weight", "transformer.")
    assert ingest.normalize_key("model.diffusion_model.x.weight") == ("x.weight", "model.diffusion_model.")
    assert ingest.normalize_key("modulation.1.weight") == ("modulation.layers.1.weight", None)
    assert ingest.normalize_key("nested.key") == ("nested.key", None)


def test_analyze_reports_missing_unexpected_and_shapes(tmp_path, small_tree):
    path = _write(
        tmp_path / "pack.safetensors",
        {
            "img_in_linear.weight": mx.zeros((128, 256), dtype=mx.bfloat16),
            "surprise.weight": mx.zeros((4, 4), dtype=mx.bfloat16),
        },
    )
    report = ingest.analyze(path)
    assert report["ok"] is False
    assert report["missing"]["count"] == 1  # tiny.weight
    assert report["unexpected"]["count"] == 1
    assert "surprise.weight" in report["unexpected"]["examples"]
    assert report["errors"]


def test_analyze_rejects_shape_mismatch(tmp_path, small_tree):
    path = _write(tmp_path / "pack.safetensors", {"tiny.weight": mx.zeros((64, 32), dtype=mx.bfloat16)})
    report = ingest.analyze(path)
    assert report["shape_mismatches"]["count"] == 1
    assert "expected (64, 64)" in report["shape_mismatches"]["examples"][0]


def test_analyze_accepts_a_matching_4bit_pack(tmp_path, small_tree):
    path = _write(
        tmp_path / "pack.safetensors",
        {
            # 4-bit packing: 256 input dims -> 256*4/32 = 32 packed uint32 columns,
            # group size 64 -> 256/64 = 4 scales.
            "img_in_linear.weight": mx.zeros((128, 32), dtype=mx.uint32),
            "img_in_linear.scales": mx.zeros((128, 4), dtype=mx.bfloat16),
            "img_in_linear.biases": mx.zeros((128, 4), dtype=mx.bfloat16),
            "tiny.weight": mx.zeros((64, 64), dtype=mx.bfloat16),
        },
        {"quantization_level": "4"},
    )
    report = ingest.analyze(path)
    assert report["ok"] is True, report
    assert report["quantization"]["bits"] == 4
    assert report["quantization"]["group_size"] == 64
    assert report["quantization"]["layers"] == 1
    assert report["quantization"]["consistent"] is True
    assert report["stored_metadata"]["quantization_level"] == "4"


def test_analyze_reports_unquantized_pack_as_a_warning(tmp_path, small_tree):
    path = _write(
        tmp_path / "pack.safetensors",
        {
            "img_in_linear.weight": mx.zeros((128, 256), dtype=mx.bfloat16),
            "tiny.weight": mx.zeros((64, 64), dtype=mx.bfloat16),
        },
    )
    report = ingest.analyze(path)
    assert report["ok"] is True
    assert report["quantization"]["quantized"] is False
    assert any("unquantised" in warning for warning in report["warnings"])


def test_analyze_tolerates_missing_file(tmp_path):
    report = ingest.analyze(tmp_path / "nope.safetensors")
    assert report["ok"] is False
    assert report["errors"]


def test_scan_merges_shards_and_flags_duplicates(tmp_path):
    _write(tmp_path / "0.safetensors", {"a.weight": mx.zeros((2, 2), dtype=mx.bfloat16)})
    _write(tmp_path / "1.safetensors", {"b.weight": mx.zeros((2, 2), dtype=mx.bfloat16)})
    scanned = ingest.scan(tmp_path)
    assert set(scanned["tensors"]) == {"a.weight", "b.weight"}
    assert scanned["duplicate_tensors"] == []


def test_rewrite_header_adds_metadata_and_preserves_payload(tmp_path):
    source = _write(
        tmp_path / "source.safetensors",
        {"a.weight": mx.arange(16, dtype=mx.float32).reshape(4, 4)},
    )
    target = tmp_path / "target.safetensors"
    count = ingest._rewrite_header(source, target, {"quantization_level": "4", "mflux_version": "refract-ingest"})
    assert count == 1

    metadata, tensors = ingest.read_header(target)
    assert metadata["quantization_level"] == "4"
    assert tensors["a.weight"]["shape"] == (4, 4)

    # The header may change size, but the tensor payload is copied verbatim.
    header_len = int.from_bytes(source.read_bytes()[:8], "little")
    target_len = int.from_bytes(target.read_bytes()[:8], "little")
    assert source.read_bytes()[8 + header_len :] == target.read_bytes()[8 + target_len :]


def test_install_refuses_an_incompatible_pack(tmp_path, small_tree):
    base = tmp_path / "base"
    base.mkdir()
    bad = _write(tmp_path / "bad.safetensors", {"wrong.weight": mx.zeros((2, 2), dtype=mx.bfloat16)})
    result = ingest.install(source=bad, base_model_path=base, target_dir=tmp_path / "out", quantize=4)
    assert result["installed"] is False
    assert result["reason"] == "validation failed"
    assert not (tmp_path / "out" / "transformer").exists()


def test_install_writes_index_and_metadata(tmp_path, small_tree):
    """End-to-end install with the load verification stubbed out (no real model here)."""
    base = tmp_path / "base"
    (base / "text_encoder").mkdir(parents=True)
    (base / "text_encoder" / "model.safetensors").write_bytes(b"stub")
    (base / "processor").mkdir()
    (base / "processor" / "tokenizer.json").write_text("{}")

    pack = _write(
        tmp_path / "pack.safetensors",
        {
            "img_in_linear.weight": mx.zeros((128, 32), dtype=mx.uint32),
            "img_in_linear.scales": mx.zeros((128, 4), dtype=mx.bfloat16),
            "img_in_linear.biases": mx.zeros((128, 4), dtype=mx.bfloat16),
            "tiny.weight": mx.zeros((64, 64), dtype=mx.bfloat16),
        },
    )

    calls: list[Path] = []

    def fake_verify(model_dir):
        calls.append(Path(model_dir))
        return {"ok": True, "bits": 4, "tensors": 4, "parameters": 1234}

    import refract_backend.ingest as module

    original = module.verify_loads
    module.verify_loads = fake_verify  # type: ignore[assignment]
    try:
        result = ingest.install(
            source=pack, base_model_path=base, target_dir=tmp_path / "out", quantize=4
        )
    finally:
        module.verify_loads = original

    assert result["installed"] is True
    assert result["bits"] == 4
    assert calls == [tmp_path / "out"]

    index = json.loads((tmp_path / "out" / "transformer" / "model.safetensors.index.json").read_text())
    assert index["metadata"]["quantization_level"] == "4"
    assert index["weight_map"]["tiny.weight"] == "0.safetensors"
    # Base components came along; the transformer subdirectory is the pack's.
    assert (tmp_path / "out" / "processor" / "tokenizer.json").exists()
    metadata, _tensors = ingest.read_header(tmp_path / "out" / "transformer" / "0.safetensors")
    assert metadata["quantization_level"] == "4"


def test_verify_loads_reports_errors_instead_of_raising(tmp_path):
    (tmp_path / "transformer").mkdir()
    (tmp_path / "transformer" / "0.safetensors").write_bytes(b"not a safetensors file")
    result = ingest.verify_loads(tmp_path)
    assert result["ok"] is False
    assert "error" in result
