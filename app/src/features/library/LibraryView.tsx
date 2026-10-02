import { PromptText } from "../../components/PromptText";
import { useEffect, useState } from "react";
import { useStore } from "../../store/useStore";
import { Button, ConfirmButton, EmptyState, Pill, useFileUrl } from "../../components/ui";
import type { LibraryItem } from "../../lib/api";
import { humanDuration, relativeTime } from "../../lib/format";
import { copyToClipboard, revealInFinder, saveImageAs } from "../../lib/ipc";
import { CompareView } from "../compare/CompareView";

function GridCard({
  item,
  selected,
  onSelect,
  onToggleFavorite,
  onDelete,
}: {
  item: LibraryItem;
  selected: boolean;
  onSelect: () => void;
  onToggleFavorite: () => void;
  onDelete: () => void;
}) {
  const url = useFileUrl(item.outputs[0] ?? null);
  return (
    <div
      className={`grid-card ${selected ? "selected" : ""}`}
      onClick={onSelect}
      title={item.prompt}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => event.target === event.currentTarget && event.key === "Enter" && onSelect()}
    >
      {url ? <img src={url} alt={item.prompt} draggable={false} /> : <div className="center" style={{ height: "100%" }}>no preview</div>}
      <button
        className={`fav ${item.favorite ? "on" : ""}`}
        aria-pressed={item.favorite ?? false}
        title={item.favorite ? "Remove from favourites" : "Add to favourites"}
        onClick={(event) => {
          event.stopPropagation();
          onToggleFavorite();
        }}
        type="button"
      >
        {item.favorite ? "★" : "☆"}
      </button>
      <div className="tile-delete" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
        {!["queued", "running"].includes(item.status) ? (
          <ConfirmButton label="Delete" confirmLabel="Delete files?" onConfirm={onDelete} title="Delete this result and its generated files" />
        ) : null}
      </div>
      <div className="meta truncate selectable" onClick={(event) => event.stopPropagation()}>{item.prompt}</div>
    </div>
  );
}

export function LibraryView() {
  const library = useStore((state) => state.library);
  const selectedItemId = useStore((state) => state.selectedItemId);
  const selectItem = useStore((state) => state.selectItem);
  const favoritesOnly = useStore((state) => state.libraryFavoritesOnly);
  const setFavoritesOnly = useStore((state) => state.setFavoritesOnly);
  const loadLibrary = useStore((state) => state.loadLibrary);
  const toggleFavorite = useStore((state) => state.toggleFavorite);
  const deleteItem = useStore((state) => state.deleteItem);
  const replay = useStore((state) => state.replay);
  const editResult = useStore((state) => state.editResult);
  const toast = useStore((state) => state.toast);
  const sources = useStore((state) => state.sources);
  const system = useStore((state) => state.system);
  const settings = useStore((state) => state.settings);
  const projects = useStore((state) => state.projects);
  const projectFilter = useStore((state) => state.libraryProjectFilter);
  const [filterSource, setFilterSource] = useState<string>("");

  // Where new images go, not necessarily where the service would put them by default.
  const outputsDir = settings.outputDir ?? system?.outputs_dir ?? "the app's outputs folder";

  useEffect(() => {
    void loadLibrary();
  }, [loadLibrary, favoritesOnly]);

  const selected = library.find((item) => item.id === selectedItemId) ?? null;
  const visible = filterSource ? library.filter((item) => item.model_source === filterSource) : library;

  if (!library.length) {
    return (
      <EmptyState
        glyph="▦"
        title={
          projectFilter
            ? "Nothing in this project yet"
            : favoritesOnly
            ? "No favourites yet"
            : "Nothing generated yet"
        }
        body={
          projectFilter
            ? "Results you generate while this project is open are collected here."
            : favoritesOnly
            ? "Star a result in the grid or the comparison view and it will show up here."
            : "Every generation lands here with the settings that produced it, so any result can be replayed exactly."
        }
        action={<Button onClick={() => useStore.setState({ view: "compose" })}>Go to Compose</Button>}
      />
    );
  }

  return (
    <div className="col grow" style={{ minHeight: 0 }}>
      <div className="row" style={{ gap: 8, padding: "12px 16px 0" }}>
        <Pill>{library.length} results</Pill>
        {favoritesOnly ? <Pill tone="accent">favourites</Pill> : null}
        {projectFilter ? <Pill tone="accent">{projects.find((p) => p.id === projectFilter)?.name ?? "project"}</Pill> : null}
        <div className="grow" />
        {projects.length > 1 ? (
          <select
            className="select"
            style={{ maxWidth: 170 }}
            value={projectFilter ?? ""}
            onChange={(event) => {
              useStore.setState({ libraryProjectFilter: event.target.value || null });
              void useStore.getState().loadLibrary();
            }}
          >
            <option value="">All projects</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        ) : null}
        <select className="select" style={{ maxWidth: 200 }} value={filterSource} onChange={(event) => setFilterSource(event.target.value)}>
          <option value="">All model sources</option>
          {sources.map((source) => (
            <option key={source.id} value={source.id}>
              {source.label}
            </option>
          ))}
        </select>
        <Button variant="subtle" onClick={() => setFavoritesOnly(!favoritesOnly)}>
          {favoritesOnly ? "Show all" : "Favourites only"}
        </Button>
        <Button variant="subtle" onClick={() => void loadLibrary()}>
          ↻
        </Button>
      </div>

      <div className="row grow" style={{ minHeight: 0, alignItems: "stretch" }}>
        <div className="scroll-area" style={{ flex: "0 0 58%", minWidth: 320 }}>
          <div className="grid">
            {visible.map((item) => (
              <GridCard
                key={item.id}
                item={item}
                selected={item.id === selectedItemId}
                onSelect={() => selectItem(item.id === selectedItemId ? null : item.id)}
                onToggleFavorite={() => void toggleFavorite(item.id)}
                onDelete={() => void deleteItem(item.id, true)}
              />
            ))}
          </div>
          <div className="caption faint" style={{ padding: "0 16px 16px" }} title={outputsDir}>
            Outputs: <span className="mono">{outputsDir}</span>
            {settings.outputDir ? " (chosen)" : ""}
          </div>
        </div>

        <div className="col grow" style={{ minHeight: 0, borderLeft: "0.5px solid var(--border)" }}>
          {selected ? (
            <>
              <div className="col" style={{ gap: 6, padding: 16 }}>
                <div className="row" style={{ gap: 8 }}>

                  <div className="grow" />
                  <Pill tone={selected.status === "done" ? "ok" : selected.status === "cancelled" ? "warn" : "bad"}>
                    {selected.status}
                  </Pill>
                  <Pill>{selected.model_source}</Pill>
                </div>
                <PromptText prompt={selected.prompt} />
                <div className="caption muted selectable">
                  {selected.width ?? "?"}×{selected.height ?? "?"} · {selected.params.steps ?? "?"} steps · seed{" "}
                  {(selected.seeds ?? []).join(", ") || "—"} · {humanDuration(selected.duration_s)} ·{" "}
                  {relativeTime(selected.created_at)}
                  {selected.peak_memory_gb ? ` · peak ${selected.peak_memory_gb.toFixed(1)} GB` : ""}
                </div>
                {selected.error ? <div className="caption" style={{ color: "var(--danger)" }}>{selected.error}</div> : null}
              </div>

              {selected.outputs.length ? (
                <CompareView
                  references={selected.references}
                  outputs={selected.outputs}
                  caption={<span>{selected.params.guidance && selected.params.guidance > 1 ? `guidance ${selected.params.guidance}` : "guidance off"}</span>}
                  actions={
                    <>
                      <Button
                        variant="subtle"
                        onClick={async () => {
                          const ok = await copyToClipboard(selected.outputs[0]);
                          toast(ok ? "Copied" : "Clipboard unavailable", ok ? "success" : "error");
                        }}
                      >
                        ⧉ Copy
                      </Button>
                      <Button variant="subtle" onClick={() => void revealInFinder(selected.outputs[0])}>
                        ⌸ Reveal
                      </Button>
                      <Button
                        variant="subtle"
                        onClick={async () => {
                          const saved = await saveImageAs(selected.outputs[0]);
                          if (saved) toast("Saved", "success");
                        }}
                      >
                        ↓ Save As
                      </Button>
                      <Button
                        variant="subtle"
                        title="Load this image into Compose as the thing to edit, then write what to change"
                        onClick={() =>
                          void editResult({
                            id: selected.id,
                            outputs: selected.outputs,
                            references: selected.references,
                            prompt: selected.prompt,
                            params: selected.params,
                            project_id: selected.project_id,
                          })
                        }
                      >
                        ↩ Refine
                      </Button>
                      <Button variant="subtle" title="Keep the settings, change nothing else" onClick={() => void replay(selected.id)}>
                        ↻ Replay
                      </Button>
                      <Button variant="subtle" onClick={() => void toggleFavorite(selected.id)}>
                        {selected.favorite ? "★" : "☆"}
                      </Button>
                      <Button variant="subtle" onClick={() => void deleteItem(selected.id, false)}>
                        Remove from library
                      </Button>
                      <ConfirmButton
                        label="Delete image"
                        confirmLabel="Delete the PNG too?"
                        title="Also deletes the file from disk"
                        onConfirm={() => void deleteItem(selected.id, true)}
                      />
                    </>
                  }
                />
              ) : (
                <EmptyState glyph="⚠︎" title="No image for this run" body={selected.error ?? "The job did not finish."} />
              )}
            </>
          ) : (
            <EmptyState
              glyph="▦"
              title="Select a result"
              body="Click any image to compare it with the reference images it was generated from, and to replay it."
            />
          )}
        </div>
      </div>
    </div>
  );
}
