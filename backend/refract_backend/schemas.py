"""Request/response shapes for the local HTTP API, plus the validation rules.

The limits here mirror what the Qwen-Image-2.1 reference pipeline actually accepts
(at most ten reference images, 32-pixel dimension steps, guidance >= 1). Validating in one
place means the UI can surface a precise reason instead of a stack trace from deep inside
the model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

MAX_REFERENCES = 10
DIMENSION_MULTIPLE = 32
REFERENCE_RESOLUTIONS = (256, 384, 512, 640, 768, 896, 1024, 1280, 1536, 2048)
QUANTIZE_CHOICES = (3, 4, 5, 6, 8)

ModelSourceId = Literal["mlx-q4", "upstream-q4", "flux2-klein-4b", "prepared", "custom"]

PHASES = (
    "queued",
    "resolving",
    "downloading",
    "loading",
    "encoding",
    "denoise",
    "decoding",
    "saving",
    "done",
    "cancelled",
    "failed",
)


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    resolved_prompt: str | None = None
    avatar_bindings: list[dict] = Field(default_factory=list)
    negative_prompt: str | None = None
    reference_paths: list[str] = Field(default_factory=list)
    width: int | None = None
    height: int | None = None
    match_reference_size: bool = True
    output_resolution: int = 1024
    steps: int = Field(default=40, ge=1, le=100)
    guidance: float = Field(default=1.0, ge=1.0, le=20.0)
    seed: int | None = None
    seeds: list[int] | None = None
    quantize: int | None = 4
    use_kv_cache: bool = True
    low_ram: bool = False
    vae_tiling: bool = False
    mlx_cache_limit_gb: float | None = Field(default=None, gt=0)
    preview_interval: int = Field(default=5, ge=0, le=40)
    model_source: ModelSourceId = "mlx-q4"
    model_path: str | None = None
    #: Which project this run belongs to, so the Library can be filtered to it later.
    #: Optional: a run with no project is still valid and simply stays unfiled.
    project_id: str | None = None
    project_session_id: str | None = None
    save_metadata: bool = True
    output_dir: str | None = None
    output_name: str | None = None

    @field_validator("reference_paths")
    @classmethod
    def _check_references(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_REFERENCES:
            raise ValueError(f"at most {MAX_REFERENCES} reference images are supported")
        cleaned = []
        for item in value:
            text = item.strip()
            if text:
                cleaned.append(text)
        return cleaned

    @field_validator("width", "height")
    @classmethod
    def _check_dimensions(cls, value: int | None, info) -> int | None:
        if value is None:
            return None
        if value % DIMENSION_MULTIPLE != 0:
            raise ValueError(
                f"{info.field_name} must be a multiple of {DIMENSION_MULTIPLE} "
                f"(Qwen-Image-2.1 packs latents in {DIMENSION_MULTIPLE}px tiles); got {value}"
            )
        if not 256 <= value <= 2048:
            raise ValueError(f"{info.field_name} must be between 256 and 2048; got {value}")
        return value

    @field_validator("output_resolution")
    @classmethod
    def _check_output_resolution(cls, value: int) -> int:
        if value not in REFERENCE_RESOLUTIONS:
            allowed = ", ".join(str(v) for v in REFERENCE_RESOLUTIONS)
            raise ValueError(f"output resolution must be one of {allowed}; got {value}")
        return value

    @field_validator("quantize")
    @classmethod
    def _check_quantize(cls, value: int | None) -> int | None:
        if value is not None and value not in QUANTIZE_CHOICES:
            raise ValueError(f"quantize must be one of {QUANTIZE_CHOICES} or null; got {value}")
        return value

    def seed_list(self) -> list[int]:
        if self.seeds:
            return list(self.seeds)
        return [self.seed if self.seed is not None else -1]

    def to_metadata(self) -> dict:
        return self.model_dump(exclude={"reference_paths", "output_dir", "output_name"})


class SourceInfo(BaseModel):
    id: str
    label: str
    detail: str
    repo_id: str | None = None
    approx_download_bytes: int = 0
    available: bool = False
    location: str | None = None
    size_bytes: int = 0
    quantized_bits: int | None = None
    notes: list[str] = Field(default_factory=list)


class ReferencePreview(BaseModel):
    path: str
    name: str
    width: int | None = None
    height: int | None = None
    size_bytes: int = 0


class JobRecord(BaseModel):
    id: str
    status: str
    phase: str = "queued"
    prompt: str = ""
    negative_prompt: str | None = None
    references: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    preview_path: str | None = None
    step: int = 0
    total_steps: int = 0
    seeds: list[int] = Field(default_factory=list)
    seconds_per_step: float | None = None
    eta_seconds: float | None = None
    elapsed_seconds: float = 0.0
    peak_memory_gb: float | None = None
    message: str | None = None
    error: str | None = None
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    model_source: str = "mlx-q4"
    model_path: str | None = None
    quantize: int | None = None
    width: int | None = None
    height: int | None = None
    params: dict = Field(default_factory=dict)


class ValidateRequest(BaseModel):
    path: str
    component: Literal["transformer", "text_encoder", "vae", "model"] = "transformer"
    base_model_path: str | None = None
    quantize: int | None = 4


class InstallRequest(BaseModel):
    path: str
    base_model_path: str
    name: str = "custom-4bit"
    quantize: int | None = 4
    force: bool = False


class DeleteRequest(BaseModel):
    path: str
    confirm: bool = False
