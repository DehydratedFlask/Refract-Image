import { useEffect, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { useStore } from "../store/useStore";
import { fileUrl } from "../lib/api";

/** Resolve an on-disk path to a URL the webview can load. */
export function useFileUrl(path: string | null | undefined): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    if (!path) {
      setUrl(null);
      return;
    }
    fileUrl(path)
      .then((value) => {
        if (!cancelled) setUrl(value);
      })
      .catch(() => {
        if (!cancelled) setUrl(null);
      });
    return () => {
      cancelled = true;
    };
  }, [path]);
  return url;
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "subtle" | "danger";
  size?: "default" | "large";
  loading?: boolean;
  icon?: ReactNode;
};

export function Button({
  variant = "default",
  size = "default",
  loading,
  icon,
  children,
  className = "",
  disabled,
  ...rest
}: ButtonProps) {
  const classes = ["btn", variant !== "default" ? variant : "", size === "large" ? "large" : "", className]
    .filter(Boolean)
    .join(" ");
  return (
    <button className={classes} disabled={disabled || loading} {...rest}>
      {loading ? <span className="spinner" /> : icon}
      {children}
    </button>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <span className="kbd">{children}</span>;
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented" role="tablist">
      {options.map((option) => (
        <button
          key={option.value}
          role="tab"
          aria-selected={value === option.value}
          className={value === option.value ? "active" : ""}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Switch({ on, onChange, label }: { on: boolean; onChange: (next: boolean) => void; label: string }) {
  return (
    <button
      className="switch"
      data-on={on}
      aria-pressed={on}
      aria-label={label}
      onClick={() => onChange(!on)}
      type="button"
    >
      <span />
    </button>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  // The label never wraps and the hint never pushes it around: label left, hint right,
  // truncated. Without this, longer hints re-flow the control underneath and a row of
  // fields stops lining up.
  return (
    <label className="col" style={{ gap: 6, flex: 1, minWidth: 0 }}>
      <span className="field-label">
        <span className="field-label-text">{label}</span>
        {hint ? <span className="field-label-hint">{hint}</span> : null}
      </span>
      {children}
    </label>
  );
}

export function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="row" style={{ justifyContent: "space-between", gap: 12 }}>
      <div className="col" style={{ gap: 1 }}>
        <span style={{ fontWeight: 500 }}>{label}</span>
        {hint ? <span className="caption muted">{hint}</span> : null}
      </div>
      {children}
    </div>
  );
}

export function Pill({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "ok" | "warn" | "bad" | "accent";
  children: ReactNode;
}) {
  return <span className={`pill ${tone === "neutral" ? "" : tone}`}>{children}</span>;
}

export function ProgressBar({ value }: { value: number }) {
  return (
    <div className="progress" role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100}>
      <div style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

export function EmptyState({
  glyph,
  title,
  body,
  action,
}: {
  glyph: string;
  title: string;
  body?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="glyph" aria-hidden>
        {glyph}
      </div>
      <div style={{ fontSize: "var(--text-title-3)", fontWeight: 600, color: "var(--text-primary)" }}>{title}</div>
      {body ? <div className="muted" style={{ maxWidth: 380 }}>{body}</div> : null}
      {action}
    </div>
  );
}

export function Disclosure({
  title,
  subtitle,
  open,
  onToggle,
  children,
}: {
  title: string;
  subtitle?: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <div className="col">
      <button
        className="sidebar-row"
        onClick={onToggle}
        aria-expanded={open}
        style={{ justifyContent: "space-between" }}
        type="button"
      >
        <span className="row" style={{ gap: 6 }}>
          <span className="faint" style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform 150ms var(--ease-out)" }}>
            ▸
          </span>
          <span style={{ fontWeight: 500 }}>{title}</span>
        </span>
        {subtitle ? <span className="caption faint">{subtitle}</span> : null}
      </button>
      {open ? <div className="col" style={{ gap: 12, padding: "4px 8px 4px" }}>{children}</div> : null}
    </div>
  );
}

export function Sheet({
  title,
  onClose,
  children,
  width,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  width?: number;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="sheet-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="sheet" style={width ? { width } : undefined} role="dialog" aria-label={title}>
        <div className="sheet-header">
          <div style={{ fontWeight: 600, fontSize: "var(--text-title-3)" }}>{title}</div>
          <div className="grow" />
          <Button variant="subtle" onClick={onClose} aria-label="Close">
            ✕
          </Button>
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>
  );
}

/**
 * Two-step destructive action: the first click asks, the second performs. Avoids a modal
 * for something reversible and keeps the confirmation attached to what it affects.
 */
export function ConfirmButton({
  label,
  confirmLabel = "Confirm?",
  onConfirm,
  title,
}: {
  label: ReactNode;
  confirmLabel?: string;
  onConfirm: () => void;
  title?: string;
}) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const timer = window.setTimeout(() => setArmed(false), 4000);
    return () => window.clearTimeout(timer);
  }, [armed]);
  return (
    <Button
      variant="subtle"
      className={armed ? "danger" : ""}
      title={title}
      style={armed ? { color: "var(--danger)", fontWeight: 600 } : undefined}
      onClick={() => {
        if (armed) {
          setArmed(false);
          onConfirm();
        } else {
          setArmed(true);
        }
      }}
    >
      {armed ? confirmLabel : label}
    </Button>
  );
}

export function ToastHost() {
  const toasts = useStore((state) => state.toasts);
  const dismiss = useStore((state) => state.dismissToast);
  if (!toasts.length) return null;
  return (
    <div className="toast-host">
      {toasts.map((toast) => (
        <button
          key={toast.id}
          className="toast"
          onClick={() => dismiss(toast.id)}
          style={{ borderColor: toast.tone === "error" ? "var(--danger)" : undefined }}
          type="button"
        >
          <span aria-hidden>{toast.tone === "error" ? "⚠️" : toast.tone === "success" ? "✓" : "•"}</span>
          <span>{toast.message}</span>
        </button>
      ))}
    </div>
  );
}
