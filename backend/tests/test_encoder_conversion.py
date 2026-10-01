import json

import mlx.core as mx
import pytest

from refract_backend import ingest
from refract_backend.tools.prepare_encoder import convert_encoder


def test_encoder_conversion_quantizes_language_and_preserves_vision(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    config = source / "config.json"
    config.write_text("{}")
    expected = {
        "language_model.layers.0.self_attn.o_proj.weight": (64, 64),
        "language_model.layers.1.mlp.down_proj.weight": (64, 64),
        "visual.test.weight": (64, 64),
    }
    monkeypatch.setattr(ingest, "expected_module_keys", lambda *args: expected)
    shards = {
        "one.safetensors": {"model.language_model.layers.0.self_attn.o_proj.weight": mx.ones((64, 64))},
        "two.safetensors": {
            "model.language_model.layers.1.mlp.down_proj.weight": mx.ones((64, 64)),
            "model.visual.test.weight": mx.ones((64, 64)),
            "lm_head.weight": mx.zeros((64, 64)),
        },
    }
    weight_map = {}
    for name, arrays in shards.items():
        mx.save_safetensors(str(source / name), arrays)
        weight_map.update({key: name for key in arrays})
    (source / "model.safetensors.index.json").write_text(json.dumps({"weight_map": weight_map}))
    target = tmp_path / "converted"
    result = convert_encoder(source, target, config, "pinned-revision")
    index = json.loads((target / "model.safetensors.index.json").read_text())
    assert result["quantized_layers"] == 2
    assert index["metadata"]["refract_encoder_revision"] == "pinned-revision"
    assert index["metadata"]["refract_encoder_abliterated"] == "true"
    assert all((target / name).is_file() for name in index["weight_map"].values())
    arrays = {}
    for name in set(index["weight_map"].values()):
        arrays.update(mx.load(str(target / name)))
    assert arrays["visual.test.weight"].shape == (64, 64)
    assert "visual.test.scales" not in arrays
    assert arrays["language_model.layers.0.self_attn.o_proj.weight"].shape == (64, 8)
    assert "lm_head.weight" not in arrays
