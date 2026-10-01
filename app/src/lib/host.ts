/**
 * Client for the native Swift shell (`native/`).
 *
 * The page normally runs inside a `WKWebView` in a real app, and the app can do what a tab cannot:
 * hand over the *path* of a file you picked or dropped from Finder, reveal a result in Finder, own
 * the menu bar, raise a notification when a long job finishes, and keep the machine from sleeping
 * mid-denoise.
 *
 * With no host present — plain `vite dev` in a browser tab — every call reports "no host" and each
 * caller keeps its browser fallback, which is what keeps visual QA possible without the app open.
 */

export interface HostBridge {
  host: string;
  invoke<T = unknown>(command: string, args?: Record<string, unknown>): Promise<T>;
  on(name: string, handler: (detail: unknown) => void): () => void;
}

declare global {
  interface Window {
    __refract?: HostBridge;
  }
}

export function isNativeHost(): boolean {
  if (typeof window === "undefined") return false;
  const bridge = window.__refract;
  return !!bridge && typeof bridge.invoke === "function" && typeof bridge.on === "function";
}

/**
 * Ask the app to do something. Rejects with the app's own explanation when it could not, which is
 * deliberately a sentence rather than a code — the callers show it or fall back, never branch on it.
 */
export async function hostInvoke<T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  const bridge = window.__refract;
  if (!bridge) throw new Error(`Refract Image host bridge is not available (command: ${command})`);
  return (await bridge.invoke<T>(command, args)) as T;
}

/** Host-originated events: `menu`, `backend-ready`, `backend-error`, `drag-drop`, `drag-state`. */
export function onHostEvent<T>(name: string, handler: (detail: T) => void): () => void {
  const bridge = window.__refract;
  if (!bridge) return () => undefined;
  return bridge.on(name, (detail) => handler(detail as T));
}
