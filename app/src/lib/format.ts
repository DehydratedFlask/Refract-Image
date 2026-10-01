export function humanBytes(value: number | null | undefined): string {
  if (!value || value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

export function humanDuration(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest.toString().padStart(2, "0")}s`;
}

export function relativeTime(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return "";
  const delta = Date.now() / 1000 - epochSeconds;
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)} min ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)} h ago`;
  const days = Math.floor(delta / 86400);
  if (days < 7) return `${days} d ago`;
  return new Date(epochSeconds * 1000).toLocaleDateString();
}

export function baseName(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

export function dirName(path: string): string {
  const parts = path.split(/[\\/]/);
  parts.pop();
  return parts.join("/");
}

export function phaseLabel(phase: string): string {
  const labels: Record<string, string> = {
    queued: "Queued",
    resolving: "Starting",
    downloading: "Downloading",
    loading: "Loading model",
    encoding: "Reading prompt and references",
    denoise: "Denoising",
    decoding: "Decoding",
    saving: "Saving",
    installing: "Installing",
    validating: "Validating",
    done: "Done",
    cancelled: "Cancelled",
    failed: "Failed",
  };
  return labels[phase] ?? phase;
}

export function percent(step: number, total: number): number {
  if (!total) return 0;
  return Math.max(0, Math.min(100, (step / total) * 100));
}

export function cloneWithout<T extends Record<string, unknown>>(source: T, keys: string[]): Partial<T> {
  const copy: Record<string, unknown> = { ...source };
  for (const key of keys) delete copy[key];
  return copy as Partial<T>;
}
