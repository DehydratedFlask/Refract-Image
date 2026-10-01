import { useState } from "react";
import { useStore } from "../../store/useStore";
import { Button, ConfirmButton, EmptyState, Field, Pill, ProgressBar } from "../../components/ui";
import type { ValidationReport } from "../../lib/api";
import { humanBytes, percent, phaseLabel } from "../../lib/format";
import { hasNativeHost, pickDirectory, pickFile, restartBackend, revealInFinder } from "../../lib/ipc";

export function ModelsView() {
  const sources = useStore((state) => state.sources);
  const system = useStore((state) => state.system);
  const health = useStore((state) => state.health);
  const params = useStore((state) => state.params);
  const updateSettings = useStore((state) => state.updateSettings);
  const downloadSource = useStore((state) => state.downloadSource);
  const installSource = useStore((state) => state.installSource);

  const encoders = useStore((state) => state.encoders);
  const activeEncoders = useStore((state) => state.activeEncoders);
  const prepareSource = useStore((state) => state.prepareSource);
  const deleteModel = useStore((state) => state.deleteModel);
  const modelTask = useStore((state) => state.modelTask);
  const validation = useStore((state) => state.validation);
  const validationPath = useStore((state) => state.validationPath);
  const validatePath = useStore((state) => state.validatePath);
  const installCustom = useStore((state) => state.installCustom);
  const toast = useStore((state) => state.toast);

  const [basePath, setBasePath] = useState("");
  const [packPath, setPackPath] = useState("");
  const [installName, setInstallName] = useState("custom-4bit");

  const busy = modelTask && ["queued", "running"].includes(modelTask.status);

  return (
    <div className="scroll-area">
      <div className="col" style={{ gap: 20, padding: 20, maxWidth: 980 }}>
        {/* ---------------------------------------------------------- capability */}
        <section className="col card" style={{ gap: 10, padding: 16 }}>
          <div className="row" style={{ gap: 8 }}>
            <span style={{ fontWeight: 600 }}>Runtime</span>
            <div className="grow" />
            {health?.runner_ready ? <Pill tone="ok">MLX ready</Pill> : <Pill tone="bad">not available</Pill>}
            {health?.mock ? <Pill tone="warn">mock runner</Pill> : null}
          </div>
          <div className="caption muted selectable">
            mflux {health?.mflux_version ?? "?"}
            {health?.mflux_commit ? ` (${health.mflux_commit.slice(0, 8)})` : ""} · MLX {health?.mlx_version ?? "?"} ·{" "}
            {system?.chip ?? "?"} · {system ? `${Math.round(system.total_ram_gb)} GB RAM` : "?"} · macOS{" "}
            {system?.macos_version ?? "?"}
          </div>
          {health?.runner_note ? (
            <div className="caption" style={{ color: "var(--warning)" }}>
              {health.runner_note}
            </div>
          ) : null}
          <div className="row" style={{ gap: 8 }}>
            <Button
              variant="subtle"
              onClick={async () => {
                const ok = await restartBackend();
                toast(ok ? "Restarting the local service…" : "Only available inside the app", ok ? "info" : "error");
              }}
              disabled={!hasNativeHost()}
            >
              Restart service
            </Button>
            <Button
              variant="subtle"
              onClick={async () => {
                await useStore.getState().refreshAll();
                toast("Refreshed", "success");
              }}
            >
              Refresh
            </Button>
          </div>
        </section>

        {/* ---------------------------------------------------------- progress */}
        {modelTask && busy ? (
          <section className="col card" style={{ gap: 8, padding: 16 }}>
            <div className="row" style={{ gap: 8 }}>
              <span className="spinner" />
              <span style={{ fontWeight: 600 }}>
                {phaseLabel(modelTask.phase)} · {modelTask.kind}
              </span>
              <div className="grow" />
              <Button variant="subtle" onClick={() => useStore.getState().setModelTask(null)}>
                Hide
              </Button>
            </div>
            <ProgressBar
              value={
                modelTask.total_bytes
                  ? percent(modelTask.downloaded_bytes, modelTask.total_bytes)
                  : modelTask.total_steps
                    ? percent(modelTask.step, modelTask.total_steps)
                    : 0
              }
            />
            <div className="caption muted">{modelTask.message}</div>
          </section>
        ) : null}

        {/* ---------------------------------------------------------- sources */}
        <section className="col" style={{ gap: 12 }}>
          <div style={{ fontWeight: 600, fontSize: "var(--text-title-3)" }}>Model sources</div>
          {sources.map((source) => (
            <div key={source.id} className="col card" style={{ gap: 10, padding: 16 }}>
              <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                <span style={{ fontWeight: 600 }}>{source.label}</span>
                {source.available ? <Pill tone="ok">ready</Pill> : <Pill tone="warn">not on disk</Pill>}
                {source.quantized_bits ? <Pill tone="accent">{source.quantized_bits}-bit</Pill> : <Pill tone="accent">pre-quantised</Pill>}
                {source.id === params.model_source ? <Pill>in use</Pill> : null}
                <div className="grow" />
                <span className="caption faint mono">
                  {source.size_bytes > 0 ? humanBytes(source.size_bytes) : `≈${humanBytes(source.approx_download_bytes)} download`}
                </span>
              </div>

              <div className="caption muted">{source.detail}</div>
              {source.notes.map((note) => (
                <div key={note} className="caption faint">
                  • {note}
                </div>
              ))}

              <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                <Button variant="primary" disabled={Boolean(busy) || source.id === params.model_source} onClick={() => updateSettings({ modelSource: source.id })}>
                  Use this source
                </Button>

                {source.install_job === "prepare-klein" ? (
                  // FLUX.2 has no repository to download: it is assembled from a base
                  // snapshot and a converted encoder, so it is one button, not two.
                  <Button
                    variant="default"
                    disabled={Boolean(busy) || source.available}
                    onClick={() => void installSource(source.id)}
                    loading={Boolean(busy) && modelTask?.kind === "prepare-klein"}
                    title="Fetch the base model and the ablated text encoder, then stage them into one loadable pack"
                  >
                    {source.available ? "Staged" : "Download and stage"}
                  </Button>
                ) : source.repo_id ? (
                  <>
                    <Button variant="default" disabled={Boolean(busy) || source.available} onClick={() => void downloadSource(source.id)} loading={Boolean(busy) && modelTask?.kind === "download"}>
                      {source.available ? "Downloaded" : "Download"}
                    </Button>
                    <Button
                      variant="default"
                      disabled={Boolean(busy)}
                      onClick={() => void prepareSource(source.id)}
                      title="Write a local model directory so later launches skip the download and the quantising"
                    >
                      Prepare local copy
                    </Button>
                  </>
                ) : null}

                {source.location ? (
                  <ConfirmButton
                    label="Delete from disk"
                    confirmLabel="Really delete?"
                    onConfirm={() => void deleteModel(source.location as string)}
                    title={`Delete ${source.location}`}
                  />
                ) : null}
              </div>

              {source.installed?.length ? (
                <div className="col" style={{ gap: 4 }}>
                  {source.installed.map((model) => (
                    <div key={model.path} className="row caption" style={{ gap: 8 }}>
                      <span className="mono truncate" title={model.path}>
                        {model.name}
                      </span>
                      <span className="faint">{humanBytes(model.size_bytes)}</span>
                      {model.origin ? <span className="faint truncate">{model.origin}</span> : null}
                      <div className="grow" />
                      <Button variant="subtle" onClick={() => updateSettings({ modelSource: source.id })}>
                        Use
                      </Button>
                      <ConfirmButton label="Delete" confirmLabel="Sure?" onConfirm={() => void deleteModel(model.path)} />
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
        </section>

        {/* ---------------------------------------------------------- encoders */}
        <section className="col card" style={{ gap: 12, padding: 16 }}>
          <div className="row" style={{ gap: 8 }}>
            <span style={{ fontWeight: 600, fontSize: "var(--text-title-3)" }}>Text encoders</span>
            <div className="grow" />
            <Pill>per model family</Pill>
          </div>
          <div className="caption muted">
            Encoders are <strong>not</strong> interchangeable between model families. Qwen-Image-2.1 uses a
            vision-language Qwen3-VL-8B whose visual tower is what lets reference editing see the image;
            FLUX.2 Klein uses a text-only Qwen3-4B. Each family can only be offered the encoders that
            actually match its module tree, so the two groups below never mix.
          </div>

          {["qwen21", "flux2"].map((family) => {
            const familyEncoders = encoders.filter((entry) => entry.family === family);
            if (!familyEncoders.length) return null;
            const active = activeEncoders[family] ?? null;
            return (
              <div key={family} className="col" style={{ gap: 6, padding: "8px 0", borderTop: "1px solid var(--border)" }}>
                <div className="row" style={{ gap: 8 }}>
                  <span className="mono">{family === "qwen21" ? "Qwen-Image-2.1" : "FLUX.2 Klein 4B"}</span>
                  <div className="grow" />
                  <span className="caption faint">
                    {active ? familyEncoders.find((e) => e.key === active)?.label ?? active : "no pack installed"}
                  </span>
                </div>
                {familyEncoders.map((entry) => (
                  <div key={entry.key} className="row" style={{ gap: 8, paddingLeft: 10 }}>
                    <span className="truncate caption" title={entry.detail}>
                      {entry.label}
                    </span>
                    <div className="grow" />
                    {entry.installed ? <Pill tone="ok">installed</Pill> : null}
                    {entry.key === active ? <Pill tone="warn">in use</Pill> : null}
                  </div>
                ))}
              </div>
            );
          })}
          <div className="caption faint">
            Encoders are baked into a model directory by the prepare tools, so switching one re-stages
            that pack: <span className="mono">prepare_encoder</span> for Qwen-Image,{" "}
            <span className="mono">prepare_klein</span> for FLUX.2.
          </div>
        </section>

        {/* ---------------------------------------------------------- custom */}
        <section className="col card" style={{ gap: 12, padding: 16 }}>
          <div className="row" style={{ gap: 8 }}>
            <span style={{ fontWeight: 600, fontSize: "var(--text-title-3)" }}>Install custom weights</span>
            <div className="grow" />
            <Pill>validated before use</Pill>
          </div>
          <div className="caption muted">
            Point at a third-party transformer pack (for example a community MLX 4-bit safetensors) and a complete
            base model directory. Every tensor name and shape is compared against the live mflux module tree; if the
            pack passes, it is then loaded back through mflux itself before the app will use it. Anything that fails
            is reported with the exact tensors that did not line up, and no directory is left behind.
          </div>

          <Field label="Base model directory" hint="contributes the text encoder, VAE and processor">
            <div className="row" style={{ gap: 6 }}>
              <input
                className="input mono"
                placeholder={sources.find((s) => s.id === "prepared")?.location ?? "/path/to/prepared-model"}
                value={basePath}
                spellCheck={false}
                onChange={(event) => setBasePath(event.target.value)}
              />
              <Button
                variant="subtle"
                disabled={!hasNativeHost()}
                onClick={async () => {
                  const picked = await pickDirectory("Choose a base model directory");
                  if (picked) setBasePath(picked);
                }}
              >
                Choose…
              </Button>
            </div>
          </Field>

          <Field label="Transformer pack" hint=".safetensors file or a directory of shards">
            <div className="row" style={{ gap: 6 }}>
              <input
                className="input mono"
                placeholder="/path/to/qwen-image-2.1-MLX-4bit.safetensors"
                value={packPath}
                spellCheck={false}
                onChange={(event) => setPackPath(event.target.value)}
              />
              <Button
                variant="subtle"
                disabled={!hasNativeHost()}
                onClick={async () => {
                  const picked = await pickFile("Choose a transformer pack", ["safetensors"]);
                  if (picked) setPackPath(picked);
                }}
              >
                Choose…
              </Button>
            </div>
          </Field>

          <div className="row" style={{ gap: 8, alignItems: "flex-end" }}>
            <Field label="Install as" hint="directory name under Models">
              <input className="input" value={installName} spellCheck={false} onChange={(event) => setInstallName(event.target.value)} />
            </Field>
            <Button variant="default" disabled={!packPath} onClick={() => void validatePath(packPath, basePath || null)}>
              Validate
            </Button>
            <Button
              variant="primary"
              disabled={!packPath || !basePath || Boolean(busy) || !validation?.ok}
              title={validation?.ok ? "Install and verify" : "Validate the pack first"}
              onClick={() => void installCustom({ path: packPath, base_model_path: basePath, name: installName, quantize: 4 })}
            >
              Install and use
            </Button>
          </div>

          {validation ? <ValidationPanel report={validation} path={validationPath} /> : null}
        </section>

        {/* ---------------------------------------------------------- disk */}
        <section className="col card" style={{ gap: 8, padding: 16 }}>
          <div className="row" style={{ gap: 8 }}>
            <span style={{ fontWeight: 600, fontSize: "var(--text-title-3)" }}>Disk</span>
            {system ? (
              system.data_is_external ? (
                <Pill tone="ok">on the external drive</Pill>
              ) : (
                <Pill tone="warn">on the internal disk</Pill>
              )
            ) : null}
          </div>
          <div className="caption muted">
            Storage: <span className="mono">{system?.data_root}</span>
            {system?.data_volume ? ` (${system.data_volume})` : ""}
          </div>
          <div className="caption muted">
            Models: {humanBytes(system?.models_size_bytes ?? 0)} in <span className="mono">{system?.models_dir}</span>
          </div>
          <div className="caption muted">
            Hugging Face cache: {humanBytes(system?.hf_cache_size_bytes ?? 0)} in{" "}
            <span className="mono">{system?.hf_cache_dir}</span>
          </div>
          <div className="caption muted">Free space: {humanBytes((system?.free_disk_gb ?? 0) * 1e9)}</div>
          <div className="row" style={{ gap: 8 }}>
            <Button variant="subtle" onClick={() => void useStore.getState().refreshSources()}>
              Recalculate
            </Button>
            <Button
              variant="subtle"
              disabled={!system?.data_root || !hasNativeHost()}
              onClick={() => void revealInFinder(system?.data_root ?? "")}
            >
              Reveal folder
            </Button>
            {system?.hf_cache_dir ? (
              <ConfirmButton
                label="Clear download cache"
                confirmLabel="Delete the cache?"
                title="Deletes downloaded model files; they can be fetched again"
                onConfirm={() => void deleteModel(system.hf_cache_dir)}
              />
            ) : null}
          </div>
          <div className="caption faint">
            Storage follows the app: when it runs from a checkout on an external drive, weights, downloads and
            generated images are kept beside the code (in <span className="mono">.refract</span>) rather than filling
            the internal disk. Deleting a prepared model directory is always safe — the source can be prepared again,
            and any installed custom pack can be rebuilt from its input files.
          </div>
        </section>

        {!sources.length ? (
          <EmptyState glyph="◈" title="No sources reported" body="The local service is not answering. Restart it above." />
        ) : null}
      </div>
    </div>
  );
}

function ValidationPanel({ report, path }: { report: ValidationReport; path: string | null }) {
  const [showExamples, setShowExamples] = useState(false);
  return (
    <div className="col card" style={{ gap: 8, padding: 12, background: "var(--bg-inset)" }}>
      <div className="row" style={{ gap: 8 }}>
        <Pill tone={report.ok ? "ok" : "bad"}>{report.ok ? "compatible" : "incompatible"}</Pill>
        {report.quantization ? (
          <Pill tone={report.quantization.consistent ? "accent" : "warn"}>
            {report.quantization.quantized
              ? `${report.quantization.bits}-bit · group ${report.quantization.group_size} · ${report.quantization.layers} layers`
              : "unquantised"}
          </Pill>
        ) : null}
        <span className="caption faint truncate mono" title={path ?? ""}>
          {path}
        </span>
        <div className="grow" />
        <Button variant="subtle" onClick={() => setShowExamples(!showExamples)}>
          {showExamples ? "Hide detail" : "Show detail"}
        </Button>
      </div>

      <div className="caption muted">
        {report.matched_keys ?? 0} of {report.expected_keys ?? 0} expected tensors matched
        {report.coverage ? ` (${Math.round(report.coverage * 100)}%)` : ""} · {report.tensor_count ?? 0} tensors ·{" "}
        {humanBytes(report.total_bytes ?? 0)}
        {report.stripped_prefixes?.length ? ` · stripped ${report.stripped_prefixes.join(", ")}` : ""}
      </div>

      {report.errors.map((error) => (
        <div key={error} className="caption" style={{ color: "var(--danger)" }}>
          {error}
        </div>
      ))}
      {report.warnings.map((warning) => (
        <div key={warning} className="caption" style={{ color: "var(--warning)" }}>
          {warning}
        </div>
      ))}

      {report.missing?.count ? (
        <div className="caption">
          Missing {report.missing.count}
          {showExamples ? `: ${report.missing.examples.join(", ")}` : ""}
        </div>
      ) : null}
      {report.unexpected?.count ? (
        <div className="caption">
          Unexpected {report.unexpected.count}
          {showExamples ? `: ${report.unexpected.examples.join(", ")}` : ""}
        </div>
      ) : null}
      {report.shape_mismatches?.count ? (
        <div className="caption">
          Shape mismatches {report.shape_mismatches.count}
          {showExamples ? `: ${report.shape_mismatches.examples.join(" · ")}` : ""}
        </div>
      ) : null}

      {report.verification ? (
        <div className="caption" style={{ color: report.verification.ok ? "var(--success)" : "var(--danger)" }}>
          {report.verification.ok
            ? `mflux loaded the pack successfully (${report.verification.bits ?? "?"}-bit, ${report.verification.parameters?.toLocaleString() ?? "?"} parameters)`
            : `mflux could not load it: ${report.verification.error}`}
        </div>
      ) : null}
    </div>
  );
}
