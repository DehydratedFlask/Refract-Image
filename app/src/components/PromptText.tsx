import { useStore } from "../store/useStore";
import { copyText } from "../lib/ipc";
import { Button } from "./ui";

export function PromptText({ prompt }: { prompt: string }) {
  const toast = useStore((state) => state.toast);
  return (
    <div className="col" style={{ gap: 6, minWidth: 0 }}>
      <div className="row" style={{ gap: 8 }}>
        <span className="field-label grow">Prompt</span>
        <Button variant="subtle" disabled={!prompt} onClick={async () => {
          const ok = await copyText(prompt);
          toast(ok ? "Prompt copied" : "Could not copy — select the prompt and press ⌘C", ok ? "success" : "error");
        }}>Copy prompt</Button>
      </div>
      <div className="selectable prompt-text">{prompt}</div>
    </div>
  );
}
