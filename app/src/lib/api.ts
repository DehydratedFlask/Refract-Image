/**
 * Client for the local Refract Image service.
 *
 * The service binds an ephemeral loopback port and mints a token per launch, so the app learns
 * both from its host at startup — the Swift app (or the Rust shell) started the service, so it is
 * the authority on where it landed. The same client also works in a plain browser (used for
 * development and QA screenshots) by reading `?api=&token=` or a `localStorage` record, which is
 * why connection details are resolved once and cached rather than read from a build-time constant.
 */

import { hostInvoke, isNativeHost } from "./host";

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface BackendInfo {
  port: number;
  host: string;
  token: string;
  pid: number;
  mock: boolean;
  mflux: string | null;
}

export interface Connection {
  base: string;
  token: string;
  mock: boolean;
}

export interface GenerateParams {
  prompt: string;
  negative_prompt?: string | null;
  reference_paths: string[];
  width?: number | null;
  height?: number | null;
  match_reference_size?: boolean;
  output_resolution: number;
  steps: number;
  guidance: number;
  seed: number | null;
  quantize: number | null;
  use_kv_cache: boolean;
  low_ram: boolean;
  vae_tiling: boolean;
  mlx_cache_limit_gb: number | null;
  preview_interval: number;
  model_source: string;
  model_path?: string | null;
  project_id?: string | null;
  project_session_id?: string | null;
  save_metadata: boolean;
  output_dir?: string | null;
  output_name?: string | null;
}

export interface Avatar {
  id: string;
  name: string;
  handle: string;
  description: string;
  references: string[];
  updated_at: number;
}
export type AvatarDraft = Pick<Avatar, "name" | "handle" | "description" | "references">;

export interface JobEvent {
  seq: number;
  at: number;
  status?: JobStatus;
  job_id?: string;
  width?: number;
  height?: number;
  phase?: string;
  message?: string;
  step?: number;
  total_steps?: number;
  elapsed_seconds?: number;
  seconds_per_step?: number | null;
  eta_seconds?: number | null;
  peak_memory_gb?: number | null;
  preview_path?: string | null;
  outputs?: string[] | null;
  seeds?: number[];
  downloaded_bytes?: number;
  total_bytes?: number | null;
  final?: boolean;
}

export interface Job {
  id: string;
  kind: "generate" | "download" | "prepare" | "prepare-klein" | "install" | "validate";
  status: JobStatus;
  phase: string;
  message: string | null;
  error: string | null;
  step: number;
  total_steps: number;
  seeds: number[];
  outputs: string[];
  preview_path: string | null;
  references: string[];
  result: Record<string, unknown> | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  elapsed_seconds: number;
  seconds_per_step: number | null;
  eta_seconds: number | null;
  peak_memory_gb: number | null;
  downloaded_bytes: number;
  total_bytes: number | null;
  model_source: string | null;
  model_path: string | null;
  quantize: number | null;
  width: number | null;
  height: number | null;
  payload: Record<string, unknown>;
  seq?: number;
  events?: JobEvent[];
}

export interface LibraryItem {
  id: string;
  created_at: number;
  prompt: string;
  negative_prompt: string | null;
  params: Partial<GenerateParams>;
  references: string[];
  outputs: string[];
  status: string;
  duration_s: number | null;
  peak_memory_gb: number | null;
  model_source: string | null;
  model_path: string | null;
  quantize: number | null;
  width: number | null;
  height: number | null;
  seeds: number[];
  favorite: boolean;
  project_id: string | null;
  error: string | null;
}

export interface SourceEntry {
  id: string;
  label: string;
  detail: string;
  /** Which job kind installs this source: "download", or "prepare-klein" for FLUX.2. */
  install_job?: string;
  repo_id: string | null;
  approx_download_bytes: number;
  notes: string[];
  available: boolean;
  location: string | null;
  size_bytes: number;
  quantized_bits: number | null;
  installed?: InstalledModel[];
}

/** A saved workspace: a name, a full generation session, and its own reference copies. */
export interface ProjectSession {
  id: string;
  name: string;
  session: Partial<GenerateParams>;
  references: string[];
  updated_at: number;
}

export interface Project {
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  prompt: string;
  session: Partial<GenerateParams>;
  references: string[];
  /** Paths whose copy has gone missing; reported rather than silently dropped. */
  missing_references: string[];
  active_session_id: string;
  sessions: ProjectSession[];
  reference_count: number;
  generation_count: number;
  running_count: number;
}

export interface ProjectStorage {
  dir: string;
  total_size_bytes: number;
  total_size_human: string;
  per_project_bytes: Record<string, number>;
}

export interface EncoderOption {
  key: string;
  family: string;
  label: string;
  detail: string;
  installed: boolean;
  installed_path: string | null;
  default: boolean;
  active: string | null;
}

export interface EncoderCatalog {
  encoders: EncoderOption[];
  active: Record<string, string | null>;
  packs: Record<string, string | null>;
  note: string;
}

export interface InstalledModel {
  name: string;
  path: string;
  size_bytes: number;
  bits: number | null;
  created_at: string | null;
  origin: string | null;
  customised: boolean;
}

export interface SystemInfo {
  chip: string;
  architecture: string;
  macos_version: string;
  python_version: string;
  total_ram_gb: number;
  free_disk_gb: number;
  mlx_version: string;
  mflux_version: string;
  mflux_commit: string | null;
  runner_ready: boolean;
  runner_note: string | null;
  /** Where models, downloads, jobs, outputs and the library live. */
  data_root: string;
  /** The volume the data root sits on, e.g. /Volumes/ExternalDrive. */
  data_volume: string;
  /** False when the data root is on the internal disk. */
  data_is_external: boolean;
  models_dir: string;
  outputs_dir: string;
  hf_cache_dir: string;
  models_size_bytes: number;
  hf_cache_size_bytes: number;
  mlx_active_memory_gb: number;
  mlx_peak_memory_gb: number;
  busy: boolean;
  data_dir: string;
}

export interface HealthInfo {
  ok: boolean;
  version: string;
  mock: boolean;
  runner_ready: boolean;
  runner_note: string | null;
  mflux_version: string;
  mflux_commit: string | null;
  mlx_version: string;
  uptime_seconds: number;
  busy: boolean;
}

export interface ValidationReport {
  ok: boolean;
  source: string;
  component: string;
  errors: string[];
  warnings: string[];
  tensor_count?: number;
  total_bytes?: number;
  expected_keys?: number;
  matched_keys?: number;
  coverage?: number;
  stripped_prefixes?: string[];
  quantization?: {
    quantized: boolean;
    bits: number | null;
    group_size: number | null;
    layers: number;
    consistent: boolean;
    oddities: string[];
  };
  missing?: { count: number; examples: string[] };
  unexpected?: { count: number; examples: string[] };
  shape_mismatches?: { count: number; examples: string[] };
  verification?: { ok: boolean; bits?: number | null; parameters?: number; error?: string };
}

import {
  DEMO_ENABLED,
  demoFileUrl,
  demoHealth,
  demoJob,
  demoLibrary,
  demoScript,
  demoSources,
  demoSystem,
  demoValidation,
} from "./demo";

let connection: Connection | null = null;

// Demo mode keeps its own little world: jobs it created, and a mutable library copy.
const demoJobs = new Map<string, Job>();
let demoLibraryState: LibraryItem[] = [];

export function connectionOrNull(): Connection | null {
  return connection;
}

export function setConnection(next: Connection | null): void {
  connection = next;
  if (next) {
    try {
      localStorage.setItem("refract.connection", JSON.stringify(next));
    } catch {
      /* private mode: the app still works, it just will not remember */
    }
  }
}

function rememberedConnection(): Connection | null {
  try {
    const raw = localStorage.getItem("refract.connection");
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Connection;
    return parsed.base && parsed.token ? parsed : null;
  } catch {
    return null;
  }
}

function connectionFromLocation(): Connection | null {
  const url = new URL(window.location.href);
  const api = url.searchParams.get("api");
  const token = url.searchParams.get("token");
  if (api && token) return { base: api.replace(/\/$/, ""), token, mock: url.searchParams.get("mock") === "1" };
  const envApi = import.meta.env.VITE_REFRACT_API as string | undefined;
  const envToken = import.meta.env.VITE_REFRACT_TOKEN as string | undefined;
  if (envApi && envToken) return { base: envApi.replace(/\/$/, ""), token: envToken, mock: false };
  return null;
}

/**
 * Ask the app where the service is.
 *
 * Preferred over the `?api=&token=` a development server puts in the address, because the app is
 * also the one that restarts the service: after a restart the address bar holds a token that no
 * longer works, and this call is the only source that cannot go stale.
 */
async function resolveFromHost(): Promise<Connection | null> {
  if (!isNativeHost()) return null;
  try {
    const info = await hostInvoke<BackendInfo>("backend_info");
    if (!info?.port) return null;
    return { base: `http://127.0.0.1:${info.port}`, token: info.token, mock: info.mock };
  } catch {
    return null;
  }
}

/** Resolve (and cache) how to reach the service; the app first, then the browser fallbacks. */
export async function connect(): Promise<Connection> {
  if (connection) return connection;
  if (DEMO_ENABLED) {
    connection = { base: "demo", token: "demo", mock: true };
    demoLibraryState = [...demoLibrary];
    return connection;
  }
  const resolved = (await resolveFromHost()) ?? connectionFromLocation() ?? rememberedConnection();
  if (!resolved) {
    throw new Error(
      "No Refract Image service is reachable. Start the app with scripts/dev.sh (which starts the backend) " +
        "or open this page with ?api=http://127.0.0.1:PORT&token=TOKEN.",
    );
  }
  setConnection(resolved);
  return resolved;
}

function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const conn = await connect();
  const response = await fetch(`${conn.base}${path}`, {
    ...init,
    headers: {
      "X-Refract-Token": conn.token,
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* keep the status text */
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export const api = {
  avatars: () => DEMO_ENABLED ? Promise.resolve({ avatars: [] as Avatar[] }) : request<{ avatars: Avatar[] }>("/api/avatars"),
  saveAvatar: (body: AvatarDraft, id?: string) => request<Avatar>(id ? `/api/avatars/${id}` : "/api/avatars", {
    method: id ? "PUT" : "POST", body: JSON.stringify(body),
  }),
  deleteAvatar: (id: string) => request<{ deleted: string }>(`/api/avatars/${id}`, { method: "DELETE" }),
  health: () => (DEMO_ENABLED ? Promise.resolve(demoHealth) : request<HealthInfo>("/api/health")),
  system: () => (DEMO_ENABLED ? Promise.resolve(demoSystem) : request<SystemInfo>("/api/system")),
  sources: () =>
    DEMO_ENABLED
      ? Promise.resolve({ sources: demoSources, disk: { models_dir: demoSystem.models_dir } })
      : request<{ sources: SourceEntry[]; disk: Record<string, unknown> }>("/api/sources"),
  projects: () =>
    DEMO_ENABLED
      ? Promise.resolve({ projects: [], storage: { dir: demoSystem.models_dir, total_size_bytes: 0, total_size_human: "0 B", per_project_bytes: {} } })
      : request<{ projects: Project[]; storage: ProjectStorage }>("/api/projects"),
  project: (id: string) =>
    DEMO_ENABLED
      ? Promise.reject(new ApiError("Projects need the local service", 0))
      : request<Project>(`/api/projects/${id}`),
  createProject: (body: { name?: string; session?: Partial<GenerateParams>; prompt?: string }) =>
    DEMO_ENABLED
      ? Promise.reject(new ApiError("Projects need the local service", 0))
      : request<Project>("/api/projects", { method: "POST", body: JSON.stringify(body) }),
  updateProject: (id: string, body: { name?: string; session?: Partial<GenerateParams>; references?: string[]; session_id?: string }) =>
    request<Project>(`/api/projects/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  createProjectSession: (id: string) =>
    request<Project>(`/api/projects/${id}/sessions`, { method: "POST" }),
  deleteProject: (id: string) => request<{ deleted: string }>(`/api/projects/${id}`, { method: "DELETE" }),
  selectModel: (model_source: string, model_path: string | null) =>
    DEMO_ENABLED ? Promise.resolve({ unloaded: false, deferred: false }) :
    request<{ unloaded: boolean; deferred: boolean }>("/api/models/select", {
      method: "POST", body: JSON.stringify({ model_source, model_path }),
    }),
  encoders: () =>
    DEMO_ENABLED
      ? Promise.resolve({ encoders: [], active: {}, packs: {}, note: "" })
      : request<EncoderCatalog>("/api/encoders"),
  validate: (payload: { path: string; component?: string; base_model_path?: string | null }) =>
    DEMO_ENABLED
      ? delay(600).then(() => ({ ...demoValidation, source: payload.path }))
      : request<ValidationReport>("/api/validate", { method: "POST", body: JSON.stringify(payload) }),
  deleteModel: (path: string, confirm = true) =>
    request<{ deleted: boolean; freed_bytes?: number; reason?: string }>("/api/models/delete", {
      method: "POST",
      body: JSON.stringify({ path, confirm }),
    }),

  createJob: (kind: string, payload: Record<string, unknown>) => {
    if (DEMO_ENABLED) {
      const job = demoJob(kind, payload);
      if (kind === "generate") {
        job.seeds = [Number(payload.seed) || 4242];
        demoJobs.set(job.id, job);
      }
      return delay(250).then(() => job);
    }
    return request<Job>("/api/jobs", { method: "POST", body: JSON.stringify({ kind, payload }) });
  },
  job: (id: string) =>
    DEMO_ENABLED ? Promise.resolve(demoJobs.get(id) ?? demoJob("generate", {})) : request<Job>(`/api/jobs/${id}`),
  jobs: (limit = 50) =>
    DEMO_ENABLED
      ? Promise.resolve({ jobs: [...demoJobs.values()].slice(-limit) })
      : request<{ jobs: Job[] }>(`/api/jobs${query({ limit })}`),
  cancel: (id: string) => {
    if (DEMO_ENABLED) {
      const job = demoJobs.get(id);
      if (job) demoJobs.set(id, { ...job, status: "cancelled", phase: "cancelled", message: "cancelled" });
      return Promise.resolve({ cancelled: true, job: demoJobs.get(id) ?? null });
    }
    return request<{ cancelled: boolean; job: Job | null }>(`/api/jobs/${id}/cancel`, { method: "POST" });
  },

  library: (params: { search?: string; favorites?: boolean; model_source?: string; project_id?: string; limit?: number } = {}) => {
    if (DEMO_ENABLED) {
      let items = demoLibraryState;
      if (params.search) {
        const needle = params.search.toLowerCase();
        items = items.filter((item) => item.prompt.toLowerCase().includes(needle));
      }
      if (params.favorites) items = items.filter((item) => item.favorite);
      if (params.model_source) items = items.filter((item) => item.model_source === params.model_source);
      if (params.project_id) items = items.filter((item) => item.project_id === params.project_id);
      return delay(120).then(() => ({ items, count: items.length }));
    }
    return request<{ items: LibraryItem[]; count: number }>(`/api/library${query(params as Record<string, string>)}`);
  },
  favorite: (id: string, favorite: boolean) => {
    if (DEMO_ENABLED) {
      demoLibraryState = demoLibraryState.map((item) => (item.id === id ? { ...item, favorite } : item));
      return Promise.resolve({ id, favorite });
    }
    return request<{ id: string; favorite: boolean }>(`/api/library/${id}/favorite`, {
      method: "POST",
      body: JSON.stringify({ favorite }),
    });
  },
  deleteItem: (id: string, deleteFiles = false) => {
    if (DEMO_ENABLED) {
      demoLibraryState = demoLibraryState.filter((item) => item.id !== id);
      return Promise.resolve({ deleted: deleteFiles ? `${id} and its file` : id });
    }
    return request<{ deleted: string }>(`/api/library/${id}${query({ delete_files: deleteFiles })}`, {
      method: "DELETE",
    });
  },
  replay: (id: string) => {
    if (DEMO_ENABLED) {
      const item = demoLibraryState.find((entry) => entry.id === id);
      const job = demoJob("generate", { ...(item?.params ?? {}), reference_paths: item?.references ?? [] });
      job.references = item?.references ?? [];
      job.seeds = item?.seeds ?? [4242];
      demoJobs.set(job.id, job);
      return delay(200).then(() => job);
    }
    return request<Job>(`/api/library/${id}/replay`, { method: "POST" });
  },
};

export async function fileUrl(path: string): Promise<string> {
  if (DEMO_ENABLED) return demoFileUrl(path) ?? demoFileUrl("demo:result-1")!;
  const conn = await connect();
  return `${conn.base}/api/files${query({ path, token: conn.token })}`;
}

/** Upload arbitrary bytes (drag-and-drop in a browser, where no filesystem path exists). */
export async function uploadFiles(files: File[]): Promise<string[]> {
  const conn = await connect();
  const form = new FormData();
  for (const file of files) form.append("files", file);
  const response = await fetch(`${conn.base}/api/uploads?token=${encodeURIComponent(conn.token)}`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) throw new ApiError(await response.text(), response.status);
  const body = (await response.json()) as { paths: string[] };
  return body.paths;
}

/**
 * Stream a job's events. Falls back to polling when EventSource is unavailable or dies,
 * which happens if the service restarts mid-job.
 */
export function streamJob(
  jobId: string,
  since: number,
  onEvent: (event: JobEvent) => void,
  onDone: () => void,
): () => void {
  let closed = false;
  let cursor = since;
  let source: EventSource | null = null;

  if (DEMO_ENABLED) {
    // Replay the scripted events at a readable pace, updating the stored job as it goes.
    const job = demoJobs.get(jobId);
    if (!job) {
      onDone();
      return () => undefined;
    }
    const events = demoScript(job);
    let index = 0;
    let previous = 0;
    const tick = () => {
      if (closed) return;
      const event = events[index++];
      if (!event) return;
      const wait = Math.max(120, (event.at - previous) * 90);
      previous = event.at;
      window.setTimeout(() => {
        if (closed) return;
        const current = demoJobs.get(jobId);
        if (current && event.step) demoJobs.set(jobId, { ...current, step: event.step, phase: event.phase ?? current.phase });
        if (current && event.preview_path) demoJobs.set(jobId, { ...current, preview_path: event.preview_path });
        onEvent(event);
        if (event.phase === "done") {
          const finished = demoJobs.get(jobId);
          if (finished) {
            const outputs = ["demo:result-1"];
            demoJobs.set(jobId, { ...finished, status: "done", phase: "done", outputs, references: finished.references.length ? finished.references : [] });
            demoLibraryState = [
              {
                ...finished,
                status: "done",
                outputs,
                prompt: String(finished.payload.prompt ?? "demo prompt"),
                negative_prompt: null,
                params: finished.payload as LibraryItem["params"],
                references: finished.references,
                duration_s: finished.elapsed_seconds,
                peak_memory_gb: 13.1,
                model_source: "mlx-q4",
                model_path: null,
                quantize: 4,
                width: finished.width,
                height: finished.height,
                seeds: finished.seeds,
                favorite: false,
                project_id: null,
                error: null,
                created_at: Date.now() / 1000,
                started_at: finished.started_at,
                finished_at: Date.now() / 1000,
                elapsed_seconds: finished.elapsed_seconds,
                seconds_per_step: 0.85,
                eta_seconds: 0,
                downloaded_bytes: 0,
                total_bytes: null,
                kind: "generate",
                payload: finished.payload,
              } as LibraryItem,
              ...demoLibraryState,
            ];
          }
          onDone();
          return;
        }
        tick();
      }, wait);
    };
    tick();
    return () => {
      closed = true;
    };
  }

  connect().then((conn) => {
    if (closed) return;
    try {
      source = new EventSource(`${conn.base}/api/jobs/${jobId}/events${query({ since, token: conn.token })}`);
      source.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as JobEvent;
          cursor = Math.max(cursor, event.seq ?? 0);
          onEvent(event);
          if (event.final && !closed) {
            closed = true;
            source?.close();
            onDone();
          }
        } catch {
          /* ignore malformed frames */
        }
      };
      source.onerror = () => {
        source?.close();
        if (!closed) poll();
      };
    } catch {
      poll();
    }
  });

  const poll = async () => {
    while (!closed) {
      try {
        const job = await api.job(jobId);
        for (const event of job.events ?? []) {
          if ((event.seq ?? 0) > cursor) {
            cursor = event.seq ?? cursor;
            onEvent(event);
          }
        }
        if (["done", "failed", "cancelled"].includes(job.status)) {
          closed = true;
          onDone();
          return;
        }
      } catch {
        /* keep trying while the job is alive */
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  };

  return () => {
    closed = true;
    source?.close();
  };
}
