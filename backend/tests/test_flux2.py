from __future__ import annotations

import json

import pytest

from refract_backend import encoders
from refract_backend.model_store import SOURCES
from refract_backend.runner import ModelNotReady, resolve_model
from refract_backend.schemas import GenerateRequest
from refract_backend.tools.prepare_klein import _gguf_to_mflux_keys


def test_klein_source_is_declared_as_its_own_family():
    spec = SOURCES["flux2-klein-4b"]
    assert spec.family == "flux2"
    assert spec.kind == "local"
    # FLUX.2 Klein is distilled for few steps; a 40-step default would be 10x the work.
    assert spec.default_steps == 4
    assert spec.supports_negative_prompt is False


def test_qwen_sources_stay_in_the_qwen_family():
    for source_id in ("mlx-q4", "upstream-q4", "prepared", "custom"):
        assert SOURCES[source_id].family == "qwen21", source_id
        assert SOURCES[source_id].default_steps == 40, source_id


def test_klein_resolves_to_a_staged_directory_or_refuses(isolated_dirs):
    with pytest.raises(ModelNotReady) as excinfo:
        resolve_model("flux2-klein-4b")
    assert "prepare_klein" in str(excinfo.value)

    from refract_backend.paths import models_dir

    staged = models_dir() / "flux2-klein-4b"
    staged.mkdir(parents=True)
    (staged / "refract-manifest.json").write_text(json.dumps({"encoder": "ablated"}))
    resolved = resolve_model("flux2-klein-4b")
    assert resolved.family == "flux2"
    assert resolved.path == str(staged)
    assert resolved.default_steps == 4
    assert resolved.supports_negative_prompt is False


def test_gguf_key_mapping_covers_the_qwen3_layout():
    assert _gguf_to_mflux_keys("token_embd.weight") == "model.embed_tokens.weight"
    assert _gguf_to_mflux_keys("output_norm.weight") == "model.norm.weight"
    assert _gguf_to_mflux_keys("blk.0.attn_q.weight") == "model.layers.0.self_attn.q_proj.weight"
    assert _gguf_to_mflux_keys("blk.7.attn_output.weight") == "model.layers.7.self_attn.o_proj.weight"
    assert _gguf_to_mflux_keys("blk.35.ffn_down.weight") == "model.layers.35.mlp.down_proj.weight"
    assert _gguf_to_mflux_keys("blk.0.attn_q_norm.weight") == "model.layers.0.self_attn.q_norm.weight"
    # Tied embeddings and rotary caches have no place in mflux's encoder.
    assert _gguf_to_mflux_keys("lm_head.weight") is None
    assert _gguf_to_mflux_keys("rope_freqs_real") is None
    # An unknown leaf must be refused, not guessed into the wrong module.
    assert _gguf_to_mflux_keys("blk.0.something_new.weight") is None


def test_encoders_are_scoped_to_their_family():
    for entry in encoders.ENCODERS:
        assert entry.family in ("qwen21", "flux2")
    qwen = [e.key for e in encoders.ENCODERS if e.family == "qwen21"]
    flux = [e.key for e in encoders.ENCODERS if e.family == "flux2"]
    assert qwen == ["qwen21-stock", "qwen21-heretic"]
    assert flux == ["flux2-stock", "flux2-ablated"]
    # A cross-family offer is the exact mistake this module exists to prevent.
    assert not set(qwen) & set(flux)


def test_encoder_catalog_reports_stock_for_an_unmarked_pack(isolated_dirs):
    from refract_backend.paths import models_dir

    # A directory with no manifest says nothing about its encoder, so the safe reading is
    # "stock" rather than crediting an ablation that was never recorded.
    (models_dir() / "flux2-klein-4b").mkdir(parents=True)
    catalog = encoders.describe_encoders()
    assert catalog["active"]["flux2"] == "flux2-stock"
    assert [e for e in catalog["encoders"] if e["installed"]] == [
        e for e in catalog["encoders"] if e["key"] == "flux2-stock"
    ]


def test_encoder_catalog_detects_the_staged_klein_ablated_encoder(isolated_dirs):
    from refract_backend.paths import models_dir

    staged = models_dir() / "flux2-klein-4b"
    (staged / "text_encoder").mkdir(parents=True)
    (staged / "refract-manifest.json").write_text(json.dumps({"encoder": "ablated"}))
    assert encoders.installed_encoder("flux2").key == "flux2-ablated"
    assert encoders.describe_encoders()["active"]["flux2"] == "flux2-ablated"


def test_encoder_catalog_reads_the_heretic_marker_from_the_qwen_manifest(isolated_dirs, monkeypatch):
    from refract_backend.paths import models_dir

    pack = models_dir() / "qwen-image-2.1-mlx-q4"
    (pack / "text_encoder").mkdir(parents=True)
    (pack / "refract-manifest.json").write_text(
        json.dumps({"components": {"text_encoder": "pottokao/...-Heretic@047e5434 (abliterated)"}})
    )
    monkeypatch.setattr(encoders, "qwen_pack", lambda: pack)
    assert encoders.installed_encoder("qwen21").key == "qwen21-heretic"


def test_request_default_steps_are_family_agnostic():
    # The request carries the user's value; the runner is what clamps it per family, so the
    # schema itself stays family-neutral and nothing is silently rewritten on the way in.
    assert GenerateRequest(prompt="a vase").steps == 40
