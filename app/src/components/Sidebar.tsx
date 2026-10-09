import { useStore } from "../store/useStore";
import { Pill } from "./ui";
import { humanBytes, humanDuration } from "../lib/format";
import { openPath } from "../lib/ipc";

export function Sidebar() {
  const view = useStore((state) => state.view);
  const setView = useStore((state) => state.setView);
  const library = useStore((state) => state.library);
  const favoritesOnly = useStore((state) => state.libraryFavoritesOnly);
  const setFavoritesOnly = useStore((state) => state.setFavoritesOnly);
  const params = useStore((state) => state.params);
  const sources = useStore((state) => state.sources);
  const system = useStore((state) => state.system);
  const health = useStore((state) => state.health);
  const currentJob = useStore((state) => state.currentJob);
  const loadLibrary = useStore((state) => state.loadLibrary);
  const settings = useStore((state) => state.settings);
  const projects = useStore((state) => state.projects);
  const activeProjectId = useStore((state) => state.activeProjectId);
  const openProject = useStore((state) => state.openProject);
  const createProject = useStore((state) => state.createProject);
  const avatars = useStore((state) => state.avatars);
  const sessions = projects.flatMap((project) => project.sessions.map((session) => ({ project, session })))
    .sort((a, b) => b.session.updated_at - a.session.updated_at);

  // The folder the user picked wins over the service's default, here and in Settings.
  const outputsDir = settings.outputDir ?? system?.outputs_dir ?? "";

  const favourites = library.filter((item) => item.favorite).length;
  const readySources = sources.filter((source) => source.available).length;
  const running = currentJob && ["queued", "running"].includes(currentJob.status);
  const activeProject = projects.find((project) => project.id === activeProjectId);

  return (
    <nav className="sidebar">
      <div className="col" style={{ gap: 2 }}>
        <div className="sidebar-section-label">
          Project
          <button
            className="sidebar-add"
            onClick={() => void createProject()}
            type="button"
            title="New project (⌘P)"
            aria-label="New project"
          >
            ＋
          </button>
        </div>
        {projects.length ? (
          <div className="sidebar-project-list">{projects.map((project) => (
            <button
              key={project.id}
              className={`sidebar-row ${project.id === activeProjectId ? "active" : ""}`}
              onClick={() => {
                useStore.setState({ view: "projects", projectBrowserOpen: false });
                void openProject(project.id);
              }}
              type="button"
              title={project.prompt ? `${project.name} — ${project.prompt}` : project.name}
            >
              <span className="glyph" aria-hidden>
                {project.reference_count ? "▤" : "◦"}
              </span>
              <span className="truncate">{project.name}</span>
              <span className="count">{project.generation_count || ""}</span>
            </button>
          ))}</div>
        ) : (
          <button className="sidebar-row" onClick={() => useStore.setState({ view: "projects", projectBrowserOpen: true })} type="button">
            <span className="glyph" aria-hidden>
              ＋
            </span>
            New project
          </button>
        )}
        {activeProject ? (
          <button
            className={`sidebar-row ${view === "projects" ? "active" : ""}`}
            onClick={() => useStore.setState({ view: "projects", projectBrowserOpen: true })}
            type="button"
            title={activeProject.prompt || activeProject.name}
          >
            <span className="glyph" aria-hidden>
              ❏
            </span>
            All projects
            <span className="count">{projects.length}</span>
          </button>
        ) : null}
      </div>

      <div className="col" style={{ gap: 2 }}>
        <div className="sidebar-section-label">Compose</div>
        <button
          className={`sidebar-row ${view === "compose" ? "active" : ""}`}
          onClick={async () => {
            if (activeProjectId) await useStore.getState().newProjectSession(activeProjectId);
            else useStore.getState().setParams({ prompt: "", reference_paths: [], seed: null });
            setView("compose");
          }}
          type="button"
        >
          <span className="glyph" aria-hidden>
            ✦
          </span>
          New generation
        </button>
        <button
          className="sidebar-row"
          onClick={() => setView("compose")}
          type="button"
          title={params.reference_paths.length ? params.reference_paths.join("\n") : "No references selected"}
        >
          <span className="glyph" aria-hidden>
            ▤
          </span>
          References
          <span className="count">{params.reference_paths.length}/10</span>
        </button>
      </div>

      <div className="col" style={{ gap: 2 }}>
        <div className="sidebar-section-label">Library</div>
        <button className={`sidebar-row ${view === "avatars" ? "active" : ""}`}
          onClick={() => { setView("avatars"); void useStore.getState().refreshAvatars(); }} type="button">
          <span className="glyph" aria-hidden>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
              <circle cx="12" cy="8" r="4" /><path d="M4 21v-2a8 8 0 0 1 16 0v2" />
            </svg>
          </span>
          Avatars<span className="count">{avatars.length}</span>
        </button>
        <button
          className={`sidebar-row ${view === "library" && !favoritesOnly ? "active" : ""}`}
          onClick={() => {
            setFavoritesOnly(false);
            setView("library");
            void loadLibrary();
          }}
          type="button"
        >
          <span className="glyph" aria-hidden>
            ▦
          </span>
          All images
          <span className="count">{library.length}</span>
        </button>
        <button
          className={`sidebar-row ${view === "library" && favoritesOnly ? "active" : ""}`}
          onClick={() => {
            setFavoritesOnly(true);
            setView("library");
            void loadLibrary();
          }}
          type="button"
        >
          <span className="glyph" aria-hidden>
            ★
          </span>
          Favourites
          <span className="count">{favourites}</span>
        </button>
        <button
          className="sidebar-row"
          onClick={() => void openPath(outputsDir)}
          title={outputsDir}
          type="button"
        >
          <span className="glyph" aria-hidden>
            ⌸
          </span>
          Outputs folder
          {settings.outputDir ? <span className="caption faint">custom</span> : null}
        </button>
      </div>

      <div className="col" style={{ gap: 2 }}>
        <div className="sidebar-section-label">Models</div>
        <button
          className={`sidebar-row ${view === "models" ? "active" : ""}`}
          onClick={() => setView("models")}
          type="button"
        >
          <span className="glyph" aria-hidden>
            ◈
          </span>
          Model sources
          <span className="count">{readySources}</span>
        </button>
        {running ? (
          <div className="sidebar-row" style={{ cursor: "default" }}>
            <span className="glyph" aria-hidden>
              <span className="spinner" />
            </span>
            {currentJob.total_steps ? `${currentJob.step}/${currentJob.total_steps}` : "working"}
          </div>
        ) : null}
      </div>

      <div className="grow" />

      <section className="sidebar-sessions" aria-label="Past sessions">
        <div className="sidebar-section-label">Past sessions</div>
        <div className="sidebar-session-list">
          {sessions.map(({ project, session }) => {
            const title = session.session.prompt?.trim() || session.name;
            const selected = params.project_id === project.id && params.project_session_id === session.id;
            return <button key={`${project.id}/${session.id}`} type="button"
              className={`sidebar-row sidebar-session ${selected ? "active" : ""}`}
              aria-current={selected ? "true" : undefined}
              title={`${title}\n${project.name} · ${new Date(session.updated_at * 1000).toLocaleString()}`}
              onClick={async () => { await openProject(project.id, session.id); setView("compose"); }}>
              <span className="truncate">{title}</span>
              <span className="caption muted truncate">{project.name} · {new Date(session.updated_at * 1000).toLocaleDateString()}</span>
            </button>;
          })}
          {!sessions.length ? <p className="caption muted sidebar-session-empty">Your saved sessions will appear here.</p> : null}
        </div>
      </section>

      <div className="col" style={{ gap: 6, padding: "0 8px" }}>
        <div className="divider" />
        <div className="caption muted col" style={{ gap: 4 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span>MLX peak</span>
            <span className="mono">{system ? `${system.mlx_peak_memory_gb.toFixed(1)} GB` : "—"}</span>
          </div>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span>Memory</span>
            <span className="mono">{system ? `${Math.round(system.total_ram_gb)} GB` : "—"}</span>
          </div>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span>Free disk</span>
            <span className="mono">{system ? humanBytes(system.free_disk_gb * 1e9) : "—"}</span>
          </div>
          {currentJob?.peak_memory_gb ? (
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span>Last run</span>
              <span className="mono">{humanDuration(currentJob.elapsed_seconds)}</span>
            </div>
          ) : null}
        </div>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          {health ? (
            health.mock ? (
              <Pill tone="warn">mock</Pill>
            ) : (
              <Pill tone="ok">mflux {health.mflux_version}</Pill>
            )
          ) : (
            <Pill tone="bad">no service</Pill>
          )}
          {system ? <Pill>{system.chip.split(" ").slice(-2).join(" ")}</Pill> : null}
        </div>
      </div>
    </nav>
  );
}
