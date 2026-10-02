import { useEffect, useRef } from "react";
import { useStore } from "../store/useStore";
import { Button, Kbd, Segmented } from "./ui";
import { humanBytes } from "../lib/format";
import type { View } from "../store/useStore";

export function TopBar() {
  const view = useStore((state) => state.view);
  const setView = useStore((state) => state.setView);
  const search = useStore((state) => state.librarySearch);
  const setSearch = useStore((state) => state.setSearch);
  const system = useStore((state) => state.system);
  const health = useStore((state) => state.health);
  const sources = useStore((state) => state.sources);
  const params = useStore((state) => state.params);
  const setParams = useStore((state) => state.setParams);
  const newSession = useStore((state) => state.newProjectSession);
  const clearReferences = useStore((state) => state.clearReferences);
  const openSettings = useStore.setState;
  const inputRef = useRef<HTMLInputElement>(null);
  const projects = useStore((state) => state.projects);
  const activeProjectId = useStore((state) => state.activeProjectId);
  const openProject = useStore((state) => state.openProject);

  const activeSource = sources.find((source) => source.id === params.model_source);
  const activeProject = projects.find((project) => project.id === activeProjectId);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f") {
        event.preventDefault();
        if (useStore.getState().view !== "library") setView("library");
        window.setTimeout(() => inputRef.current?.focus(), 30);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setView]);

  return (
    <header className="topbar" data-window-drag>
      {/* Space for the native traffic lights; they belong to this bar's left edge. */}
      <div className="traffic-lights" data-window-drag />
      <div className="topbar-title" data-window-drag>
        Refract Image
      </div>

      <Segmented<View>
        value={view}
        onChange={(next) => setView(next)}
        options={[
          { value: "compose", label: "Compose" },
          { value: "library", label: "Library" },
          { value: "projects", label: "Projects" },
          { value: "models", label: "Models" },
        ]}
      />

      {/* Quick switcher: the current project is always named here, so there is never a
          question of which set of references the prompt on screen belongs to. */}
      {projects.length ? (
        <select
          className="select"
          style={{ maxWidth: 190, minWidth: 120 }}
          value={activeProjectId ?? ""}
          onChange={(event) => {
            if (event.target.value) void openProject(event.target.value);
          }}
          title="Switch project (⌘J to cycle)"
        >
          {activeProject ? null : <option value="">No project</option>}
          {projects.map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))}
        </select>
      ) : null}

      {view === "library" ? (
        <input
          ref={inputRef}
          className="input"
          style={{ maxWidth: 260, minWidth: 140 }}
          placeholder="Search prompts…"
          value={search}
          spellCheck={false}
          onChange={(event) => {
            setSearch(event.target.value);
            void useStore.getState().loadLibrary();
          }}
        />
      ) : null}

      <div className="topbar-spacer" data-window-drag />

      <div className="topbar-status">
        {activeSource ? (
          <span className="truncate" style={{ maxWidth: 220 }} title={activeSource.location ?? activeSource.repo_id ?? ""}>
            {activeSource.available ? activeSource.label : `${activeSource.label} · not downloaded`}
          </span>
        ) : null}
        {system ? <span className="disk">{humanBytes(system.models_size_bytes)} on disk</span> : null}
        {health?.mock ? <span className="pill warn">mock runner</span> : null}
      </div>

      <Button
        variant="subtle"
        title="Shortcut cheat sheet (⌘/)"
        onClick={() => useStore.setState({ cheatSheetOpen: true })}
      >
        ⌘/
      </Button>

      <Button
        variant="subtle"
        icon={<span aria-hidden>＋</span>}
        title="New generation (⌘N)"
        onClick={() => {
          if (activeProjectId) void newSession(activeProjectId);
          else {
            setParams({ prompt: "", reference_paths: [], seed: null });
            clearReferences();
            setView("compose");
          }
        }}
      >
        New
      </Button>

      <Button variant="subtle" title="Settings (⌘,)" onClick={() => openSettings({ settingsOpen: true })}>
        ⚙
      </Button>
      <span className="kbd" title="Every primary action has a shortcut">
        <Kbd>⌘</Kbd>
        <Kbd>⏎</Kbd>
      </span>
    </header>
  );
}
