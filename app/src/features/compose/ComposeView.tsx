import { PromptText } from "../../components/PromptText";
import { useStore } from "../../store/useStore";
import { Button, Disclosure, EmptyState, Kbd, ProgressBar, useFileUrl } from "../../components/ui";
import { baseName } from "../../lib/format";
import { humanDuration, percent, phaseLabel } from "../../lib/format";
import { copyToClipboard, revealInFinder, saveImageAs } from "../../lib/ipc";
import { CompareView } from "../compare/CompareView";
import { AdvancedPanel } from "./AdvancedPanel";
import { GenerateBar } from "./GenerateBar";
import { ReferenceStrip } from "./ReferenceStrip";
import { AvatarPrompt } from "./AvatarPrompt";

export function ComposeView({ dragActive }: { dragActive: boolean }) {
  const params = useStore((state) => state.params);
  const setParams = useStore((state) => state.setParams);
  const advancedOpen = useStore((state) => state.advancedOpen);
  const currentJob = useStore((state) => state.currentJob);
  const sources = useStore((state) => state.sources);
  const toast = useStore((state) => state.toast);
  const refineSource = useStore((state) => state.refineSource);
  const clearRefine = useStore((state) => state.clearRefine);
  const editResult = useStore((state) => state.editResult);
  const refineUrl = useFileUrl(refineSource?.path ?? null);

  const jobs = useStore((state) => state.jobs);
  const job = params.project_id
    ? [...jobs].reverse().find((entry) => entry.kind === "generate" && entry.payload.project_id === params.project_id &&
        (entry.payload.project_session_id ?? "s1") === (params.project_session_id ?? "s1")) ?? null
    : currentJob;
  const previewUrl = useFileUrl(job?.preview_path ?? null);
  const running = Boolean(job && ["queued", "running"].includes(job.status));
  const finishedOutputs = job && job.status === "done" ? job.outputs : [];
  const source = sources.find((entry) => entry.id === params.model_source);

  return (
    <div className="compose-layout">
      <div className="scroll-area compose-form">
        <div className="col" style={{ gap: 20, padding: 16 }}>
          <ReferenceStrip dragActive={dragActive} />

          {refineSource ? (
            <div className="refine-banner">
              {refineUrl ? <img src={refineUrl} alt="" /> : <span className="caption faint">↩</span>}
              <div className="col" style={{ gap: 2, minWidth: 0 }}>
                <span style={{ fontWeight: 600 }}>
                  Refining <span className="mono">{baseName(refineSource.path)}</span>
                </span>
                <span className="caption faint selectable">
                  {refineSource.originalPrompt
                    ? `was: ${refineSource.originalPrompt}`
                    : "describe the change you want in this image"}
                </span>
              </div>
              <div className="grow" />
              <Button variant="subtle" title="Stop refining and keep composing from here" onClick={clearRefine}>
                Not refining
              </Button>
            </div>
          ) : null}

          <AvatarPrompt placeholder={
                refineSource
                  ? "e.g. Make the light warmer and the background softer, keep the pose"
                  : params.reference_paths.length
                  ? "e.g. Change the jacket to dark green. Preserve the person's face and pose."
                  : "e.g. A detailed illustrated botanical poster with the title SPRING"
              } />

          <Disclosure
            title="Advanced"
            subtitle={advancedOpen ? undefined : `${params.steps} steps · ${params.width ?? "auto"}×${params.height ?? "auto"}`}
            open={advancedOpen}
            onToggle={() => useStore.setState({ advancedOpen: !advancedOpen })}
          >
            <AdvancedPanel />
          </Disclosure>

          <div className="divider" />
          <GenerateBar />
        </div>
      </div>

      <div className="compose-canvas">
        {job && running ? (
          <div className="col grow" style={{ minHeight: 0, gap: 10, padding: 16 }}>
            <div className="row" style={{ gap: 8 }}>
              <span className="spinner" />
              <span style={{ fontWeight: 500 }}>{phaseLabel(job.phase)}</span>
              {job.total_steps ? (
                <span className="caption mono muted">
                  {job.step}/{job.total_steps}
                </span>
              ) : null}
              <div className="grow" />
              <span className="caption mono muted">
                {job.seconds_per_step ? `${job.seconds_per_step.toFixed(2)} s/step` : ""}
                {job.eta_seconds ? ` · ${humanDuration(job.eta_seconds)} left` : ""}
              </span>
            </div>
            <ProgressBar value={percent(job.step, job.total_steps)} />
            <div className="compare-canvas grow" style={{ minHeight: 0 }}>
              {previewUrl ? (
                <img src={previewUrl} alt="Live preview" />
              ) : (
                <div className="col center" style={{ gap: 6 }}>
                  <span className="muted">Denoising — the first preview appears shortly</span>
                  <span className="caption faint">Enable “Live previews” in Advanced to decode the latent mid-run</span>
                </div>
              )}
            </div>
            <div className="caption muted truncate">{job.message}</div>
          </div>
        ) : job && finishedOutputs.length ? (
          <>
          <div style={{ padding: "12px 16px 0" }}><PromptText prompt={String(job.payload.prompt ?? "")} /></div>
          <CompareView
            references={job.references}
            outputs={finishedOutputs}
            caption={
              <span>
                {job.width}×{job.height} · {job.total_steps} steps · seed {(job.seeds ?? []).join(", ")} ·{" "}
                {humanDuration(job.elapsed_seconds)}
                {job.peak_memory_gb ? ` · peak ${job.peak_memory_gb.toFixed(1)} GB` : ""}
              </span>
            }
            actions={
              <>
                <Button
                  variant="subtle"
                  title="Copy the image to the clipboard"
                  onClick={async () => {
                    const ok = await copyToClipboard(finishedOutputs[0]);
                    toast(ok ? "Copied" : "Clipboard unavailable", ok ? "success" : "error");
                  }}
                >
                  ⧉ Copy
                </Button>
                <Button variant="subtle" onClick={() => void revealInFinder(finishedOutputs[0])}>
                  ⌸ Reveal
                </Button>
                <Button
                  variant="subtle"
                  onClick={async () => {
                    const saved = await saveImageAs(finishedOutputs[0]);
                    if (saved) toast("Saved", "success");
                  }}
                >
                  ↓ Save As
                </Button><Button
                  variant="subtle"
                  title="Use this image as the thing to edit, and write a new instruction"
                  onClick={() =>
                    void editResult({
                      id: job.id,
                      outputs: finishedOutputs,
                      references: job.references,
                      prompt: String(job.payload.prompt ?? ""),
                      params: job.payload,
                      project_id: typeof job.payload.project_id === "string" ? job.payload.project_id : null,
                    })
                  }
                >
                  ↩ Refine
                </Button>
                <Button variant="subtle" title="Run again with the same settings and a fresh random seed" onClick={() => {
                    setParams({ seed: Math.floor(Math.random() * 2 ** 31) });
                    void useStore.getState().generate();
                  }}
                >
                  ↻ Variation
                </Button>
                <Button variant="subtle" onClick={() => useStore.setState({ view: "library", selectedItemId: job.id })}>
                  ▦ Library
                </Button>
              </>
            }
          />
          </>
        ) : (
          <EmptyState
            glyph="✦"
            title={params.reference_paths.length ? "Ready to edit" : "Start with a prompt"}
            body={
              params.reference_paths.length
                ? "Press Generate to run the model on your references. Results appear here beside the reference images, side by side and as a wipe overlay."
                : "Drop reference images above to edit or combine them, or leave the strip empty for plain text-to-image generation."
            }
            action={
              <div className="col center" style={{ gap: 6 }}>
                <span className="caption muted">
                  <Kbd>⌘</Kbd> <Kbd>⏎</Kbd> generates · <Kbd>esc</Kbd> cancels
                </span>
                {source ? (
                  <span className="caption faint">
                    {source.available
                      ? `Running on ${source.label}`
                      : `${source.label} is not on disk yet — open Models to download it`}
                  </span>
                ) : null}
              </div>
            }
          />
        )}
      </div>
    </div>
  );
}
