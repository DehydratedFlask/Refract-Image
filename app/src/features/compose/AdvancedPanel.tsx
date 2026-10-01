import { useStore } from "../../store/useStore";
import { Button, Field, Row, Switch } from "../../components/ui";


const SIZE_PRESETS = [
  { label: "512", value: 512 },
  { label: "768", value: 768 },
  { label: "1024", value: 1024 },
  { label: "1280", value: 1280 },
];

const OUTPUT_RESOLUTIONS = [256, 384, 512, 640, 768, 896, 1024, 1280, 1536];

export function AdvancedPanel() {
  const params = useStore((state) => state.params);
  const setParams = useStore((state) => state.setParams);
  const sources = useStore((state) => state.sources);
  const encoders = useStore((state) => state.encoders);
  const activeEncoders = useStore((state) => state.activeEncoders);
  const system = useStore((state) => state.system);
  // FLUX.2 carries its own text encoder (a text-only Qwen3-4B), so the encoder list shown
  // here follows the family of the selected source rather than listing both.
  const isFlux2 = params.model_source === "flux2-klein-4b";
  const familyEncoders = isFlux2
    ? encoders.filter((e) => e.family === "flux2")
    : encoders.filter((e) => e.family === "qwen21");
  const activeEncoder = familyEncoders.find((e) => e.key === activeEncoders[isFlux2 ? "flux2" : "qwen21"]);

  const lowRamHint =
    system && system.total_ram_gb > 0 && system.total_ram_gb < 32
      ? "Recommended on this machine (under 32 GB of memory)"
      : "Evicts the text encoder after encoding and reloads it per run";

  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="row" style={{ gap: 12, alignItems: "flex-start" }}>
        <Field label="Steps" hint="40 recommended">
          <input
            className="input"
            type="number"
            min={1}
            max={100}
            value={params.steps}
            onChange={(event) => setParams({ steps: clamp(Number(event.target.value), 1, 100) })}
          />
        </Field>
        <Field label="Guidance" hint={params.guidance <= 1 ? "off" : "CFG"}>
          <input
            className="input"
            type="number"
            min={1}
            max={20}
            step={0.5}
            value={params.guidance}
            onChange={(event) => setParams({ guidance: Math.max(1, Number(event.target.value)) })}
          />
        </Field>
      </div>

      <Field label="Seed" hint={params.seed === null ? "random each run" : `fixed at ${params.seed}`}>
        <div className="row" style={{ gap: 6 }}>
          <input
            className="input mono"
            type="number"
            placeholder="random"
            style={{ maxWidth: 180 }}
            value={params.seed ?? ""}
            onChange={(event) => setParams({ seed: event.target.value === "" ? null : Number(event.target.value) })}
          />
          <Button variant="subtle" title="Randomise the seed" onClick={() => setParams({ seed: Math.floor(Math.random() * 2 ** 31) })}>
            🎲
          </Button>
          {params.seed !== null ? (
            <Button variant="subtle" onClick={() => setParams({ seed: null })}>
              Clear
            </Button>
          ) : null}
        </div>
      </Field>

      {params.guidance > 1 ? (
        <Field label="Negative prompt" hint="only used when guidance is above 1">
          <input
            className="input"
            placeholder="blurry, low quality, watermark"
            value={params.negative_prompt ?? ""}
            spellCheck={false}
            onChange={(event) => setParams({ negative_prompt: event.target.value || null })}
          />
        </Field>
      ) : null}

      <div className="col" style={{ gap: 8 }}>
        <span className="field-label">
          <span className="field-label-text">Output size</span>
          <span className="field-label-hint">multiples of 32</span>
        </span>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          {SIZE_PRESETS.map((preset) => (
            <Button
              key={preset.value}
              variant={params.width === preset.value && params.height === preset.value ? "primary" : "default"}
              onClick={() => setParams({ width: preset.value, height: preset.value })}
            >
              {preset.label}²
            </Button>
          ))}
          <Button
            variant={params.width === null && params.height === null ? "primary" : "default"}
            title="One reference: preserve original pixel dimensions. Multiple references: derive shape from the last image."
            onClick={() => setParams({ width: null, height: null })}
          >
            auto
          </Button>
        </div>
        <div className="row" style={{ gap: 6, alignItems: "center" }}>
          <span className="caption faint" style={{ width: 62 }}>
            custom
          </span>
          <input
            className="input mono narrow"
            type="number"
            step={32}
            min={256}
            max={2048}
            value={params.width ?? ""}
            placeholder="auto"
            onChange={(event) => setParams({ width: event.target.value === "" ? null : Number(event.target.value) })}
          />
          <span className="faint">×</span>
          <input
            className="input mono narrow"
            type="number"
            step={32}
            min={256}
            max={2048}
            value={params.height ?? ""}
            placeholder="auto"
            onChange={(event) => setParams({ height: event.target.value === "" ? null : Number(event.target.value) })}
          />
          <span className="caption faint" title="A blank dimension is derived from the last reference image's aspect ratio">
            blank = reference size
          </span>
        </div>
      </div>

      <Field label="Reference budget" hint="pixel area each reference is resized to">
        <select
          className="select"
          value={params.output_resolution}
          onChange={(event) => setParams({ output_resolution: Number(event.target.value) })}
        >
          {OUTPUT_RESOLUTIONS.map((value) => (
            <option key={value} value={value}>
              {value} px — {value <= 512 ? "fastest, matches the pipeline's own examples" : value >= 1024 ? "most detail, slower" : "balanced"}
            </option>
          ))}
        </select>
      </Field>

      <div className="divider" />

      <Field label="Model source" hint="switchable per run">
        <select
          className="select"
          value={params.model_source}
          onChange={(event) => useStore.getState().updateSettings({ modelSource: event.target.value })}
        >
          {sources.map((source) => (
            <option key={source.id} value={source.id}>
              {source.label}
              {source.available ? "" : " (not ready)"}
            </option>
          ))}
        </select>
      </Field>

      {params.model_source === "prepared" || params.model_source === "custom" ? (
        <Field label="Model directory" hint="absolute path">
          <input
            className="input mono"
            placeholder={sources.find((s) => s.id === params.model_source)?.location ?? "/path/to/model"}
            value={params.model_path ?? ""}
            onChange={(event) => setParams({ model_path: event.target.value || null })}
            spellCheck={false}
          />
        </Field>
      ) : null}

      <Field
        label="Text encoder"
        hint={
          isFlux2
            ? "Baked into the staged pack by prepare_klein; shown for reference"
            : activeEncoder
              ? activeEncoder.label
              : "No pack staged — the encoder is decided at Prepare time"
        }
      >
        <select className="select" value={activeEncoder?.key ?? ""} disabled>
          {familyEncoders.length ? (
            familyEncoders.map((entry) => (
              <option key={entry.key} value={entry.key}>
                {entry.label}
                {entry.installed ? " (in use)" : ""}
              </option>
            ))
          ) : (
            <option value="">none staged</option>
          )}
        </select>
      </Field>

      <div className="divider" />

      <Row label="Reuse text/reference prefix" hint="KV cache — faster, off only for A/B comparison">
        <Switch on={params.use_kv_cache} onChange={(next) => setParams({ use_kv_cache: next })} label="KV cache" />
      </Row>

      <Row label="Low memory mode" hint={lowRamHint}>
        <Switch on={params.low_ram} onChange={(next) => useStore.getState().updateSettings({ lowRam: next })} label="Low memory" />
      </Row>

      <Row label="Tile the VAE decode" hint="Lower peak memory at large sizes, marginally slower">
        <Switch on={params.vae_tiling} onChange={(next) => useStore.getState().updateSettings({ vaeTiling: next })} label="VAE tiling" />
      </Row>

      <Row label="Save metadata sidecar" hint="Records every setting next to the PNG">
        <Switch
          on={params.save_metadata}
          onChange={(next) => useStore.getState().updateSettings({ saveMetadata: next })}
          label="Metadata"
        />
      </Row>

      <div className="row" style={{ gap: 12, alignItems: "flex-start" }}>
        <Field label="Live previews" hint="every N steps, 0 = off">
          <input
            className="input"
            type="number"
            min={0}
            max={40}
            value={params.preview_interval}
            onChange={(event) =>
              useStore.getState().updateSettings({ previewInterval: clamp(Number(event.target.value), 0, 40) })
            }
          />
        </Field>
        <Field label="MLX cache cap" hint="GB, blank = none">
          <input
            className="input"
            type="number"
            min={0}
            step={0.5}
            placeholder="none"
            value={params.mlx_cache_limit_gb ?? ""}
            onChange={(event) =>
              useStore
                .getState()
                .updateSettings({ mlxCacheLimitGb: event.target.value === "" ? null : Number(event.target.value) })
            }
          />
        </Field>
      </div>

      {/* The destination lives on the generate bar, always in view, and in Settings —
          one setting, one place to look while a run is being set up. */}
    </div>
  );
}

function clamp(value: number, min: number, max: number): number {
  if (Number.isNaN(value)) return min;
  return Math.max(min, Math.min(max, value));
}
