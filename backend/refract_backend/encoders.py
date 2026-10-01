"""Text encoder variants, per model family.

The abliteration story differs per family, and conflating them is exactly the mistake worth
preventing:

* **Qwen-Image-2.1** uses a *vision-language* encoder (`Qwen3-VL-8B-Instruct`, with the visual
  tower that reference editing depends on). Two encoders are available: the stock mlx-community
  4-bit one, and the Heretic-ablated conversion of `pottokao/…-Text-Encoder-Heretic`. Both are
  real Qwen-Image encoders; the pack holds whichever was installed last.
* **FLUX.2 Klein 4B** uses a *text-only* `Qwen3-4B`. Its ablated variant is a llama.cpp GGUF
  and has to be dequantised into mflux's layout by ``tools/prepare_klein``.

An encoder is only offered for a family whose module tree it actually matches — offering the
Klein encoder as a Qwen-Image option is the mistake this module exists to make impossible.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .paths import models_dir


@dataclass(frozen=True)
class EncoderOption:
    key: str
    family: str
    label: str
    detail: str
    #: Set when this encoder is not merely downloadable but already inside a staged pack.
    installed: bool = False
    installed_path: str | None = None
    default: bool = False


#: Encoders Refract can put in front of each family, keyed by (family, key).
ENCODERS: tuple[EncoderOption, ...] = (
    EncoderOption(
        key="qwen21-stock",
        family="qwen21",
        label="Stock Qwen3-VL 4-bit",
        detail="mlx-community/Qwen-Image-2.1-mflux-q4. Original weights, no ablation.",
    ),
    EncoderOption(
        key="qwen21-heretic",
        family="qwen21",
        label="Heretic-ablated Qwen3-VL 4-bit",
        detail=(
            "pottokao/Qwen-Image-2.1-Text-Encoder-Heretic, directionally ablated and "
            "converted to MLX 4-bit with the visual tower grafted back in. Refusals "
            "measured at 5/100 against the stock encoder's 100/100."
        ),
        default=True,
    ),
    EncoderOption(
        key="flux2-stock",
        family="flux2",
        label="Stock Qwen3-4B",
        detail="The bf16 text encoder that ships with FLUX.2 Klein 4B.",
    ),
    EncoderOption(
        key="flux2-ablated",
        family="flux2",
        label="Ablated Qwen3-4B (GGUF Q4_0)",
        detail=(
            "Huihui abliteration, dequantised to bf16 by prepare_klein. Removes prompt "
            "refusal but adds no visual knowledge."
        ),
        default=True,
    ),
)


def _manifest(pack: Path) -> dict[str, Any]:
    path = pack / "refract-manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def qwen_pack() -> Path | None:
    """The staged Qwen-Image pack, whose text_encoder/ was last installed by hand."""
    from .model_store import staged_pack_dir

    return staged_pack_dir()


def klein_pack() -> Path:
    return models_dir() / "flux2-klein-4b"


def _installed_marker(pack: Path | None) -> tuple[str | None, str | None]:
    """(encoder key, encoder path) for whatever a staged pack actually contains.

    Reads the manifest written by the prepare tools, then the sidecar `encoder-origin.json`
    that prepare_klein leaves inside the encoder directory. A pack with neither is treated as
    stock rather than guessed at, so the UI never claims an ablation that is not there.
    """
    if pack is None or not pack.is_dir():
        return None, None
    manifest = _manifest(pack)
    origin = pack / "text_encoder" / "encoder-origin.json"
    if origin.exists():
        try:
            data = json.loads(origin.read_text())
        except (OSError, ValueError):
            data = {}
        return data.get("encoder") or "ablated", str(pack / "text_encoder")
    # prepare_klein records the chosen variant in the manifest; without that the pack is the
    # stock one, whatever the directory is called.
    marker = manifest.get("encoder")
    if isinstance(marker, str):
        return marker, str(pack / "text_encoder")
    # The Qwen pack is staged by ingest and the encoder swapped in place afterwards, so its
    # provenance is a prose entry under manifest["components"]["text_encoder"] rather than a
    # key. "Heretic" is the only ablated variant for that family, so matching on it states
    # what the manifest recorded rather than guessing at the weights.
    component = manifest.get("components", {}).get("text_encoder")
    if isinstance(component, str) and "heretic" in component.lower():
        return "heretic", str(pack / "text_encoder")
    return "stock", str(pack / "text_encoder")


#: Marker recorded on disk -> the EncoderOption key it means for each family. The markers
#: are what the prepare tools write, so they stay short and stable.
_MARKER_TO_KEY = {
    ("qwen21", "heretic"): "qwen21-heretic",
    ("qwen21", "stock"): "qwen21-stock",
    ("flux2", "ablated"): "flux2-ablated",
    ("flux2", "stock"): "flux2-stock",
}


def _pack_for(family: str) -> Path | None:
    return qwen_pack() if family == "qwen21" else klein_pack()


def installed_encoder(family: str) -> EncoderOption | None:
    pack = _pack_for(family)
    if pack is None or not pack.is_dir():
        return None
    marker, _ = _installed_marker(pack)
    key = _MARKER_TO_KEY.get((family, marker or ""))
    if key is None:
        return None
    return next((e for e in ENCODERS if e.key == key), None)


def list_encoders(family: str | None = None) -> list[dict[str, Any]]:
    """The encoders offered for a family, annotated with what is actually installed."""
    active: dict[str, str | None] = {}
    for candidate in ("qwen21", "flux2"):
        found = installed_encoder(candidate)
        active[candidate] = found.key if found else None

    entries: list[dict[str, Any]] = []
    for option in ENCODERS:
        if family and option.family != family:
            continue
        entries.append(
            {
                **asdict(option),
                "installed": active.get(option.family) == option.key,
                "active": active.get(option.family),
            }
        )
    return entries


def describe_encoders() -> dict[str, Any]:
    packs = {"qwen21": qwen_pack(), "flux2": klein_pack()}
    return {
        "encoders": list_encoders(),
        "active": {
            family: (installed_encoder(family).key if installed_encoder(family) else None)
            for family in ("qwen21", "flux2")
        },
        "packs": {family: (str(pack) if pack and pack.is_dir() else None) for family, pack in packs.items()},
        "note": (
            "Encoders are per model family and are not interchangeable: FLUX.2 Klein uses a "
            "text-only Qwen3-4B while Qwen-Image-2.1 uses a vision-language Qwen3-VL-8B "
            "whose visual tower is what lets reference editing see the image."
        ),
    }
