import { useState } from "react";
import { api, type Avatar, type AvatarDraft } from "../../lib/api";
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

const blank = (): AvatarDraft => ({ name: "", handle: "", description: "", references: [] });
const makeHandle = (name: string) => name.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);

export function AvatarManager({ onClose }: { onClose: () => void }) {
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
    setDraft({ name: avatar.name, handle: avatar.handle, description: avatar.description, references: avatar.references });
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
  return <div onKeyDown={(event) => { event.stopPropagation(); if (event.key === "Escape") onClose(); }}>
    <Sheet title="Avatars" onClose={onClose} width={600}>
      <div className="col" style={{ gap: 18 }}>
        <p className="muted">Save reference images for a person or subject. Type @ and choose their name in any prompt to include those images.</p>
        {avatars.length ? <div className="col" style={{ gap: 8 }}>
          {avatars.map((avatar) => <div className="row card" key={avatar.id} style={{ padding: 10, gap: 10 }}>
            <AvatarImage path={avatar.references[0]} />
            <div className="col grow" style={{ gap: 3 }}><strong>{avatar.name}</strong>
              <span className="caption muted">@{avatar.handle} · {avatar.references.length} images</span></div>
            <Button disabled={busy} onClick={() => edit(avatar)}>Edit</Button>
            {!busy ? <ConfirmButton label="Delete" confirmLabel="Delete avatar?" onConfirm={() => void remove(avatar.id)} /> : null}
          </div>)}
        </div> : <span className="caption faint">No avatars yet. Add your first below.</span>}
        <div className="divider" />
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
          <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
            {draft.references.map((path, index) => <AvatarImage key={path} path={path} onRemove={() => setDraft({ ...draft, references: draft.references.filter((_, i) => i !== index) })} />)}
            <Button disabled={draft.references.length >= 10} onClick={async () => {
              const picked = await pickImages();
              setDraft((previous) => ({ ...previous, references: [...new Set([...previous.references, ...picked])].slice(0, 10) }));
            }}>＋ Add images</Button>
          </div>
          <span className="caption faint">Images are copied into the app. Originals stay where they are.</span>
        </fieldset>
        {error ? <div role="alert" style={{ color: "var(--danger)" }}>{error}</div> : null}
        <Button variant="primary" loading={busy} disabled={!draft.name.trim() || !draft.handle || !draft.references.length} onClick={() => void save()}>
          {editing ? "Save changes" : "Save avatar"}
        </Button>
      </div>
    </Sheet>
  </div>;
}
