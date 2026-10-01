import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  api,
  ApiError,
  connect,
  connectionOrNull,
  streamJob,
  type HealthInfo,
  type Job,
  type JobEvent,
  type LibraryItem,
  type GenerateParams,
  type Project,
  type ProjectStorage,
  type EncoderOption,
  type SourceEntry,
  type SystemInfo,
  type ValidationReport,
} from "../lib/api";
import { baseName, dirName } from "../lib/format";
import { keepAwake, notifyFinished } from "../lib/ipc";
import { appInfo, hasNativeHost } from "../lib/ipc";

export type View = "compose" | "library" | "projects" | "models";

/** What a refinement is being made from, so Compose can say so while you write the prompt. */
export interface RefineSource {
  /** The Library row or job the result came from. */
  id: string;
  /** The image now being edited — it becomes reference 1. */
  path: string;
  /** The prompt that produced it, shown greyed out for reference. */
  originalPrompt: string;
  /** Where that result was filed, if anywhere. */
  projectId: string | null;
}

/** A finished result, from either the Library or the running job. */
export interface EditableResult {
  id: string;
  outputs: string[];
  references: string[];
  prompt: string;
  params: Partial<GenerateParams>;
  project_id?: string | null;
}

/**
 * Confirmation after a run, naming the folder the image landed in.
 *
 * Demo mode's outputs are `demo:` placeholders rather than paths, so the folder is only
 * named when the result really is an absolute path.
 */
function savedTo(job: Job): string {
  const first = job.outputs?.[0];
  if (!first || !first.startsWith("/")) return "Image ready";
  const folder = baseName(dirName(first));
  return folder ? `Image ready — saved to ${folder}` : "Image ready";
}
export type Tone = "info" | "success" | "error";

export interface Settings {
  modelSource: string;
  outputDir: string | null;
  lowRam: boolean;
  vaeTiling: boolean;
  previewInterval: number;
  saveMetadata: boolean;
  mlxCacheLimitGb: number | null;
  theme: "system" | "light" | "dark";
}

export interface Toast {
  id: string;
  message: string;
  tone: Tone;
}

export const DEFAULT_PARAMS: GenerateParams = {
  prompt: "",
  negative_prompt: null,
  reference_paths: [],
  width: null,
  height: null,
  match_reference_size: true,
  output_resolution: 1024,
  steps: 40,
  guidance: 1,
  seed: null,
  quantize: 4,
  use_kv_cache: true,
  low_ram: false,
  vae_tiling: false,
  mlx_cache_limit_gb: null,
  preview_interval: 5,
  model_source: "mlx-q4",
  model_path: null,
  project_id: null,
  save_metadata: true,
};

/**
 * The parts of the live session a project remembers.
 *
 * `reference_paths` is deliberately absent: the project owns its own copies, and letting
 * a save write them back would have the UI overwrite the server's managed paths with the
 * originals it was trying to escape. They are carried in `references` on the wire instead.
 */
const SESSION_FIELDS = [
  "prompt",
  "negative_prompt",
  "width",
  "height",
  "match_reference_size",
  "output_resolution",
  "steps",
  "guidance",
  "seed",
  "quantize",
  "use_kv_cache",
  "low_ram",
  "vae_tiling",
  "mlx_cache_limit_gb",
  "preview_interval",
  "model_source",
  "model_path",
  "save_metadata",
  "output_dir",
  "output_name",
] as const satisfies readonly (keyof GenerateParams)[];

export function sessionOf(params: GenerateParams): Partial<GenerateParams> {
  const session: Record<string, unknown> = {};
  for (const key of SESSION_FIELDS) session[key] = params[key];
  return session as Partial<GenerateParams>;
}

/**
 * The settings a refinement inherits from the run that produced the image.
 *
 * Everything that shapes the image, and nothing that describes *this* prompt: the prompt
 * and references are the point of the edit, and the seed is dropped so the refinement is a
 * fresh sample rather than a repeat of the run it is refining.
 */
const REFINE_FIELDS = [
  "steps",
  "guidance",
  "quantize",
  "model_source",
  "model_path",
  "width",
  "height",
  "match_reference_size",
  "output_resolution",
  "use_kv_cache",
  "low_ram",
  "vae_tiling",
  "mlx_cache_limit_gb",
  "preview_interval",
  "save_metadata",
] as const;

function pickRefineSettings(params: Partial<GenerateParams>): Partial<GenerateParams> {
  const picked: Record<string, unknown> = {};
  for (const key of REFINE_FIELDS) {
    if (params[key] !== undefined) picked[key] = params[key];
  }
  return picked as Partial<GenerateParams>;
}

/** Matches MAX_REFERENCES on the request schema. */
const MAX_REFERENCES = 10;

const DEFAULT_SETTINGS: Settings = {
  modelSource: "mlx-q4",
  outputDir: null,
  lowRam: false,
  vaeTiling: false,
  previewInterval: 5,
  saveMetadata: true,
  mlxCacheLimitGb: null,
  theme: "system",
};

interface Store {
  ready: boolean;
  bootError: string | null;
  view: View;
  health: HealthInfo | null;
  system: SystemInfo | null;
  sources: SourceEntry[];
  projects: Project[];
  projectStorage: ProjectStorage | null;
  activeProjectId: string | null;
  projectsLoading: boolean;
  encoders: EncoderOption[];
  activeEncoders: Record<string, string | null>;
  jobs: Job[];
  currentJob: Job | null;
  library: LibraryItem[];
  librarySearch: string;
  libraryFavoritesOnly: boolean;
  /** When set, the Library shows only this project's results. */
  libraryProjectFilter: string | null;
  selectedItemId: string | null;
  toasts: Toast[];
  settings: Settings;
  params: GenerateParams;
  advancedOpen: boolean;
  refineSource: RefineSource | null;
  settingsOpen: boolean;
  cheatSheetOpen: boolean;
  onboardingOpen: boolean;
  modelTaskId: string | null;
  firstRunModel: string | null;
  modelTask: Job | null;
  validation: ValidationReport | null;
  validationPath: string | null;

  init: () => Promise<void>;
  refreshAll: () => Promise<void>;
  refreshEncoders: () => Promise<void>;
  refreshProjects: () => Promise<void>;
  createProject: (name?: string) => Promise<string | null>;
  openProject: (id: string) => Promise<void>;
  renameProject: (id: string, name: string) => Promise<void>;
  deleteProject: (id: string) => Promise<void>;
  saveActiveProject: () => Promise<void>;
  setView: (view: View) => void;
  setParams: (patch: Partial<GenerateParams>) => void;
  updateSettings: (patch: Partial<Settings>) => void;
  addReferences: (paths: string[]) => void;
  removeReference: (index: number) => void;
  moveReference: (from: number, to: number) => void;
  clearReferences: () => void;
  generate: () => Promise<void>;
  cancelJob: (id?: string) => Promise<void>;
  trackGeneration: (job: Job) => void;
  submitting: boolean;
  setSearch: (value: string) => void;
  setFavoritesOnly: (value: boolean) => void;
  selectItem: (id: string | null) => void;
  loadLibrary: () => Promise<void>;
  toggleFavorite: (id: string) => Promise<void>;
  deleteItem: (id: string, deleteFiles: boolean) => Promise<void>;
  replay: (id: string) => Promise<void>;
  editResult: (result: EditableResult) => Promise<void>;
  clearRefine: () => void;
  refreshSources: () => Promise<void>;
  downloadSource: (id: string) => Promise<void>;
  installSource: (id: string) => Promise<void>;
  installKlein: (id: string) => Promise<void>;
  prepareSource: (id: string) => Promise<void>;
  validatePath: (path: string, base?: string | null) => Promise<void>;
  installCustom: (payload: { path: string; base_model_path: string; name: string; quantize: number | null }) => Promise<void>;
  deleteModel: (path: string) => Promise<void>;
  setModelTask: (job: Job | null) => void;
  toast: (message: string, tone?: Tone) => void;
  dismissToast: (id: string) => void;
}

let initializing: Promise<void> | null = null;
const generationStreams = new Set<string>();
const isPending = (job: Job) => ["queued", "running"].includes(job.status);

/**
 * Autosave into the active project.
 *
 * Typing a prompt changes the session on every keystroke, so the save is debounced and the
 * last one always lands. Restoring a project deliberately goes through the raw setter
 * rather than `setParams`, so opening a project never looks like an edit and never
 * schedules a save of the values it just read back.
 */
const AUTOSAVE_DELAY_MS = 900;
let autosaveTimer: number | null = null;

function cancelAutosave() {
  if (autosaveTimer !== null) {
    window.clearTimeout(autosaveTimer);
    autosaveTimer = null;
  }
}

/** Mark the session clean: any edit after this schedules a fresh save. */
function markClean() {
  cancelAutosave();
}

function markDirty() {
  cancelAutosave();
  autosaveTimer = window.setTimeout(() => {
    autosaveTimer = null;
    void useStore.getState().saveActiveProject();
  }, AUTOSAVE_DELAY_MS);
}

/**
 * The model the native first-run setup recorded, read over the bridge.
 *
 * Null in a browser session and on a machine set up from a terminal, which is the same answer
 * as "the user was never asked": onboarding then falls back to offering both models itself.
 */
async function firstRunModel(): Promise<string | null> {
  if (!hasNativeHost()) return null;
  try {
    const info = await appInfo();
    return typeof info?.first_run_model === "string" ? info.first_run_model : null;
  } catch {
    return null;
  }
}

export const useStore = create<Store>()(
  persist(
    (set, get) => ({
      ready: false,
      bootError: null,
      view: "compose",
      health: null,
      system: null,
      sources: [],
      projects: [],
      projectStorage: null,
      activeProjectId: null,
      projectsLoading: false,
      encoders: [],
      activeEncoders: {},
      jobs: [],
      currentJob: null,
      submitting: false,
      library: [],
      librarySearch: "",
      libraryFavoritesOnly: false,
      libraryProjectFilter: null,
      selectedItemId: null,
      toasts: [],
      settings: DEFAULT_SETTINGS,
      params: DEFAULT_PARAMS,
      advancedOpen: false,
      refineSource: null,
      settingsOpen: false,
      cheatSheetOpen: false,
      onboardingOpen: false,
      modelTaskId: null,
      firstRunModel: null,
      modelTask: null,
      validation: null,
      validationPath: null,

      toast: (message, tone = "info") => {
        const id = Math.random().toString(36).slice(2);
        set((state) => ({ toasts: [...state.toasts, { id, message, tone }] }));
        // Optimistic UI: short confirmations that clear themselves.
        window.setTimeout(() => get().dismissToast(id), tone === "error" ? 5000 : 2400);
      },

      dismissToast: (id) => set((state) => ({ toasts: state.toasts.filter((toast) => toast.id !== id) })),

      init: () => {
        if (initializing) return initializing;
        initializing = (async () => {
        try {
          await connect();
        } catch (error) {
          set({ bootError: (error as Error).message, ready: false });
          return;
        }
        set({ bootError: null });
        await get().refreshAll();
        for (const job of get().jobs.filter((job) => job.kind === "generate" && isPending(job))) {
          get().trackGeneration(job);
        }
        const { sources, health, system } = get();
        const usable = sources.find((source) => source.id === "mlx-q4");
        const anythingReady = sources.some((source) => source.available);
        // The model the native setup window recorded, if there is one. It decides both which
        // source the app opens on and which download the onboarding offers first.
        const chosen = await firstRunModel();
        set({
          ready: true,
          firstRunModel: chosen,
          onboardingOpen: !anythingReady,
          view: anythingReady ? "compose" : "models",
          params: {
            ...get().params,
            // The chosen model wins over the stored default, but only on a machine that was
            // actually asked — otherwise a terminal install's default is left alone.
            model_source: chosen ?? get().settings.modelSource,
            low_ram: get().settings.lowRam || (system ? system.total_ram_gb > 0 && system.total_ram_gb < 32 : false),
            vae_tiling: get().settings.vaeTiling,
            preview_interval: get().settings.previewInterval,
            save_metadata: get().settings.saveMetadata,
            mlx_cache_limit_gb: get().settings.mlxCacheLimitGb,
            output_dir: get().settings.outputDir,
          },
        });
        // Reopen the project this session was last in, so "come back to it at any time" survives
        // a quit and relaunch rather than only a switch within one run of the app. The list
        // is awaited rather than left in flight by refreshAll: deciding against it needs it
        // to be loaded, or a perfectly valid project id reads as deleted.
        await get().refreshProjects();
        const { activeProjectId, projects } = get();
        if (activeProjectId && projects.some((project) => project.id === activeProjectId)) {
          await get().openProject(activeProjectId);
        } else if (activeProjectId) {
          // It was deleted while the app was closed.
          set({ activeProjectId: null, params: { ...get().params, project_id: null } });
        }
        if (health?.mock) {
          get().toast("Mock runner is active — no MLX model is loaded on this machine.", "info");
        } else if (usable && !usable.available) {
          get().toast("The 4-bit model pack is not downloaded yet. Open Models to fetch it.", "info");
        }
        })().finally(() => { initializing = null; });
        return initializing;
      },

      refreshAll: async () => {
        const results = await Promise.allSettled([api.health(), api.system(), api.sources(), api.library(), api.jobs(30)]);
        const [health, system, sources, library, jobs] = results;
        set({
          health: health.status === "fulfilled" ? health.value : get().health,
          system: system.status === "fulfilled" ? system.value : get().system,
          sources: sources.status === "fulfilled" ? sources.value.sources : get().sources,
          library: library.status === "fulfilled" ? library.value.items : get().library,
          jobs: jobs.status === "fulfilled" ? jobs.value.jobs : get().jobs,
        });
        void get().refreshEncoders();
        void get().refreshProjects();
      },

      refreshEncoders: async () => {
        const catalog = await api.encoders().catch(() => null);
        if (!catalog) return;
        set({ encoders: catalog.encoders, activeEncoders: catalog.active ?? {} });
      },

      installSource: async (id: string) => {
        // Which job installs a source is its own property, not a guess from the id: FLUX.2 is
        // assembled from several pieces and has to be prepared rather than downloaded.
        const source = get().sources.find((entry) => entry.id === id);
        if (!source) {
          get().toast("That model is not offered any more.", "error");
          return;
        }
        if (source.install_job === "prepare-klein") {
          await get().installKlein(id);
          return;
        }
        await get().downloadSource(id);
        // The download only fetches files; the staged pack is what mflux can load, so the
        // prepare step is not optional for a source that needs one.
        if (source.id === "mlx-q4") await get().prepareSource(id);
      },

      installKlein: async (id: string) => {
        try {
          const job = await api.createJob("prepare-klein", { encoder: "ablated" });
          get().setModelTask({ ...job, payload: { ...job.payload, source_id: id } } as Job);
          void keepAwake(true);
          streamJob(job.id, 0, (event) => {
            const current = get().modelTask;
            if (current) set({ modelTask: applyEvent(current, event) });
          }, async () => {
            void keepAwake(get().jobs.some(isPending));
            const finished = await api.job(job.id);
            get().setModelTask(finished);
            await get().refreshSources();
            const ok = finished.status === "done";
            const message = ok ? "FLUX.2 Klein 4B is ready" : finished.error ?? "The download did not finish";
            get().toast(message, ok ? "success" : "error");
            void notifyFinished(ok ? "Model ready" : "Download failed", message);
          });
        } catch (error) {
          get().toast(error instanceof ApiError ? error.message : (error as Error).message, "error");
        }
      },

      refreshProjects: async () => {
        set({ projectsLoading: true });
        try {
          const catalog = await api.projects();
          set({ projects: catalog.projects, projectStorage: catalog.storage, projectsLoading: false });
        } catch {
          set({ projectsLoading: false });
        }
      },

      /**
       * Open a project: its stored session replaces the live one wholesale.
       *
       * The switch is a snapshot of what is on screen right now, saved first, so leaving a
       * project never loses work typed since the last autosave.
       */
      openProject: async (id) => {
        const current = get().activeProjectId;
        if (current && current !== id) await get().saveActiveProject();
        try {
          const project = await api.project(id);
          set((state) => ({
            activeProjectId: project.id,
            params: {
              ...state.params,
              ...project.session,
              reference_paths: project.references,
              project_id: project.id,
            },
          }));
          markClean();
          if (project.missing_references.length) {
            get().toast(
              `${project.missing_references.length} reference image(s) in this project are missing`,
              "error",
            );
          }
          if (get().view === "library") void get().loadLibrary();
        } catch (error) {
          get().toast(error instanceof ApiError ? error.message : (error as Error).message, "error");
        }
      },

      createProject: async (name) => {
        // Seed from what is on screen: "new project" almost always means "keep working on
        // this, separately", not "throw the current composition away".
        const seed = sessionOf(get().params);
        try {
          const project = await api.createProject({
            name,
            session: seed,
            prompt: get().params.prompt,
          });
          await get().refreshProjects();
          set((state) => ({ activeProjectId: project.id, params: { ...state.params, project_id: project.id } }));
          markClean();
          get().toast(`Project “${project.name}” created`, "success");
          return project.id;
        } catch (error) {
          get().toast(error instanceof ApiError ? error.message : (error as Error).message, "error");
          return null;
        }
      },

      renameProject: async (id, name) => {
        const trimmed = name.trim();
        if (!trimmed) return;
        try {
          const project = await api.updateProject(id, { name: trimmed });
          set((state) => ({ projects: state.projects.map((entry) => (entry.id === id ? project : entry)) }));
        } catch (error) {
          get().toast((error as Error).message, "error");
        }
      },

      deleteProject: async (id) => {
        const active = get().activeProjectId === id;
        try {
          await api.deleteProject(id);
          await get().refreshProjects();
          if (active) {
            // Reopen the most recent survivor so compose is never left pointing at a
            // project that no longer exists.
            const next = get().projects[0]?.id ?? null;
            if (next) await get().openProject(next);
            else set({ activeProjectId: null });
          }
          get().toast("Project deleted", "info");
        } catch (error) {
          get().toast((error as Error).message, "error");
        }
      },

      saveActiveProject: async () => {
        const id = get().activeProjectId;
        if (!id) return;
        try {
          const project = await api.updateProject(id, {
            session: sessionOf(get().params),
            references: get().params.reference_paths,
          });
          set((state) => ({ projects: state.projects.map((entry) => (entry.id === id ? project : entry)) }));
        } catch (error) {
          get().toast(`Could not save this project: ${(error as Error).message}`, "error");
        }
      },

      setView: (view) => set({ view }),
      setParams: (patch) => {
        set((state) => ({ params: {
          ...state.params, ...patch,
          ...(patch.width !== undefined || patch.height !== undefined
            ? { match_reference_size: patch.width == null && patch.height == null }
            : {}),
        } }));
        markDirty();
      },
      updateSettings: (patch) => {
        set((state) => {
          const settings = { ...state.settings, ...patch };
          const params: GenerateParams = { ...state.params };
          if (patch.modelSource !== undefined) params.model_source = patch.modelSource;
          if (patch.lowRam !== undefined) params.low_ram = patch.lowRam;
          if (patch.vaeTiling !== undefined) params.vae_tiling = patch.vaeTiling;
          if (patch.previewInterval !== undefined) params.preview_interval = patch.previewInterval;
          if (patch.saveMetadata !== undefined) params.save_metadata = patch.saveMetadata;
          if (patch.mlxCacheLimitGb !== undefined) params.mlx_cache_limit_gb = patch.mlxCacheLimitGb;
          if (patch.outputDir !== undefined) params.output_dir = patch.outputDir;
          return { settings, params };
        });
        markDirty();
      },

      addReferences: (paths) =>
        set((state) => {
          const existing = state.params.reference_paths;
          const merged = [...existing];
          for (const path of paths) {
            if (merged.length >= 10) break;
            if (!merged.includes(path)) merged.push(path);
          }
          if (merged.length === existing.length) return state;
          markDirty();
          return { params: { ...state.params, reference_paths: merged } };
        }),

      removeReference: (index) => {
        set((state) => ({
          params: { ...state.params, reference_paths: state.params.reference_paths.filter((_, i) => i !== index) },
        }));
        markDirty();
      },

      moveReference: (from, to) =>
        set((state) => {
          const next = [...state.params.reference_paths];
          if (from < 0 || to < 0 || from >= next.length || to >= next.length) return state;
          const [moved] = next.splice(from, 1);
          next.splice(to, 0, moved);
          markDirty();
          return { params: { ...state.params, reference_paths: next } };
        }),

      clearReferences: () => {
        set((state) => ({ params: { ...state.params, reference_paths: [] } }));
        markDirty();
      },

      generate: async () => {
        if (get().submitting) return;
        const params = { ...get().params, reference_paths: [...get().params.reference_paths] };
        if (!params.prompt.trim()) {
          get().toast("Write a prompt first.", "error");
          return;
        }
        // Flush first: reference copies are made server-side on save, so a reference added
        // a moment ago would not yet belong to the project this run is tagged with.
        cancelAutosave();
        await get().saveActiveProject();
        if (params.guidance <= 1) params.negative_prompt = null;
        set({ submitting: true });
        try {
          const job = await api.createJob("generate", params as unknown as Record<string, unknown>);
          get().trackGeneration(job);
          set({ view: "compose" });
          get().toast(job.status === "queued" ? "Task added to queue" : "Generation started", "info");
        } catch (error) {
          const message = error instanceof ApiError ? error.message : (error as Error).message;
          get().toast(message, "error");
        } finally {
          set({ submitting: false });
        }
      },

      trackGeneration: (job) => {
        if (generationStreams.has(job.id)) return;
        const update = (next: Job) => set((state) => {
          const jobs = [...state.jobs.filter((entry) => entry.id !== next.id), next]
            .sort((a, b) => a.created_at - b.created_at);
          const pending = jobs.filter((entry) => entry.kind === "generate" && isPending(entry));
          const active = pending.find((entry) => entry.status === "running") ?? pending[0];
          return { jobs, currentJob: active ?? next };
        });
        update(job);
        void keepAwake(true);
        generationStreams.add(job.id);
        streamJob(job.id, job.seq ?? 0, (event) => {
          const current = get().jobs.find((entry) => entry.id === job.id);
          if (current) update(applyEvent(current, event));
        }, () => {
          void (async () => {
            try {
              const finished = await api.job(job.id);
              update(finished);
              const message = finished.status === "done" ? savedTo(finished)
                : finished.status === "failed" ? finished.error ?? "Generation failed" : "Generation cancelled";
              get().toast(message, finished.status === "done" ? "success" : finished.status === "failed" ? "error" : "info");
              await get().loadLibrary();
              if (finished.status === "done") set({ selectedItemId: finished.id });
              if (finished.status !== "cancelled") void notifyFinished("Generation " + finished.status, message);
            } catch (error) {
              get().toast((error as Error).message, "error");
            } finally {
              generationStreams.delete(job.id);
              void keepAwake(get().jobs.some(isPending));
            }
          })();
        });
      },

      cancelJob: async (id) => {
        const job = id ? get().jobs.find((entry) => entry.id === id) : get().currentJob;
        if (!job || !isPending(job)) return;
        try {
          await api.cancel(job.id);
          get().toast(job.status === "queued" ? "Removing queued task…" : "Cancelling…", "info");
        } catch (error) {
          get().toast((error as Error).message, "error");
        }
      },

      setSearch: (value) => set({ librarySearch: value }),
      setFavoritesOnly: (value) => set({ libraryFavoritesOnly: value }),
      selectItem: (id) => set({ selectedItemId: id }),

      loadLibrary: async () => {
        const { librarySearch, libraryFavoritesOnly, libraryProjectFilter } = get();
        const result = await api.library({
          search: librarySearch || undefined,
          favorites: libraryFavoritesOnly || undefined,
          project_id: libraryProjectFilter || undefined,
        });
        set({ library: result.items });
      },

      toggleFavorite: async (id) => {
        const item = get().library.find((entry) => entry.id === id);
        if (!item) return;
        set((state) => ({
          library: state.library.map((entry) => (entry.id === id ? { ...entry, favorite: !entry.favorite } : entry)),
        }));
        try {
          await api.favorite(id, !item.favorite);
        } catch {
          set((state) => ({
            library: state.library.map((entry) => (entry.id === id ? { ...entry, favorite: item.favorite } : entry)),
          }));
          get().toast("Could not update the favourite", "error");
        }
      },

      deleteItem: async (id, deleteFiles) => {          const snapshot = get().library;
        const selectedItemId = get().selectedItemId;
        set((state) => ({
          library: state.library.filter((entry) => entry.id !== id),
          selectedItemId: state.selectedItemId === id ? null : state.selectedItemId,
        }));
        try {
          await api.deleteItem(id, deleteFiles);
          if (get().currentJob?.id === id) set({ currentJob: null });
          get().toast(deleteFiles ? "Deleted with its image" : "Removed from the library", "success");
        } catch (error) {
          set({ library: snapshot, selectedItemId });
          get().toast(error instanceof ApiError ? error.message : "Could not delete that entry", "error");
        }
      },

      replay: async (id) => {
        try {
          const job = await api.replay(id);
          get().trackGeneration(job);
          set({ view: "compose" });
          get().toast("Replay added with the recorded settings", "info");
        } catch (error) {
          get().toast(error instanceof ApiError ? error.message : "Could not replay that entry", "error");
        }
      },

      /**
       * Refine a result: load it into Compose as the thing to edit.
       *
       * The result becomes reference 1 and the references it was made from follow it, so a
       * long refinement chain keeps the original scene rather than drifting away from it.
       * The prompt is deliberately cleared rather than appended to — the result already
       * encodes everything the old prompt asked for, so restating it only competes with
       * the new instruction for the model's attention.
       */
      editResult: async (result) => {
        const output = result.outputs[0];
        if (!output) {
          get().toast("That run produced no image to edit", "error");
          return;
        }
        // Refining a result stays in the project that result belongs to, so the refined
        // image is filed next to what it came from rather than wherever you happened to be.
        const target = result.project_id ?? null;
        if (target && target !== get().activeProjectId) {
          await get().openProject(target);
        }
        const merged: string[] = [output];
        for (const path of result.references ?? []) {
          if (merged.length >= MAX_REFERENCES) break;
          if (!merged.includes(path)) merged.push(path);
        }
        // Carry the settings the result was made with: refining is about the image, not
        // about silently changing size, steps or model halfway through.
        set((state) => ({
          refineSource: {
            id: result.id,
            path: output,
            originalPrompt: result.prompt,
            projectId: target,
          },
          view: "compose",
          params: {
            ...state.params,
            ...pickRefineSettings(result.params),
            reference_paths: merged,
            prompt: "",
            seed: null,
          },
        }));
        markClean();
        get().toast("Describe the change you want — press ⌘↵ to generate", "info");
      },

      clearRefine: () => set({ refineSource: null }),

      refreshSources: async () => {
        const result = await api.sources();
        set({ sources: result.sources });
      },

      setModelTask: (job) => set({ modelTask: job, modelTaskId: job?.id ?? null }),

      downloadSource: async (id) => {
        const job = await api.createJob("download", { source_id: id });
        get().setModelTask(job);
        void keepAwake(true);
        streamJob(job.id, 0, (event) => {
          const current = get().modelTask;
          if (current) set({ modelTask: applyEvent(current, event) });
        }, async () => {
          void keepAwake(get().jobs.some(isPending));
          const finished = await api.job(job.id);
          get().setModelTask(finished);
          await get().refreshSources();
          const message = finished.status === "done" ? "Model downloaded" : finished.error ?? "Download failed";
          get().toast(message, finished.status === "done" ? "success" : "error");
          void notifyFinished(finished.status === "done" ? "Download finished" : "Download failed", message);
        });
      },

      prepareSource: async (id) => {
        const job = await api.createJob("prepare", { source_id: id });
        get().setModelTask(job);
        void keepAwake(true);
        streamJob(job.id, 0, (event) => {
          const current = get().modelTask;
          if (current) set({ modelTask: applyEvent(current, event) });
        }, async () => {
          void keepAwake(get().jobs.some(isPending));
          const finished = await api.job(job.id);
          get().setModelTask(finished);
          await get().refreshSources();
          const message = finished.status === "done" ? "Model prepared" : finished.error ?? "Prepare failed";
          get().toast(message, finished.status === "done" ? "success" : "error");
          void notifyFinished(finished.status === "done" ? "Model prepared" : "Prepare failed", message);
        });
      },

      validatePath: async (path, base) => {
        try {
          const report = await api.validate({ path, component: "transformer", base_model_path: base ?? null });
          set({ validation: report, validationPath: path });
        } catch (error) {
          get().toast(error instanceof ApiError ? error.message : "Validation failed", "error");
        }
      },

      installCustom: async (payload) => {
        const job = await api.createJob("install", payload as unknown as Record<string, unknown>);
        get().setModelTask(job);
        void keepAwake(true);
        streamJob(job.id, 0, (event) => {
          const current = get().modelTask;
          if (current) set({ modelTask: applyEvent(current, event) });
        }, async () => {
          void keepAwake(get().jobs.some(isPending));
          const finished = await api.job(job.id);
          get().setModelTask(finished);
          await get().refreshSources();
          const message = finished.status === "done" ? "Custom weights installed" : finished.error ?? "Install failed";
          get().toast(message, finished.status === "done" ? "success" : "error");
          void notifyFinished(finished.status === "done" ? "Weights installed" : "Install failed", message);
        });
      },

      deleteModel: async (path) => {
        try {
          const result = await api.deleteModel(path, true);
          await get().refreshSources();
          get().toast(result.deleted ? "Deleted" : "Nothing to delete", result.deleted ? "success" : "info");
        } catch (error) {
          get().toast(error instanceof ApiError ? error.message : "Delete failed", "error");
        }
      },
    }),
    {
      name: "refract.state",
      version: 1,
      migrate: (persisted) => {
        const state = persisted as { params?: GenerateParams };
        if (state.params) {
          const wasDefault = state.params.width === 1024 && state.params.height === 1024;
          state.params = wasDefault
            ? { ...state.params, width: null, height: null, match_reference_size: true }
            : { ...state.params, match_reference_size: !state.params.width && !state.params.height };
        }
        return state;
      },
      // activeProjectId is persisted so a relaunch lands in the same project. params is not
      // trusted to carry it, though: the project's own copy is the source of truth on boot,
      // and reopening restores whatever was saved rather than a stale local mirror.
      partialize: (state) => ({
        settings: state.settings,
        params: state.params,
        advancedOpen: state.advancedOpen,
        activeProjectId: state.activeProjectId,
      }),
    },
  ),
);

export function applyEvent(job: Job, event: JobEvent): Job {
  return {
    ...job,
    status: event.status ?? (event.phase === "queued" ? "queued" : ["done", "failed", "cancelled"].includes(event.phase ?? "") ? event.phase as Job["status"] : "running"),
    width: event.width ?? job.width,
    height: event.height ?? job.height,
    seq: event.seq ?? job.seq,
    phase: event.phase ?? job.phase,
    message: event.message ?? job.message,
    step: event.step ?? job.step,
    total_steps: event.total_steps ?? job.total_steps,
    seeds: event.seeds ?? job.seeds,
    preview_path: event.preview_path ?? job.preview_path,
    outputs: event.outputs ?? job.outputs,
    elapsed_seconds: event.elapsed_seconds ?? job.elapsed_seconds,
    seconds_per_step: event.seconds_per_step ?? job.seconds_per_step,
    eta_seconds: event.eta_seconds ?? job.eta_seconds,
    peak_memory_gb: event.peak_memory_gb ?? job.peak_memory_gb,
    downloaded_bytes: event.downloaded_bytes ?? job.downloaded_bytes,
    total_bytes: event.total_bytes ?? job.total_bytes,
  };
}

export function connectionSummary(): string {
  const conn = connectionOrNull();
  return conn ? conn.base : "not connected";
}
