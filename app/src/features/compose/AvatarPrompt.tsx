import { useRef, useState } from "react";
import { useStore } from "../../store/useStore";
import { mentionedAvatars, generationReferences } from "../../lib/avatars";
import type { Avatar } from "../../lib/api";
import { Button } from "../../components/ui";
import { AvatarManager } from "./AvatarManager";

export function AvatarPrompt({ placeholder }: { placeholder: string }) {
  const params = useStore((state) => state.params);
  const setParams = useStore((state) => state.setParams);
  const avatars = useStore((state) => state.avatars);
  const [manager, setManager] = useState(false);
  const [mention, setMention] = useState<{ start: number; end: number; query: string } | null>(null);
  const [active, setActive] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const suggestions = mention ? avatars.filter((avatar) => avatar.handle.startsWith(mention.query.toLowerCase()) || avatar.name.toLowerCase().includes(mention.query.toLowerCase())).slice(0, 8) : [];
  const open = Boolean(mention && !dismissed);
  const activeSuggestion = suggestions[Math.min(active, Math.max(0, suggestions.length - 1))];
  const tagged = mentionedAvatars(params.prompt, avatars);
  const total = generationReferences(params.prompt, params.reference_paths, avatars).length;

  function locate(value: string, caret: number) {
    const match = /(?<![\w@])@([a-zA-Z0-9_-]*)$/.exec(value.slice(0, caret));
    setMention(match ? { start: match.index, end: caret + (value.slice(caret).match(/^[a-zA-Z0-9_-]*/)?.[0].length ?? 0), query: match[1] } : null);
    setActive(0); setDismissed(false);
  }
  function choose(avatar: Avatar) {
    if (!mention) return;
    const text = `@${avatar.handle} `;
    setParams({ prompt: params.prompt.slice(0, mention.start) + text + params.prompt.slice(mention.end) });
    const caret = mention.start + text.length;
    setMention(null); setDismissed(true);
    requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(caret, caret); });
  }

  return <div className="col" style={{ gap: 6 }}>
    <div className="row" style={{ justifyContent: "space-between" }}>
      <label className="field-label" htmlFor="generation-prompt">Prompt</label>
      <Button variant="subtle" onClick={() => setManager(true)}>Avatars · {avatars.length}</Button>
    </div>
    <div className="avatar-prompt">
      <textarea id="generation-prompt" ref={input} className="textarea" rows={5} placeholder={placeholder}
        value={params.prompt} spellCheck={false} role="combobox" aria-autocomplete="list"
        aria-expanded={open} aria-controls={open ? "avatar-suggestions" : undefined}
        aria-activedescendant={open && activeSuggestion ? `avatar-option-${activeSuggestion.id}` : undefined}
        onChange={(event) => { setParams({ prompt: event.target.value }); locate(event.target.value, event.target.selectionStart); }}
        onClick={(event) => locate(event.currentTarget.value, event.currentTarget.selectionStart)}
        onKeyUp={(event) => {
          if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) locate(event.currentTarget.value, event.currentTarget.selectionStart);
        }}
        onBlur={() => setDismissed(true)}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || !open) return;
          if (event.key === "Escape") { event.stopPropagation(); event.preventDefault(); setDismissed(true); }
          else if (suggestions.length && ["ArrowDown", "ArrowUp", "Enter", "Tab"].includes(event.key) && !event.metaKey && !event.ctrlKey
            && !(event.shiftKey && ["Tab", "Enter"].includes(event.key))) {
            event.preventDefault(); event.stopPropagation();
            if (event.key === "ArrowDown") setActive((active + 1) % suggestions.length);
            else if (event.key === "ArrowUp") setActive((active + suggestions.length - 1) % suggestions.length);
            else choose(activeSuggestion ?? suggestions[0]);
          }
        }} />
      {open ? <div className="avatar-suggestions" id="avatar-suggestions" role="listbox" aria-label="Avatar suggestions">
        {suggestions.map((avatar, index) => <button key={avatar.id} type="button" role="option"
          id={`avatar-option-${avatar.id}`} aria-selected={avatar.id === activeSuggestion?.id}
          onMouseEnter={() => setActive(index)}
          onMouseDown={(event) => event.preventDefault()} onClick={() => choose(avatar)}>
          <strong>{avatar.name}</strong><span className="caption muted">@{avatar.handle} · {avatar.references.length} images</span>
        </button>)}
        {!suggestions.length ? <div className="caption muted" style={{ padding: 10 }}>No matching avatars. Add one with the Avatars button.</div> : null}
      </div> : null}
    </div>
    <span className="caption muted">Type @ to find a saved avatar. Use ↑/↓ to choose, then Tab to confirm.</span>
    {tagged.length ? <div className="col" style={{ gap: 6 }}>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>{tagged.map((avatar) =>
        <span className="pill accent" key={avatar.id}>@{avatar.handle} · {avatar.references.length} images</span>)}</div>
      <span className="caption" style={{ color: total > 10 ? "var(--danger)" : "var(--text-secondary)" }}>
        {total}/10 references including avatars{total > 10 ? " — remove an avatar tag or reference image" : ""}
      </span>
    </div> : null}
    {manager ? <AvatarManager onClose={() => setManager(false)} /> : null}
  </div>;
}
