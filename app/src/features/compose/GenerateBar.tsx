import { useStore } from "../../store/useStore";
import { generationReferences } from "../../lib/avatars";
import { Button, Kbd, ProgressBar, useFileUrl } from "../../components/ui";
import { humanDuration, percent, phaseLabel } from "../../lib/format";
import { hasNativeHost, pickDirectory } from "../../lib/ipc";

export function GenerateBar() {
  const params = useStore((state) => state.params);
  const avatars = useStore((state) => state.avatars);
  const generate = useStore((state) => state.generate);
  const cancelJob = useStore((state) => state.cancelJob);
  const currentJob = useStore((state) => state.currentJob);
  const jobs = useStore((state) => state.jobs);
  const submitting = useStore((state) => state.submitting);
  const system = useStore((state) => state.system);
  const sources = useStore((state) => state.sources);
  const settings = useStore((state) => state.settings);
  const updateSettings = useStore((state) => state.updateSettings);
  const toast = useStore((state) => state.toast);
  const previewUrl = useFileUrl(currentJob?.preview_path ?? null);

  const running = currentJob && ["queued", "running"].includes(currentJob.status);
  const pending = jobs.filter((job) => job.kind === "generate" && ["queued", "running"].includes(job.status));
  const queued = pending.filter((job) => job.status === "queued");
  const source = sources.find((entry) => entry.id === params.model_source);
  const references = generationReferences(params.prompt, params.reference_paths, avatars).length;
  const mode = references ? `${references} reference${references === 1 ? "" : "s"}` : "text-to-image";

  // Where this run will write. The chosen folder wins; otherwise the service's own
  // outputs directory, which is what the backend falls back to when output_dir is null.
  const outputDir = settings.outputDir ?? system?.outputs_dir ?? null;

  async function chooseOutputDir() {
    const picked = await pickDirectory("Where should generated images go?");
    if (!picked) return;
    updateSettings({ outputDir: picked });
    toast(`Images will be saved to ${picked}`, "success");
  }

  return (
    <div className="col" style={{ gap: 10 }}>
      {running ? (
        <div className="col card" style={{ gap: 8, padding: 12 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="row" style={{ gap: 6 }}>
              <span className="spinner" />
              <span style={{ fontWeight: 500 }}>
                {phaseLabel(currentJob.phase)}
                {currentJob.total_steps ? ` · ${currentJob.step}/${currentJob.total_steps}` : ""}
              </span>
            </span>
            <span className="caption mono muted">
              {currentJob.seconds_per_step ? `${currentJob.seconds_per_step.toFixed(2)} s/step` : "—"}
              {currentJob.eta_seconds ? ` · ${humanDuration(currentJob.eta_seconds)} left` : ""}
            </span>
          </div>

          <ProgressBar value={percent(currentJob.step, currentJob.total_steps)} />

          <div className="row" style={{ gap: 8 }}>
            {previewUrl ? (
              <img
                src={previewUrl}
                alt="Live preview"
                style={{ width: 64, height: 64, objectFit: "cover", borderRadius: 6, border: "0.5px solid var(--border)" }}
              />
            ) : null}
            <div className="col grow" style={{ gap: 2 }}>
              <span className="caption muted truncate">{currentJob.message ?? ""}</span>
              <span className="caption faint mono">
                {currentJob.peak_memory_gb ? `peak ${currentJob.peak_memory_gb.toFixed(1)} GB` : ""}
                {currentJob.elapsed_seconds ? ` · ${humanDuration(currentJob.elapsed_seconds)}` : ""}
              </span>
            </div>
            <Button variant="danger" onClick={() => void cancelJob()} title="Cancel (Esc)">
              Cancel <Kbd>esc</Kbd>
            </Button>
          </div>
        </div>
      ) : null}

      <div className="row" style={{ gap: 8 }}>
        <Button
          variant="primary"
          size="large"
          className="grow"
          disabled={!params.prompt.trim() || submitting || references > 10}
          onClick={generate}
          title="Generate (⌘⏎)"
        >
          {submitting ? "Adding task…" : pending.length ? "Add to queue" : references ? "Generate from references" : "Generate image"}
        </Button>
        <span className="kbd">
          <Kbd>⌘</Kbd>
          <Kbd>⏎</Kbd>
        </span>
      </div>

      {pending.length ? (
        <section className="col card" aria-label="Generation queue" style={{ gap: 8, padding: 12 }}>
          <strong>Queue · {queued.length} waiting</strong>
          <span className="caption faint">Tasks run one at a time. Edit the prompt and add another task.</span>
          {pending.map((job, index) => (
            <div className="row" key={job.id} style={{ gap: 8 }}>
              <span className="caption mono faint">{index + 1}</span>
              <span className="caption selectable grow" title={String(job.payload.prompt)}>{String(job.payload.prompt)}</span>
              <span className="caption muted">{job.status}</span>
              <Button variant="subtle" aria-label={`Cancel task ${index + 1}`} onClick={() => void cancelJob(job.id)}>
                {job.status === "queued" ? "Remove" : "Cancel"}
              </Button>
            </div>
          ))}
        </section>
      ) : null}

      <div className="caption muted row" style={{ gap: 8, flexWrap: "wrap" }}>
        <span>
          {mode} · {references === 1 && params.match_reference_size && !params.width && !params.height ? "original reference size" : `${params.width ?? "auto"}×${params.height ?? "auto"}`} · {params.steps} steps · guidance{" "}
          {params.guidance}
        </span>
        <span className="faint">·</span>
        <span className="truncate">{source?.label ?? params.model_source}</span>
        {source && !source.available ? <span className="pill warn">needs download</span> : null}
        {system && system.total_ram_gb > 0 && params.width && params.height ? (
          <span className="faint">
            · {estimate(params.width, params.height, params.model_source)}
          </span>
        ) : null}
      </div>

      <div className="row" style={{ gap: 6 }}>
        <span className="caption faint" style={{ flex: "0 0 auto" }}>
          Save to
        </span>
        <span
          className="caption mono truncate grow selectable"
          title={outputDir ?? "the app's default outputs folder"}
          style={{ minWidth: 0 }}
        >
          {outputDir ?? "the app's outputs folder"}
        </span>
        {settings.outputDir ? (
          <Button
            variant="subtle"
            title="Go back to the app's own outputs folder"
            onClick={() => {
              updateSettings({ outputDir: null });
              toast("Saved to the app's outputs folder again", "info");
            }}
          >
            Default
          </Button>
        ) : null}
        <Button
          variant="subtle"
          disabled={!hasNativeHost()}
          title={hasNativeHost() ? "Choose where generated images go" : "Available in the desktop app"}
          onClick={() => void chooseOutputDir()}
        >
          Choose…
        </Button>
      </div>
    </div>
  );
}

/**
 * Rough peak-memory expectation, so a too-large size is visible *before* the run rather
 * than as an out-of-memory failure halfway through. It is anchored on a measured run:
 * 512px, quantised transformer and text encoder, ~15.6 GB peak. The per-megapixel term
 * covers activations, which grow with the latent. Treat the result as a floor — the
 * upstream source keeps a bf16 text encoder resident (~17.5 GB).
 */
function estimate(width: number, height: number, sourceId: string): string {
  const megapixels = (width * height) / (1024 * 1024);
  const quantizedEncoder = sourceId === "mlx-q4";
  const base = quantizedEncoder ? 14.5 : 24.0;
  const total = base + megapixels * 4.5;
  return `≈${total.toFixed(0)} GB peak RAM`;
}
