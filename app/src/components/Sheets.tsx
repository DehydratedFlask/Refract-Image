import { useEffect, useState } from "react";
import { useStore } from "../store/useStore";
import { Button, Field, Kbd, Pill, Row, Segmented, Sheet, Switch } from "./ui";
import { humanBytes } from "../lib/format";
import { appInfo, hasNativeHost, openPath, pickDirectory, restartBackend, revealInFinder } from "../lib/ipc";

export function SettingsSheet() {
  const settings = useStore((state) => state.settings);
  const update = useStore((state) => state.updateSettings);
  const params = useStore((state) => state.params);
  const setParams = useStore((state) => state.setParams);
  const sources = useStore((state) => state.sources);
  const system = useStore((state) => state.system);
  const health = useStore((state) => state.health);
  const toast = useStore((state) => state.toast);
  const [runtime, setRuntime] = useState<{ runtime: string; version: string; mock: string; data_root: string } | null>(
    null,
  );

  // The interpreter the app actually spawns is the piece most likely to be misconfigured
  // (a GUI app inherits no PATH), so it is reported rather than left to guesswork.
  useEffect(() => {
    if (!hasNativeHost()) return;
    let live = true;
    void appInfo().then((info) => {
      if (live) setRuntime(info);
    });
    return () => {
      live = false;
    };
  }, []);

  return (
    <Sheet title="Settings" onClose={() => useStore.setState({ settingsOpen: false })} width={620}>
      <div className="col" style={{ gap: 20 }}>
        <section className="col" style={{ gap: 10 }}>
          <div className="field-label">Appearance</div>
          <Segmented
            value={settings.theme}
            onChange={(theme) => update({ theme })}
            options={[
              { value: "system", label: "Match system" },
              { value: "light", label: "Light" },
              { value: "dark", label: "Dark" },
            ]}
          />
          <div className="caption faint">
            Light and dark are separate palettes rather than an inversion, per the macOS design references.
          </div>
        </section>

        <div className="divider" />

        <section className="col" style={{ gap: 10 }}>
          <div className="field-label">Defaults for new runs</div>
          <Field label="Model source">
            <select className="select" value={settings.modelSource} onChange={(event) => update({ modelSource: event.target.value })}>
              {sources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.label}
                  {source.available ? "" : " (not on disk)"}
                </option>
              ))}
            </select>
          </Field>
          <div className="row" style={{ gap: 12 }}>
            <Field label="Steps">
              <input
                className="input"
                type="number"
                min={1}
                max={100}
                value={params.steps}
                onChange={(event) => setParams({ steps: Number(event.target.value) })}
              />
            </Field>
            <Field label="Preview every N steps" hint="0 disables live previews">
              <input
                className="input"
                type="number"
                min={0}
                max={40}
                value={settings.previewInterval}
                onChange={(event) => update({ previewInterval: Number(event.target.value) })}
              />
            </Field>
            <Field label="MLX cache cap (GB)" hint="empty = uncapped">
              <input
                className="input"
                type="number"
                min={0}
                step={0.5}
                placeholder="none"
                value={settings.mlxCacheLimitGb ?? ""}
                onChange={(event) => update({ mlxCacheLimitGb: event.target.value === "" ? null : Number(event.target.value) })}
              />
            </Field>
          </div>
          <Field
            label="Output folder"
            hint="Empty means the app's own outputs folder. Type a path or pick one."
          >
            <div className="row" style={{ gap: 6 }}>
              <input
                className="input mono"
                value={settings.outputDir ?? ""}
                placeholder={system?.outputs_dir ?? "the app's outputs folder"}
                spellCheck={false}
                aria-label="Output folder"
                onChange={(event) => update({ outputDir: event.target.value || null })}
              />
              {settings.outputDir ? (
                <Button variant="subtle" onClick={() => update({ outputDir: null })} title="Use the app's own outputs folder">
                  Default
                </Button>
              ) : null}
              <Button
                variant="subtle"
                disabled={!hasNativeHost()}
                onClick={async () => {
                  const picked = await pickDirectory("Where should generated images go?");
                  if (picked) update({ outputDir: picked });
                }}
              >
                Choose…
              </Button>
            </div>
          </Field>
        </section>

        <div className="divider" />

        <section className="col" style={{ gap: 12 }}>
          <div className="field-label">Memory</div>
          <div className="caption muted">The model stays loaded across sessions until you generate with another model or close the app.</div>
          <Row label="Tile the VAE decode" hint="Lower peak memory at large output sizes">
            <Switch on={settings.vaeTiling} onChange={(next) => update({ vaeTiling: next })} label="VAE tiling" />
          </Row>
          <Row label="Write metadata sidecars" hint="Every setting is recorded next to the PNG">
            <Switch on={settings.saveMetadata} onChange={(next) => update({ saveMetadata: next })} label="Metadata" />
          </Row>
        </section>

        <div className="divider" />

        <section className="col" style={{ gap: 10 }}>
          <div className="field-label">Runtime</div>
          <div className="caption muted selectable">
            mflux {health?.mflux_version ?? "?"}
            {health?.mflux_commit ? ` @ ${health.mflux_commit.slice(0, 8)}` : ""} · MLX {health?.mlx_version ?? "?"} ·{" "}
            {system?.architecture ?? "?"} · Python {system?.python_version ?? "?"}
          </div>
          {runtime?.runtime ? (
            <div className="caption faint selectable">
              Service: <span className="mono">{runtime.runtime}</span>
            </div>
          ) : null}
          <div className="caption muted">
            Model directory: <span className="mono">{system?.models_dir}</span>
          </div>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <span className="caption muted">
              Storage: <span className="mono">{system?.data_root}</span>
            </span>
            {system ? (
              system.data_is_external ? (
                <Pill tone="ok">external drive</Pill>
              ) : (
                <Pill tone="warn">internal disk</Pill>
              )
            ) : null}
          </div>
          <div className="caption faint">
            {humanBytes(system?.models_size_bytes ?? 0)} of prepared models and{" "}
            {humanBytes(system?.hf_cache_size_bytes ?? 0)} of downloads live there, together with the job scratch space
            and every generated image.
          </div>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <Button variant="subtle" onClick={() => void openPath(settings.outputDir ?? system?.outputs_dir ?? "")}>
              Open outputs folder
            </Button>
            <Button variant="subtle" onClick={() => void openPath(system?.models_dir ?? "")}>
              Open models folder
            </Button>
            <Button
              variant="subtle"
              disabled={!hasNativeHost() || !system?.data_root}
              onClick={() => void revealInFinder(system?.data_root ?? "")}
            >
              Reveal storage
            </Button>
            <Button
              variant="subtle"
              disabled={!hasNativeHost()}
              onClick={async () => {
                const ok = await restartBackend();
                toast(ok ? "Restarting the local service…" : "Only available inside the app", ok ? "info" : "error");
              }}
            >
              Restart service
            </Button>
          </div>
          <div className="caption faint">
            Everything runs on this Mac. The only network access is the one-time model download from Hugging Face.
          </div>
        </section>
      </div>
    </Sheet>
  );
}

const SHORTCUTS: { keys: string[]; label: string }[] = [
  { keys: ["⌘", "⏎"], label: "Generate an image" },
  { keys: ["esc"], label: "Cancel the running job" },
  { keys: ["⌘", "N"], label: "New generation" },
  { keys: ["⌘", "F"], label: "Search the library" },
  { keys: ["⌘", "1"], label: "Compose" },
  { keys: ["⌘", "2"], label: "Library" },
  { keys: ["⌘", "3"], label: "Models" },
  { keys: ["⌘", ","], label: "Settings" },
  { keys: ["⌘", "/"], label: "This cheat sheet" },
  { keys: ["⌘", "⇧", "R"], label: "Reveal the output in Finder" },
  { keys: ["⌘", "⇧", "S"], label: "Save the result as…" },
];

export function CheatSheet() {
  return (
    <Sheet title="Keyboard shortcuts" onClose={() => useStore.setState({ cheatSheetOpen: false })} width={520}>
      <div className="col" style={{ gap: 6 }}>
        {SHORTCUTS.map((shortcut) => (
          <div key={shortcut.label} className="row" style={{ justifyContent: "space-between", height: 28 }}>
            <span>{shortcut.label}</span>
            <span className="row" style={{ gap: 3 }}>
              {shortcut.keys.map((key) => (
                <Kbd key={key}>{key}</Kbd>
              ))}
            </span>
          </div>
        ))}
      </div>
    </Sheet>
  );
}

export function OnboardingSheet() {
  const sources = useStore((state) => state.sources);
  const installSource = useStore((state) => state.installSource);
  const firstRunModel = useStore((state) => state.firstRunModel);
  const close = () => useStore.setState({ onboardingOpen: false });
  // Whatever the native setup window recorded comes first; otherwise Qwen-Image, which is
  // the better default of the two for a first run.
  const preferredId = firstRunModel && sources.some((s) => s.id === firstRunModel) ? firstRunModel : "mlx-q4";
  const pack = sources.find((source) => source.id === preferredId);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <Sheet title="Welcome to Refract Image" onClose={close} width={560}>
      <div className="col" style={{ gap: 18 }}>
        <div className="col" style={{ gap: 6, alignItems: "center", textAlign: "center" }}>
          <div style={{ fontSize: 34 }} aria-hidden>
            ✦
          </div>
          <div style={{ fontSize: "var(--text-title-2)", fontWeight: 600 }}>Local image generation, on your Mac</div>
          <div className="muted" style={{ maxWidth: 420 }}>
            Refract Image runs a local image model through mflux on the MLX/Metal stack. Drop in reference images, write what
            should change, and compare the result against what you gave it.
          </div>
        </div>

        <div className="col" style={{ gap: 10 }}>
          <Step
            n={1}
            title="Fetch the model"
            body={
              pack?.install_job === "prepare-klein"
                ? "One download, about 13 GB. FLUX.2 Klein is staged from a base model and a converted text encoder, so this runs a longer preparation step after fetching."
                : "One download, about 8.9 GB. The 4-bit pack keeps both the transformer and the text encoder small enough for a laptop."
            }
          >
            {pack?.available ? (
              <Pill tone="ok">already on disk</Pill>
            ) : (
              <Button
                variant="primary"
                onClick={() => {
                  if (pack) void installSource(pack.id);
                  close();
                  useStore.setState({ view: "models" });
                }}
              >
                Download {pack?.label ?? "the model"}
              </Button>
            )}
          </Step>
          <Step n={2} title="Drop reference images" body="Drag them in from Finder, or press ＋ in the reference strip. The order matters: image 1 is the subject, later images are context." />
          <Step n={3} title="Write the change and generate" body="Press ⌘⏎. Progress, live previews and memory use are shown while it runs; Esc cancels." />
        </div>

        <div className="row" style={{ justifyContent: "space-between" }}>
          <span className="caption faint">Nothing leaves this Mac except the model download.</span>
          <Button onClick={close}>Get started</Button>
        </div>
      </div>
    </Sheet>
  );
}

function Step({ n, title, body, children }: { n: number; title: string; body: string; children?: React.ReactNode }) {
  return (
    <div className="row" style={{ gap: 12, alignItems: "flex-start" }}>
      <div
        className="center"
        style={{
          width: 24,
          height: 24,
          borderRadius: "50%",
          background: "var(--accent-soft)",
          color: "var(--accent)",
          fontWeight: 700,
          flex: "0 0 auto",
        }}
      >
        {n}
      </div>
      <div className="col" style={{ gap: 4 }}>
        <span style={{ fontWeight: 600 }}>{title}</span>
        <span className="caption muted">{body}</span>
        {children}
      </div>
    </div>
  );
}
