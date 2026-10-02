import { api } from "./lib/api";
import { useEffect, useState } from "react";
import { useStore } from "./store/useStore";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { CheatSheet, OnboardingSheet, SettingsSheet } from "./components/Sheets";
import { Button, EmptyState, ToastHost } from "./components/ui";
import { ComposeView } from "./features/compose/ComposeView";
import { LibraryView } from "./features/library/LibraryView";
import { ModelsView } from "./features/models/ModelsView";
import { ProjectsView } from "./features/projects/ProjectsView";
import { installWindowDragRegion, onFileDrop, onMenuCommand, revealInFinder, saveImageAs } from "./lib/ipc";

let modelSelectionRequest: Promise<unknown> = Promise.resolve();

/**
 * Move to the next project without leaving Compose — the common case is comparing two
 * pieces of work, not visiting the projects page. With fewer than two projects there is
 * nothing to cycle to, so the projects page is the more useful answer.
 */
function cycleProjects() {
  const { projects, activeProjectId, openProject } = useStore.getState();
  if (projects.length > 1) {
    const index = projects.findIndex((project) => project.id === activeProjectId);
    void openProject(projects[(index + 1) % projects.length].id);
  } else {
    useStore.setState({ view: "projects" });
  }
}

export default function App() {
  const ready = useStore((state) => state.ready);
  const bootError = useStore((state) => state.bootError);
  const view = useStore((state) => state.view);
  const settings = useStore((state) => state.settings);
  const health = useStore((state) => state.health);
  const settingsOpen = useStore((state) => state.settingsOpen);
  const cheatSheetOpen = useStore((state) => state.cheatSheetOpen);
  const onboardingOpen = useStore((state) => state.onboardingOpen);
  const currentJob = useStore((state) => state.currentJob);
  const modelSource = useStore((state) => state.params.model_source);
  const modelPath = useStore((state) => state.params.model_path ?? null);
  const [dragActive, setDragActive] = useState(false);

  // Boot: connect, load state, and pick a sensible first view.
  useEffect(() => {
    void useStore.getState().init();
  }, []);

  // Every model change (including restored project sessions) evicts the previous weights.
  useEffect(() => {
    if (!ready) return;
    modelSelectionRequest = modelSelectionRequest.catch(() => undefined)
      .then(() => api.selectModel(modelSource, modelPath));
    void modelSelectionRequest.catch((error: Error) => {
      useStore.getState().toast(`Could not unload the previous model: ${error.message}`, "error");
    });
  }, [ready, modelSource, modelPath]);

  // Theme: system by default, with an explicit override for either palette.
  useEffect(() => {
    const root = document.documentElement;
    if (settings.theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", settings.theme);
  }, [settings.theme]);

  // Reference images dropped anywhere in the window.
  useEffect(() => {
    return onFileDrop(
      (paths) => {
        useStore.getState().addReferences(paths);
        if (useStore.getState().view !== "projects") useStore.setState({ view: "compose" });
      },
      setDragActive,
    );
  }, []);

  // The top bar is the window's drag handle; the app moves the window when a drag starts in it.
  useEffect(() => installWindowDragRegion(), []);

  // Menu-bar commands from the native menu.
  useEffect(() => {
    return onMenuCommand((command) => {
      const store = useStore.getState();
      switch (command) {
        case "new":
          // A new generation belongs to whatever project is open, so it inherits that
          // project's settings rather than resetting the whole session.
          if (store.activeProjectId) void store.newProjectSession(store.activeProjectId);
          else {
            store.setParams({ prompt: "", reference_paths: [], seed: null });
            useStore.setState({ view: "compose" });
          }
          break;
        case "new-project":
          void store.createProject();
          break;
        case "next-project":
          cycleProjects();
          break;
        case "generate":
          void store.generate();
          break;
        case "cancel":
          void store.cancelJob();
          break;
        case "compose":
          useStore.setState({ view: "compose" });
          break;
        case "library":
          useStore.setState({ view: "library" });
          break;
        case "projects":
          useStore.setState({ view: "projects" });
          break;
        case "models":
          useStore.setState({ view: "models" });
          break;
        case "settings":
          useStore.setState({ settingsOpen: true });
          break;
        case "shortcuts":
          useStore.setState({ cheatSheetOpen: true });
          break;
        case "reveal": {
          const output = useStore.getState().currentJob?.outputs[0];
          if (output) void revealInFinder(output);
          break;
        }
        case "save-as": {
          const output = useStore.getState().currentJob?.outputs[0];
          if (output) void saveImageAs(output);
          break;
        }
        // `quit` is not forwarded: the app's Quit item terminates the process itself, because the
        // promise the shell makes is that quitting always stops the service holding the weights.
        default:
          break;
      }
    });
  }, []);

  // In-window shortcuts that are not owned by a control.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const store = useStore.getState();
      const meta = event.metaKey || event.ctrlKey;
      if (meta && event.key === "Enter") {
        event.preventDefault();
        void store.generate();
        return;
      }
      if (event.key === "Escape") {
        void store.cancelJob();
        return;
      }
      if (meta && event.key.toLowerCase() === "n") {
        event.preventDefault();
        if (store.activeProjectId) void store.newProjectSession(store.activeProjectId);
        else {
          store.setParams({ prompt: "", reference_paths: [], seed: null });
          useStore.setState({ view: "compose" });
        }
        return;
      }
      if (meta && event.key.toLowerCase() === "p") {
        event.preventDefault();
        void store.createProject();
        return;
      }
      if (meta && event.key.toLowerCase() === "j") {
        event.preventDefault();
        cycleProjects();
        return;
      }
      if (meta && event.key === ",") {
        event.preventDefault();
        useStore.setState({ settingsOpen: true });
        return;
      }
      if (meta && event.key === "/") {
        event.preventDefault();
        useStore.setState({ cheatSheetOpen: true });
        return;
      }
      if (meta && ["1", "2", "3", "4"].includes(event.key)) {
        event.preventDefault();
        const target =
          event.key === "1" ? "compose" : event.key === "2" ? "library" : event.key === "3" ? "projects" : "models";
        useStore.setState({ view: target });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!ready && bootError) {
    return (
      <EmptyState
        glyph="⚡︎"
        title="The local service is not answering"
        body={bootError}
        action={
          <div className="col center" style={{ gap: 8 }}>
            <Button variant="primary" onClick={() => void useStore.getState().init()}>
              Try again
            </Button>
            <span className="caption faint">
              In development, start it with <span className="mono">scripts/dev.sh</span>; inside the app it is started
              automatically.
            </span>
          </div>
        }
      />
    );
  }

  if (!ready) {
    return (
      <EmptyState glyph="◈" title="Starting the local service…" body="Loading weights status, machine facts and your library." />
    );
  }

  return (
    <>
      <TopBar />
      {health?.mock ? (
        <div className="banner">
          <span aria-hidden>⚠︎</span>
          <span className="grow">
            The mock runner is active, so results are placeholders. Install the MLX stack
            (<span className="mono">scripts/bootstrap.sh</span>) or switch to a machine with Apple silicon to run the
            real model.
          </span>
          <Button variant="subtle" onClick={() => useStore.setState({ view: "models" })}>
            Models
          </Button>
        </div>
      ) : null}
      <div className="app-body">
        <Sidebar />
        <main className="content">
          {view === "compose" ? <ComposeView dragActive={dragActive} /> : null}
          {view === "library" ? <LibraryView /> : null}
          {view === "projects" ? <ProjectsView dragActive={dragActive} /> : null}
          {view === "models" ? <ModelsView /> : null}
        </main>
      </div>

      {settingsOpen ? <SettingsSheet /> : null}
      {cheatSheetOpen ? <CheatSheet /> : null}
      {onboardingOpen ? <OnboardingSheet /> : null}
      <ToastHost />
      {/* A running job is visible from any view, so its state is announced once per change. */}
      {currentJob && ["queued", "running"].includes(currentJob.status) && view !== "compose" && view !== "projects" ? (
        <div className="toast-host" style={{ left: "auto" }}>
          <Button variant="default" onClick={() => useStore.setState({ view: "compose" })}>
            <span className="spinner" /> {currentJob.step}/{currentJob.total_steps || "…"} — show preview
          </Button>
        </div>
      ) : null}
    </>
  );
}
