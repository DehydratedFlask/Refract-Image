import { useEffect, useRef, useState, type ReactNode } from "react";
import { Button, Segmented, useFileUrl } from "../../components/ui";
import { baseName } from "../../lib/format";

type Mode = "split" | "wipe" | "result";

interface Transform {
  scale: number;
  x: number;
  y: number;
}

const FIT: Transform = { scale: 1, x: 0, y: 0 };

/**
 * Side-by-side comparison of the reference images and the generated result, with zoom and
 * pan shared between panes so the same region sits under the cursor in both. The wipe mode
 * overlays them for pixel-level differences.
 */
export function CompareView({
  references,
  outputs,
  actions,
  caption,
}: {
  references: string[];
  outputs: string[];
  actions?: ReactNode;
  caption?: ReactNode;
}) {
  const [mode, setMode] = useState<Mode>(outputs.length && references.length ? "split" : "result");
  const [referenceIndex, setReferenceIndex] = useState(0);
  const [outputIndex, setOutputIndex] = useState(0);
  const [transform, setTransform] = useState<Transform>(FIT);
  const [wipe, setWipe] = useState(0.5);
  const panRef = useRef<{ x: number; y: number; origin: Transform } | null>(null);
  const wipeSurface = useRef<HTMLDivElement | null>(null);

  const safeReferenceIndex = Math.min(referenceIndex, Math.max(0, references.length - 1));
  const safeOutputIndex = Math.min(outputIndex, Math.max(0, outputs.length - 1));
  const reference = references[safeReferenceIndex];
  const output = outputs[safeOutputIndex];

  // All hooks run unconditionally, before any pane is chosen.
  const referenceUrl = useFileUrl(reference);
  const outputUrl = useFileUrl(output);

  useEffect(() => {
    setTransform(FIT);
  }, [reference, output]);

  useEffect(() => {
    if (mode === "result" && outputs.length && !references.length) return;
    if (!outputs.length) setMode("result");
    else if (!references.length && mode !== "result") setMode("result");
  }, [mode, outputs.length, references.length]);

  const zoomBy = (factor: number) =>
    setTransform((current) => ({ ...current, scale: clamp(current.scale * factor, 0.2, 12) }));

  const onWheel = (event: React.WheelEvent) => {
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.1 : 0.9;
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const cx = event.clientX - rect.left - rect.width / 2;
    const cy = event.clientY - rect.top - rect.height / 2;
    setTransform((current) => {
      const scale = clamp(current.scale * factor, 0.2, 12);
      const ratio = scale / current.scale;
      return { scale, x: cx - (cx - current.x) * ratio, y: cy - (cy - current.y) * ratio };
    });
  };

  const startPan = (event: React.PointerEvent) => {
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    panRef.current = { x: event.clientX, y: event.clientY, origin: transform };
  };
  const movePan = (event: React.PointerEvent) => {
    if (!panRef.current) return;
    const { x, y, origin } = panRef.current;
    setTransform({ ...origin, x: origin.x + (event.clientX - x), y: origin.y + (event.clientY - y) });
  };
  const endPan = () => {
    panRef.current = null;
  };

  const imageStyle: React.CSSProperties = {
    transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
  };

  const renderPane = (url: string | null, path: string | undefined, label: string) => (
    <div className="col" style={{ gap: 6, minWidth: 0, minHeight: 0, flex: 1 }}>
      <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
        <span className="caption muted truncate" title={path}>
          {label}
        </span>
        <span className="caption faint truncate" style={{ maxWidth: 180 }}>
          {path ? baseName(path) : ""}
        </span>
      </div>
      <div
        className="compare-canvas"
        onWheel={onWheel}
        onPointerDown={startPan}
        onPointerMove={movePan}
        onPointerUp={endPan}
        onPointerCancel={endPan}
        style={{ cursor: "grab" }}
      >
        {url ? <img src={url} alt={label} style={imageStyle} draggable={false} /> : <span className="faint">nothing to show</span>}
      </div>
    </div>
  );

  const startWipeDrag = (event: React.PointerEvent) => {
    const surface = wipeSurface.current;
    if (!surface) return;
    const rect = surface.getBoundingClientRect();
    const move = (clientX: number) => setWipe(clamp((clientX - rect.left) / rect.width, 0, 1));
    move(event.clientX);
    const onMove = (moveEvent: PointerEvent) => move(moveEvent.clientX);
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const hasBoth = references.length > 0 && outputs.length > 0;
  const isWipe = mode === "wipe" && hasBoth;

  return (
    <div className="compare-view col grow">
      <div className="compare-toolbar row">
        <Segmented<Mode>
          value={isWipe ? "wipe" : mode}
          onChange={setMode}
          options={[
            { value: "split", label: "Side by side" },
            { value: "wipe", label: "Wipe" },
            { value: "result", label: "Result" },
          ]}
        />

        {references.length > 1 && mode !== "result" ? (
          <select
            className="select"
            style={{ maxWidth: 220 }}
            value={safeReferenceIndex}
            onChange={(event) => setReferenceIndex(Number(event.target.value))}
          >
            {references.map((path, index) => (
              <option key={`${path}-${index}`} value={index}>
                Reference {index + 1}: {baseName(path)}
              </option>
            ))}
          </select>
        ) : null}

        {outputs.length > 1 ? (
          <select
            className="select"
            style={{ maxWidth: 200 }}
            value={safeOutputIndex}
            onChange={(event) => setOutputIndex(Number(event.target.value))}
          >
            {outputs.map((path, index) => (
              <option key={path} value={index}>
                Variant {index + 1}
              </option>
            ))}
          </select>
        ) : null}

        <div className="grow" />
        <span className="caption faint">scroll to zoom · drag to pan</span>
        <Button variant="subtle" onClick={() => setTransform(FIT)} title="Reset zoom and pan">
          {Math.round(transform.scale * 100)}%
        </Button>
        <Button variant="subtle" onClick={() => zoomBy(1 / 1.25)} aria-label="Zoom out">
          −
        </Button>
        <Button variant="subtle" onClick={() => zoomBy(1.25)} aria-label="Zoom in">
          ＋
        </Button>
      </div>

      <div className="compare-stage row grow" style={{ minHeight: 0, gap: 12 }}>
        {mode === "split" && hasBoth ? (
          <>
            {renderPane(referenceUrl, reference, `Reference ${safeReferenceIndex + 1} of ${references.length}`)}
            {renderPane(outputUrl, output, "Result")}
          </>
        ) : (
          <div className="col grow" style={{ minHeight: 0, gap: 10 }}>
            <div
              ref={wipeSurface}
              className="compare-canvas"
              onWheel={isWipe ? undefined : onWheel}
              onPointerDown={isWipe ? undefined : startPan}
              onPointerMove={isWipe ? undefined : movePan}
              onPointerUp={endPan}
              onPointerCancel={endPan}
              style={{ cursor: isWipe ? "col-resize" : "grab", position: "relative" }}
            >
              {isWipe ? (
                <>
                  {referenceUrl ? <img src={referenceUrl} alt="Reference" style={imageStyle} draggable={false} /> : null}
                  {outputUrl ? (
                    <img
                      src={outputUrl}
                      alt="Result"
                      style={{ ...imageStyle, position: "absolute", clipPath: `inset(0 ${100 - wipe * 100}% 0 0)` }}
                      draggable={false}
                    />
                  ) : null}
                  <div className="wipe-handle" style={{ left: `${wipe * 100}%` }} onPointerDown={startWipeDrag} />
                  <div
                    className="floating-bar"
                    style={{ position: "absolute", bottom: 10, left: "50%", transform: "translateX(-50%)" }}
                  >
                    <span className="caption muted">reference</span>
                    <input
                      aria-label="Wipe position"
                      type="range"
                      min={0}
                      max={100}
                      value={Math.round(wipe * 100)}
                      onChange={(event) => setWipe(Number(event.target.value) / 100)}
                      style={{ width: 170 }}
                    />
                    <span className="caption muted">result</span>
                  </div>
                </>
              ) : outputUrl ? (
                <img src={outputUrl} alt="Result" style={imageStyle} draggable={false} />
              ) : (
                <span className="faint">nothing to show</span>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="compare-footer col" style={{ gap: 8 }}>
        <div className="caption muted">{caption}</div>
        {actions ? <div className="floating-bar compare-actions">{actions}</div> : null}
      </div>
    </div>
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}
