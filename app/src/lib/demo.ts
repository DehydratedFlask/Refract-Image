/**
 * Demo mode: the whole UI, none of the model.
 *
 * Enabled with `?demo=1` (or VITE_REFRACT_DEMO=1). Every service call is answered from
 * here, jobs report scripted progress, and images are generated SVG placeholders. It is
 * what lets the interface be reviewed — layout, states, dark mode, hover and drag
 * affordances — without a 9 GB download, and it doubles as a way for someone to see what
 * the app looks like before committing to the runtime install.
 *
 * Nothing in demo mode ever claims to be real: the health endpoint reports `mock: true`,
 * so the app shows its mock banner.
 */

import type {
  HealthInfo,
  Job,
  JobEvent,
  LibraryItem,
  SourceEntry,
  SystemInfo,
  ValidationReport,
} from "./api";

const params = new URLSearchParams(typeof window !== "undefined" ? window.location.search : "");
declare global {
  interface Window {
    /** Set by the standalone preview bundle built in scripts/make-preview.py. */
    __REFRACT_DEMO__?: boolean;
  }
}
export const DEMO_ENABLED =
  params.has("demo") ||
  (typeof window !== "undefined" && window.__REFRACT_DEMO__ === true) ||
  (import.meta.env.VITE_REFRACT_DEMO as string | undefined) === "1";

let counter = 0;
const nextId = () => `demo${(++counter).toString(36)}${Math.random().toString(36).slice(2, 6)}`;

/** A deterministic placeholder that reads as an image rather than a grey box. */
export function demoImage(label: string, width = 512, height = 512, hue = 210): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="hsl(${hue},72%,58%)"/>
      <stop offset="55%" stop-color="hsl(${(hue + 48) % 360},64%,44%)"/>
      <stop offset="100%" stop-color="hsl(${(hue + 96) % 360},58%,26%)"/>
    </linearGradient>
    <radialGradient id="v" cx="0.3" cy="0.2" r="0.9">
      <stop offset="0%" stop-color="rgba(255,255,255,0.35)"/>
      <stop offset="100%" stop-color="rgba(255,255,255,0)"/>
    </radialGradient>
  </defs>
  <rect width="${width}" height="${height}" fill="url(#g)"/>
  <rect width="${width}" height="${height}" fill="url(#v)"/>
  <g fill="rgba(255,255,255,0.9)" font-family="-apple-system, SF Pro Text, Helvetica, sans-serif">
    <text x="${width / 2}" y="${height / 2}" font-size="${Math.round(width / 16)}" text-anchor="middle" font-weight="600">${escapeXml(label)}</text>
    <text x="${width / 2}" y="${height / 2 + Math.round(width / 11)}" font-size="${Math.round(width / 28)}" text-anchor="middle" opacity="0.75">demo placeholder</text>
  </g>
</svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

function escapeXml(value: string): string {
  return value.replace(/[<>&"']/g, (character) =>
    ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" })[character] ?? character,
  );
}

const DEMO_REFERENCES = ["demo:reference-1", "demo:reference-2"];
const DEMO_OUTPUT = "demo:result-1";

export const demoHealth: HealthInfo = {
  ok: true,
  version: "0.0.2-demo",
  mock: true,
  runner_ready: false,
  runner_note: "Demo mode: no MLX model is loaded. Install the runtime and start the app to generate for real.",
  mflux_version: "0.20.0 (demo)",
  mflux_commit: "adcbe85975760d545ca142069713781929898be0",
  mlx_version: "0.32.3",
  uptime_seconds: 128,
  busy: false,
};

export const demoSystem: SystemInfo = {
  chip: "Apple M4 Pro",
  architecture: "arm64",
  macos_version: "27.0",
  python_version: "3.12.13",
  total_ram_gb: 48,
  free_disk_gb: 306,
  mlx_version: "0.32.3",
  mflux_version: "0.20.0 (demo)",
  mflux_commit: "adcbe85975760d545ca142069713781929898be0",
  runner_ready: false,
  runner_note: demoHealth.runner_note,
  data_root: "/Users/you/Library/Application Support/Refract",
  data_volume: "Macintosh HD",
  data_is_external: false,
  models_dir: "/Users/you/Library/Application Support/Refract/models",
  outputs_dir: "/Users/you/Library/Application Support/Refract/outputs",
  hf_cache_dir: "/Users/you/Library/Application Support/Refract/hf-home/hub",
  models_size_bytes: 10_900_000_000,
  hf_cache_size_bytes: 19_400_000_000,
  mlx_active_memory_gb: 0,
  mlx_peak_memory_gb: 19.4,
  busy: false,
  data_dir: "/Users/you/Library/Application Support/Refract",
};

export const demoSources: SourceEntry[] = [
  {
    id: "mlx-q4",
    label: "Qwen-Image-2.1 Uncensored MLX 4-bit",
    detail: "The Uncensored 4-bit MLX transformer, with a 4-bit Qwen3-VL text encoder",
    repo_id: "abenzerps/Qwen-Image-2.1-Uncensored-GGUF",
    approx_download_bytes: 14_000_000_000,
    notes: [
      "The image model is the Uncensored repo's qwen-image-2.1-UC-MLX-4bit.safetensors (4.0 GB, MLX 4-bit affine).",
      "That repository ships no 4-bit text encoder, so the 4-bit Qwen3-VL encoder comes from an mflux-format conversion.",
      "Prepare also grafts in the Qwen3-VL visual tower that reference editing needs to read the reference images.",
    ],
    available: true,
    location:
      ".refract/hf-home/hub/models--abenzerps--Qwen-Image-2.1-Uncensored-GGUF/snapshots/6b34e59/qwen-image-2.1-UC-MLX-4bit.safetensors",
    size_bytes: 14_000_000_000,
    quantized_bits: null,
  },
  {
    id: "upstream-q4",
    label: "Upstream + 4-bit at load",
    detail: "Qwen/Qwen-Image-2.1 with the transformer quantised to 4-bit when it loads",
    repo_id: "Qwen/Qwen-Image-2.1",
    approx_download_bytes: 33_100_000_000,
    notes: [
      "The text encoder stays bf16 (17.5 GB resident) because mflux deliberately does not quantise it.",
      "Use Prepare to write the quantised result to disk once and skip that work on every later launch.",
    ],
    available: false,
    location: null,
    size_bytes: 0,
    quantized_bits: 4,
  },
  {
    id: "prepared",
    label: "Prepared local model",
    detail: "A model directory written by Prepare, loaded straight from disk",
    repo_id: null,
    approx_download_bytes: 0,
    notes: ["Fastest warm start and the base directory used by custom-weight installs."],
    available: true,
    location: `${demoSystem.models_dir}/qwen-image-2.1-mlx-q4`,
    size_bytes: 10_900_000_000,
    quantized_bits: 4,
    installed: [
      {
        name: "qwen-image-2.1-mlx-q4",
        path: `${demoSystem.models_dir}/qwen-image-2.1-mlx-q4`,
        size_bytes: 10_900_000_000,
        bits: 4,
        created_at: "2026-09-30T07:00:00Z",
        origin: "abenzerps/Qwen-Image-2.1-Uncensored-GGUF",
        customised: false,
      },
    ],
  },
  {
    id: "custom",
    label: "Custom weights",
    detail: "A local directory installed from a third-party transformer pack",
    repo_id: null,
    approx_download_bytes: 0,
    notes: [
      "Built by Models > Install custom weights: a base model directory with its transformer component replaced by a validated pack.",
      "Validation compares every tensor name and shape against the live mflux module tree and then loads the pack through mflux itself.",
    ],
    available: false,
    location: null,
    size_bytes: 0,
    quantized_bits: null,
  },
];

export const demoValidation: ValidationReport = {
  ok: false,
  source: "/Users/you/Downloads/qwen-image-2.1-UC-MLX-4bit.safetensors",
  component: "transformer",
  errors: ["412 missing, 3 unexpected and 0 shape-mismatched tensors against the mflux Qwen-Image-2.1 transformer"],
  warnings: [],
  tensor_count: 841,
  total_bytes: 4_002_363_741,
  expected_keys: 1248,
  matched_keys: 836,
  coverage: 0.67,
  stripped_prefixes: ["transformer."],
  quantization: { quantized: true, bits: 4, group_size: 64, layers: 418, consistent: true, oddities: [] },
  missing: { count: 412, examples: ["transformer_blocks.0.img_mlp.gate_layer.weight", "txt_in.in_layer.weight"] },
  unexpected: { count: 3, examples: ["model.visual.patch_embed.weight"] },
  shape_mismatches: { count: 0, examples: [] },
};

const now = Date.now() / 1000;

function demoItem(
  index: number,
  prompt: string,
  overrides: Partial<LibraryItem> = {},
): LibraryItem {
  return {
    id: `demo-history-${index}`,
    created_at: now - index * 5400,
    prompt,
    negative_prompt: null,
    params: {
      prompt,
      steps: 40,
      guidance: 1,
      output_resolution: 1024,
      width: 1024,
      height: 1024,
      seed: 1000 + index,
      quantize: 4,
      model_source: "mlx-q4",
      use_kv_cache: true,
    },
    references: index % 2 === 0 ? [] : DEMO_REFERENCES,
    outputs: [`demo:result-${index}`],
    status: "done",
    duration_s: 248 + index * 22,
    peak_memory_gb: 18.6 + index * 0.5,
    model_source: "mlx-q4",
    model_path: null,
    quantize: 4,
    width: 1024,
    height: 1024,
    seeds: [1000 + index],
    favorite: index === 1,
    error: null,
    ...(overrides as object),
  } as LibraryItem & { hue?: number } & { hue: number };
}

export const demoLibrary: LibraryItem[] = [
  demoItem(0, "A detailed illustrated botanical poster with the title SPRING"),
  demoItem(1, "Change the jacket to dark green. Preserve the person's face and pose."),
  demoItem(2, "Place the subject from image 1 in the setting of image 2, golden hour"),
  demoItem(3, "Cute red panda sticker, transparent background, thick outline"),
  demoItem(4, "A moody coastal landscape at dusk, long exposure, film grain"),
];

export function demoFileUrl(path: string): string | null {
  if (!path.startsWith("demo:")) return null;
  const name = path.replace("demo:", "");
  const hue = (name.length * 47) % 360;
  const label = name.replace(/-/g, " ").replace(/\b\d+\b/, "").trim() || name;
  return demoImage(label, 768, 768, hue);
}

// ---------------------------------------------------------------- job scripting
export function demoJob(kind: string, payload: Record<string, unknown>): Job {
  const id = nextId();
  const steps = Number(payload.steps ?? 40);
  return {
    id,
    kind: kind as Job["kind"],
    status: "running",
    phase: kind === "generate" ? "loading" : kind,
    message: "demo job",
    step: 0,
    total_steps: kind === "generate" ? steps : 0,
    seeds: [],
    outputs: [],
    preview_path: null,
    references: (payload.reference_paths as string[]) ?? [],
    result: null,
    created_at: Date.now() / 1000,
    started_at: Date.now() / 1000,
    finished_at: null,
    elapsed_seconds: 0,
    seconds_per_step: null,
    eta_seconds: null,
    peak_memory_gb: null,
    downloaded_bytes: 0,
    total_bytes: null,
    model_source: (payload.model_source as string) ?? "mlx-q4",
    model_path: null,
    quantize: 4,
    width: Number(payload.width ?? 1024),
    height: Number(payload.height ?? 1024),
    payload,
    error: null,
  };
}

export function demoJobReader(job: Job) {
  let seq = 0;
  return () => {
    seq += 1;
    return { ...job, seq, phase: job.phase, status: job.status };
  };
}

export function demoScript(job: Job): JobEvent[] {
  const steps = job.total_steps || 12;
  const events: JobEvent[] = [];
  let seq = 1;
  events.push({ seq: seq++, at: 0, phase: "loading", message: "loading Qwen-Image-2.1 Uncensored 4-bit" });
  events.push({ seq: seq++, at: 0.4, phase: "loading", message: "model ready in 6.2s (4-bit)" });
  events.push({
    seq: seq++,
    at: 0.6,
    phase: "encoding",
    message: job.references.length
      ? `conditioning on ${job.references.length} reference images`
      : "text-to-image (no reference images)",
  });
  const stride = Math.max(1, Math.round(steps / 14));
  for (let step = stride; step <= steps; step += stride) {
    const elapsed = 0.8 + step * 0.85;
    events.push({
      seq: seq++,
      at: elapsed,
      phase: "denoise",
      step,
      total_steps: steps,
      elapsed_seconds: elapsed,
      seconds_per_step: 0.85,
      eta_seconds: 0.85 * (steps - step),
      peak_memory_gb: 8.5 + (step / steps) * 4,
      preview_path: `demo:preview-${step}`,
    });
  }
  events.push({
    seq: seq++,
    at: 0.8 + steps * 0.85 + 1.5,
    phase: "decoding",
    message: "decoding latents",
  });
  events.push({
    seq: seq++,
    at: 0.8 + steps * 0.85 + 3,
    phase: "saving",
    message: "writing the image to disk",
  });
  const seed = job.seeds[0] ?? 4242;
  events.push({
    seq: seq++,
    at: 0.8 + steps * 0.85 + 3.4,
    phase: "done",
    message: "done",
    outputs: [DEMO_OUTPUT],
    seeds: [seed],
    step: steps,
    total_steps: steps,
    peak_memory_gb: 13.1,
    elapsed_seconds: 0.8 + steps * 0.85 + 3.4,
  });
  return events;
}
