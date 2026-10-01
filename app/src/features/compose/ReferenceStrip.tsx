import { useState } from "react";
import { useStore } from "../../store/useStore";
import { Button, useFileUrl } from "../../components/ui";
import { baseName } from "../../lib/format";
import { pickImages } from "../../lib/ipc";

function Thumb({
  path,
  index,
  onRemove,
  onDragStart,
  onDropOn,
}: {
  path: string;
  index: number;
  onRemove: () => void;
  onDragStart: () => void;
  onDropOn: () => void;
}) {
  const url = useFileUrl(path);
  const [over, setOver] = useState(false);
  return (
    <div
      className="thumb"
      draggable
      title={`${baseName(path)}\nDrag to change order — reference order is significant to the model`}
      style={{ width: 72, height: 72, outline: over ? "2px solid var(--accent)" : undefined }}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = "move";
        onDragStart();
      }}
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        onDropOn();
      }}
    >
      {url ? <img src={url} alt={baseName(path)} /> : <div className="center" style={{ height: "100%" }} />}
      <span className="badge">{index + 1}</span>
      <button className="remove" onClick={onRemove} title="Remove" type="button">
        ✕
      </button>
    </div>
  );
}

export function ReferenceStrip({ dragActive }: { dragActive: boolean }) {
  const references = useStore((state) => state.params.reference_paths);
  const addReferences = useStore((state) => state.addReferences);
  const removeReference = useStore((state) => state.removeReference);
  const moveReference = useStore((state) => state.moveReference);
  const clearReferences = useStore((state) => state.clearReferences);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [localOver, setLocalOver] = useState(false);

  const active = dragActive || localOver;
  const full = references.length >= 10;

  return (
    <div className="section">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="field-label">
          Reference images
          <span className="faint" style={{ textTransform: "none", letterSpacing: 0 }}>
            {references.length}/10 · order matters
          </span>
        </span>
        {references.length ? (
          <Button variant="subtle" onClick={clearReferences}>
            Clear
          </Button>
        ) : null}
      </div>

      <div
        className="dropzone"
        data-active={active}
        style={{
          minHeight: 88,
          padding: 8,
          display: "flex",
          alignItems: "center",
          gap: 8,
          // Empty: the ＋ tile and the hint stack, centred. With references: a wrapping row.
          flexDirection: references.length ? "row" : "column",
          flexWrap: references.length ? "wrap" : "nowrap",
          justifyContent: "center",
        }}
        onDragOver={(event) => {
          if (event.dataTransfer?.types?.includes("Files")) {
            event.preventDefault();
            setLocalOver(true);
          }
        }}
        onDragLeave={() => setLocalOver(false)}
        onDrop={() => setLocalOver(false)}
      >
        {references.map((path, index) => (
          <Thumb
            key={`${path}-${index}`}
            path={path}
            index={index}
            onRemove={() => removeReference(index)}
            onDragStart={() => setDragIndex(index)}
            onDropOn={() => {
              if (dragIndex !== null) moveReference(dragIndex, index);
              setDragIndex(null);
            }}
          />
        ))}

        {!full ? (
          <button
            className="thumb center"
            style={{ width: 72, height: 72, background: "transparent", border: "1.5px dashed var(--border-strong)" }}
            onClick={async () => addReferences(await pickImages())}
            title="Add reference images"
            type="button"
          >
            <span className="faint" style={{ fontSize: 20 }} aria-hidden>
              ＋
            </span>
          </button>
        ) : null}

        {!references.length ? (
          <div className="col center" style={{ gap: 2, pointerEvents: "none", textAlign: "center", maxWidth: 360 }}>
            <span className="muted">Drop images here, or press ＋ to choose files</span>
            <span className="caption faint">
              Up to 10. With no references the run is plain text-to-image; with references the model edits what
              it is shown. Reference order is significant.
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
