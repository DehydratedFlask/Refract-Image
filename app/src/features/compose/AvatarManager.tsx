import { useState } from "react";
import { api, type Avatar, type AvatarDraft, type AvatarReferenceRole } from "../../lib/api";
import { pickImages } from "../../lib/ipc";
import { useStore } from "../../store/useStore";
import { Button, ConfirmButton, Sheet, useFileUrl } from "../../components/ui";

function AvatarImage({ path, onRemove }: { path: string; onRemove?: () => void }) {
  const url = useFileUrl(path);
  return <div className="avatar-image">
    {url ? <img src={url} alt="Avatar reference" /> : <span className="faint">Image</span>}
    {onRemove ? <button type="button" aria-label="Remove avatar image" onClick={onRemove}>×</button> : null}
  </div>;
}

const blank = (): AvatarDraft => ({ name: "", handle: "", description: "", references: [], reference_roles: [] });
const makeHandle = (name: string) => name.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);

export function AvatarManager({ onClose, embedded = false }: { onClose: () => void; embedded?: boolean }) {
  const avatars = useStore((state) => state.avatars);
  const refresh = useStore((state) => state.refreshAvatars);
  const toast = useStore((state) => state.toast);
  const [draft, setDraft] = useState<AvatarDraft>(blank);
  const [editing, setEditing] = useState<string | null>(null);
  const [customHandle, setCustomHandle] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  function reset() { setDraft(blank()); setEditing(null); setCustomHandle(false); setError(""); }
  function edit(avatar: Avatar) {
    setDraft({ name: avatar.name, handle: avatar.handle, description: avatar.description, references: avatar.references,
      reference_roles: avatar.reference_roles ?? avatar.references.map(() => "reference") });
    setEditing(avatar.id); setCustomHandle(true); setError("");
  }
  async function save() {
    setBusy(true); setError("");
    try {
      await api.saveAvatar(draft, editing ?? undefined);
      await refresh(); reset(); toast("Avatar saved. Type @ in your prompt to use it.", "success");
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  async function remove(id: string) {
    setBusy(true);
    try { await api.deleteAvatar(id); await refresh(); if (editing === id) reset(); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  function removeReference(index: number) {
    setDraft((previous) => ({ ...previous, references: previous.references.filter((_, i) => i !== index),
      reference_roles: previous.reference_roles.filter((_, i) => i !== index) }));
  }
  async function addReferences(role: AvatarReferenceRole) {
    setBusy(true); setError("");
    try {
      const picked = await pickImages();
      if (!picked.length) return;
      setDraft((previous) => {
        const references = [...previous.references];
        const roles = [...previous.reference_roles];
        if (role !== "reference") {
          const index = roles.indexOf(role);
          if (index >= 0) { references.splice(index, 1); roles.splice(index, 1); }
        }
        for (const path of role === "reference" ? picked : picked.slice(0, 1)) {
          const existing = references.indexOf(path);
          if (existing >= 0) roles[existing] = role;
          else if (references.length < 10) { references.push(path); roles.push(role); }
        }
        return { ...previous, references, reference_roles: roles };
      });
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  const content =
      <div className={embedded ? "avatar-manager-layout" : "col"} style={{ gap: 18 }}>
        <section className="col" style={{ gap: 12 }} aria-label="Saved avatars">
        <p className="muted" style={{ margin: 0 }}>Save a person or subject once, then use @name in any prompt. Your avatars stay available across projects and app restarts.</p>
        {avatars.length ? <div className="col" style={{ gap: 8 }}>
          {avatars.map((avatar) => <div className="row card" key={avatar.id} style={{ padding: 10, gap: 10 }}>
            <AvatarImage path={avatar.references[0]} />
            <div className="col grow" style={{ gap: 3, minWidth: 0 }}><strong className="truncate">{avatar.name}</strong>
              <span className="caption muted">@{avatar.handle} · {avatar.references.length} images</span></div>
            <Button disabled={busy} onClick={() => edit(avatar)}>Edit</Button>
            {!busy ? <ConfirmButton label="Delete" confirmLabel="Delete avatar?" onConfirm={() => void remove(avatar.id)} /> : null}
          </div>)}
        </div> : <span className="caption muted">No avatars yet. Save your first avatar to use it in future prompts.</span>}
        </section>
        {!embedded ? <div className="divider" /> : null}
        <section className="col" style={{ gap: 18, minWidth: 0 }} aria-label={editing ? "Edit avatar" : "Add avatar"}>
        <div className="row"><strong>{editing ? "Edit avatar" : "Add avatar"}</strong><div className="grow" />
          {editing ? <Button disabled={busy} variant="subtle" onClick={reset}>＋ Add new</Button> : null}</div>
        <fieldset disabled={busy} className="col avatar-fields" style={{ gap: 12 }}>
          <label className="col" style={{ gap: 5 }}>Name
            <input className="input" autoFocus value={draft.name} maxLength={60} placeholder="e.g. Alex" onChange={(event) => {
              const name = event.target.value;
              setDraft({ ...draft, name, handle: customHandle ? draft.handle : makeHandle(name) });
            }} /></label>
          <label className="col" style={{ gap: 5 }}>@handle
            <input className="input mono" value={draft.handle} maxLength={40} placeholder="alex" onChange={(event) => {
              setCustomHandle(true); setDraft({ ...draft, handle: event.target.value.replace(/^@/, "") });
            }} /></label>
          <label className="col" style={{ gap: 5 }}>Description (optional)
            <input className="input" value={draft.description} maxLength={500} placeholder="e.g. The person with short dark hair" onChange={(event) => setDraft({ ...draft, description: event.target.value })} /></label>
          <span className="field-label">Reference images · {draft.references.length}/10</span>
          <div className="avatar-reference-slots">
            {(["face", "body"] as const).map((role) => {
              const index = draft.reference_roles.indexOf(role);
              return <div className="avatar-reference-slot" key={role}>
                <span className="field-label">{role === "face" ? "Face reference" : "Body reference"}</span>
                <button className="avatar-reference-picker" type="button"
                  disabled={index < 0 && draft.references.length >= 10}
                  aria-label={`${index >= 0 ? "Replace" : "Add"} ${role} reference`}
                  onClick={() => void addReferences(role)}>
                  {index >= 0 ? <AvatarImage path={draft.references[index]} /> : <span className="caption muted">Add {role === "face" ? "a face portrait" : "a full-body image"}</span>}
                </button>
                {index >= 0 ? <Button variant="subtle" onClick={() => removeReference(index)}>Remove {role} reference</Button> : <span className="caption muted">Optional</span>}
              </div>;
            })}
          </div>
          <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
            {draft.references.map((path, index) => draft.reference_roles[index] === "reference"
              ? <AvatarImage key={path} path={path} onRemove={() => removeReference(index)} /> : null)}
            <Button disabled={draft.references.length >= 10} onClick={() => void addReferences("reference")}>＋ Add other references</Button>
          </div>
          <span className="caption muted">Use either slot, both, or other subject images. References are copied into the app when you save.</span>
        </fieldset>
        {error ? <div role="alert" style={{ color: "var(--danger)" }}>{error}</div> : null}
        <Button variant="primary" loading={busy} disabled={!draft.name.trim() || !draft.handle || !draft.references.length} onClick={() => void save()}>
          {editing ? "Save changes" : "Save avatar"}
        </Button>
        </section>
      </div>;
  if (embedded) return <div className="avatar-library">{content}</div>;
  return <div onKeyDown={(event) => { event.stopPropagation(); if (event.key === "Escape") onClose(); }}>
    <Sheet title="Avatars" onClose={onClose} width={600}>{content}</Sheet>
  </div>;
}
