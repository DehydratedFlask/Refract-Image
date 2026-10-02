/** Projects contain saved generation sessions and their editor. */
import { useEffect, useState } from "react";
import { useStore } from "../../store/useStore";
import { Button, ConfirmButton, EmptyState, Pill, useFileUrl } from "../../components/ui";
import type { Project } from "../../lib/api";
import { relativeTime } from "../../lib/format";
import { ComposeView } from "../compose/ComposeView";
import { openPath } from "../../lib/ipc";

function ProjectThumb({ project }: { project: Project }) {
  // The first saved reference stands in for the project: it is the image the work is about,
  // and it is already on disk whether or not the project has produced anything yet.
  const url = useFileUrl(project.references[0] ?? null);
  return (
    <div className="project-thumb">
      {url ? <img src={url} alt="" draggable={false} /> : <span className="caption faint">{project.name.slice(0, 1) || "◦"}</span>}
    </div>
  );
}

export function ProjectsView({ dragActive }: { dragActive: boolean }) {
  const projects = useStore((state) => state.projects);
  const storage = useStore((state) => state.projectStorage);
  const activeProjectId = useStore((state) => state.activeProjectId);
  const openProject = useStore((state) => state.openProject);
  const createProject = useStore((state) => state.createProject);
  const renameProject = useStore((state) => state.renameProject);
  const deleteProject = useStore((state) => state.deleteProject);
  const refreshProjects = useStore((state) => state.refreshProjects);
  const toast = useStore((state) => state.toast);
  const newSession = useStore((state) => state.newProjectSession);
  const browsing = useStore((state) => state.projectBrowserOpen);
  const setBrowsing = (projectBrowserOpen: boolean) => useStore.setState({ projectBrowserOpen });
  const [switching, setSwitching] = useState(false);
  const activeProject = projects.find((entry) => entry.id === activeProjectId);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    void refreshProjects();
  }, [refreshProjects]);

  const commitRename = (project: Project) => {
    setRenaming(null);
    if (draft.trim() && draft.trim() !== project.name) void renameProject(project.id, draft);
  };

  const startProject = async () => {
    const id = await createProject();
    if (id) setBrowsing(false);
  };
  const enterProject = async (id: string) => {
    setSwitching(true);
    await openProject(id);
    setSwitching(false);
    setBrowsing(false);
  };

  if (activeProject && !browsing) {
    return (
      <div className="project-workspace">
        <div className="project-session-bar row">
          <Button variant="subtle" onClick={() => setBrowsing(true)}>← All projects</Button>
          <strong>{activeProject.name}</strong>
          <select className="select" style={{ maxWidth: 300 }} aria-label="Project session"
            value={activeProject.active_session_id} disabled={switching}
            onChange={async (event) => {
              const sessionId = event.target.value;
              setSwitching(true);
              await openProject(activeProject.id, sessionId);
              setSwitching(false);
            }}>
            {activeProject.sessions.map((entry) => <option key={entry.id} value={entry.id}>
              {entry.name}{entry.session.prompt ? ` — ${entry.session.prompt.slice(0, 50)}` : ""}
            </option>)}
          </select>
          <Button variant="primary" disabled={switching} onClick={async () => {
            setSwitching(true);
            await newSession(activeProject.id);
            setSwitching(false);
          }}>＋ New session</Button>
          <div className="grow" />
          <Pill>{activeProject.generation_count} results</Pill>
          <Button variant="subtle" onClick={() => {
            useStore.setState({ view: "library", libraryProjectFilter: activeProject.id });
            void useStore.getState().loadLibrary();
          }}>Project images</Button>
        </div>
        <ComposeView dragActive={dragActive} />
      </div>
    );
  }

  if (!projects.length) {
    return (
      <EmptyState
        glyph="❏"
        title="No projects yet"
        body="A project saves your prompt, your reference images and every setting, so you can put one on hold, start something else, and come back to it exactly as you left it. Reference images are copied into the project, so they survive even if the originals move."
        action={
          <Button variant="primary" onClick={() => void startProject()}>
            New project
          </Button>
        }
      />
    );
  }

  return (
    <div className="scroll-area">
      <div className="col" style={{ gap: 16, padding: 20, maxWidth: 980 }}>
        <div className="row" style={{ gap: 8 }}>
          <span className="sidebar-section-label" style={{ padding: 0 }}>
            {projects.length} project{projects.length === 1 ? "" : "s"}
          </span>
          <div className="grow" />
          <Pill>{storage?.total_size_human ?? "—"} of references</Pill>
          <Button variant="primary" onClick={() => void startProject()}>
            ＋ New project
          </Button>
        </div>

        <div className="col" style={{ gap: 8 }}>
          {projects.map((project) => {
            const active = project.id === activeProjectId;
            const missing = project.missing_references.length;
            return (
              <div key={project.id} className={`project-row card ${active ? "active" : ""}`}>
                <ProjectThumb project={project} />

                <div className="col grow" style={{ gap: 4, minWidth: 0 }}>
                  {renaming === project.id ? (
                    <input
                      className="input"
                      value={draft}
                      autoFocus
                      onChange={(event) => setDraft(event.target.value)}
                      onBlur={() => commitRename(project)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") commitRename(project);
                        if (event.key === "Escape") setRenaming(null);
                      }}
                    />
                  ) : (
                    <button
                      className="project-name"
                      title={project.prompt || project.name}
                      onClick={() => void enterProject(project.id)}
                      type="button"
                    >
                      {project.name}
                    </button>
                  )}
                  <div className="caption faint truncate selectable">
                    {project.prompt || "No prompt yet"} · edited {relativeTime(project.updated_at)}
                  </div>
                  <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                    {active ? <Pill tone="accent">open</Pill> : null}
                    <Pill>{project.sessions.length} sessions</Pill>
                    <Pill>
                      {project.reference_count} ref{project.reference_count === 1 ? "" : "s"}
                    </Pill>
                    <Pill tone={project.generation_count ? "ok" : undefined}>
                      {project.generation_count} result{project.generation_count === 1 ? "" : "s"}
                    </Pill>
                    {project.running_count ? <Pill tone="warn">{project.running_count} running</Pill> : null}
                    {missing ? <Pill tone="bad">{missing} missing</Pill> : null}
                  </div>
                </div>

                <div className="col" style={{ gap: 4, alignItems: "flex-end" }}>
                  <Button
                    variant={active ? "subtle" : "default"}
                    onClick={() => void enterProject(project.id)}
                    title="Open this project’s sessions and generation workspace"
                  >
                    Open workspace
                  </Button>
                  <Button variant="subtle" disabled={switching} onClick={async () => {
                    setSwitching(true);
                    await newSession(project.id);
                    setSwitching(false);
                    setBrowsing(false);
                  }}>＋ New session</Button>
                  {active ? (
                    <Button variant="subtle" onClick={() => void openPath(storage?.dir ?? "")} title={storage?.dir}>
                      ⌸ References
                    </Button>
                  ) : null}
                  <div className="row" style={{ gap: 2 }}>
                    <Button
                      variant="subtle"
                      title="Rename"
                      onClick={() => {
                        setRenaming(project.id);
                        setDraft(project.name);
                      }}
                    >
                      ✎
                    </Button>
                    <ConfirmButton
                      label="✕"
                      confirmLabel="Delete?"
                      title="Delete this project. Its reference copies go too — generated images are kept."
                      onConfirm={() => {
                        void deleteProject(project.id);
                        if (active) toast("Project deleted — its generated images are still in the Library", "info");
                      }}
                    />
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <div className="caption faint">
          Reference images are copied into <span className="mono">{storage?.dir ?? "the projects folder"}</span> when you
          add them, so a project still opens if you move or delete the originals. Generated images are not copied —
          they stay wherever outputs are sent, and stay in the Library if the project is deleted.
        </div>
      </div>
    </div>
  );
}