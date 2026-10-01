/**
 * Everything that needs a native host, with a browser fallback for each call.
 *
 * There is one host — the Swift app in `native/`, reached through `lib/host.ts` — and each call
 * below either goes to it or does the browser thing. The fallbacks are not decoration: they are
 * what lets the exact same UI run under `vite` for development and visual QA. Where a browser
 * genuinely cannot do the same thing — there are no filesystem paths for a dropped file — the
 * file is uploaded to the local service instead, so the pipeline still runs end to end.
 */

import { hostInvoke, isNativeHost, onHostEvent } from "./host";
import { fileUrl, setConnection, uploadFiles } from "./api";

export const IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "webp", "tif", "tiff", "bmp", "heic"];

/**
 * True when the app is running inside its native window.
 *
 * This is the question the UI asks — "can this build hand me a real path, or open things in
 * Finder?" — and every control whose answer is no is disabled rather than hidden, so the screen
 * does not rearrange itself depending on where it happens to be running.
 */
export function hasNativeHost(): boolean {
  return isNativeHost();
}

export async function backendInfo(): Promise<Record<string, unknown> | null> {
  if (!isNativeHost()) return null;
  try {
    return await hostInvoke<Record<string, unknown>>("backend_info");
  } catch {
    return null;
  }
}

export async function restartBackend(): Promise<boolean> {
  if (!isNativeHost()) return false;
  try {
    await hostInvoke("restart_backend");
    setConnection(null);
    return true;
  } catch {
    return false;
  }
}

export async function appInfo(): Promise<{
  runtime: string;
  version: string;
  mock: string;
  data_root: string;
  logs?: string;
  shell?: string;
  /** What the native first-run setup recorded: the chosen model, or null if never asked. */
  first_run_model?: string | null;
  runtime_root?: string | null;
  program_root?: string | null;
} | null> {
  if (!isNativeHost()) return null;
  try {
    return await hostInvoke("app_info");
  } catch {
    return null;
  }
}

/** Native open panel in the app; a file input + upload in the browser. */
export async function pickImages(): Promise<string[]> {
  if (isNativeHost()) {
    try {
      const picked = await hostInvoke<string[] | null>("pick_images", { title: "Choose reference images" });
      return picked ?? [];
    } catch {
      return [];
    }
  }
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.multiple = true;
    input.onchange = async () => {
      const files = Array.from(input.files ?? []);
      resolve(files.length ? await uploadFiles(files) : []);
    };
    input.click();
  });
}

export async function pickDirectory(title = "Choose a folder"): Promise<string | null> {
  if (!isNativeHost()) return null;
  try {
    return await hostInvoke<string | null>("pick_directory", { title });
  } catch {
    return null;
  }
}

export async function pickFile(title = "Choose a file", extensions: string[] = []): Promise<string | null> {
  if (!isNativeHost()) return null;
  try {
    return await hostInvoke<string | null>("pick_file", { title, extensions });
  } catch {
    return null;
  }
}

export async function revealInFinder(path: string): Promise<void> {
  if (!path) return;
  if (isNativeHost()) {
    await hostInvoke("reveal_in_finder", { path });
    return;
  }
  // Browsers cannot reveal a file; downloading it is the closest useful action.
  const link = document.createElement("a");
  link.href = await fileUrl(path);
  link.download = path.split("/").pop() ?? "image.png";
  link.click();
}

export async function openPath(path: string): Promise<void> {
  if (!path) return;
  if (isNativeHost()) {
    await hostInvoke("open_path", { path });
    return;
  }
  window.open(await fileUrl(path), "_blank");
}

/** Save a generated image somewhere the user chooses; falls back to a download. */
export async function saveImageAs(source: string, suggestedName?: string): Promise<string | null> {
  if (!isNativeHost()) {
    await revealInFinder(source);
    return null;
  }
  const name = suggestedName ?? source.split("/").pop() ?? "image.png";
  try {
    const target = await hostInvoke<string | null>("save_as", {
      source,
      defaultPath: name,
      extensions: ["png"],
    });
    if (!target) return null;
    await hostInvoke("copy_file", { source, target });
    return target;
  } catch {
    return null;
  }
}

export async function copyToClipboard(source: string): Promise<boolean> {
  if (isNativeHost()) {
    try {
      return await hostInvoke<boolean>("copy_image", { source });
    } catch {
      return false;
    }
  }
  try {
    const response = await fetch(await fileUrl(source));
    const blob = await response.blob();
    if (navigator.clipboard && "write" in navigator.clipboard) {
      const Item = (window as unknown as { ClipboardItem: typeof ClipboardItem }).ClipboardItem;
      await navigator.clipboard.write([new Item({ [blob.type]: blob })]);
      return true;
    }
  } catch {
    /* fall through */
  }
  return false;
}

/**
 * Reference-image drops from Finder (native) or from the browser (uploaded).
 * `onStatus` drives the drop-zone highlight, which the design skill asks for explicitly.
 *
 * The Swift app intercepts drops at the window, which is the only way to learn where the file
 * lives. WebKit may instead deliver the drop into the page, where a file arrives without a path,
 * so both listeners are installed and the DOM one steps aside when the native one just fired.
 */
let lastNativeDrop = 0;

export function onFileDrop(handler: (paths: string[]) => void, onStatus?: (active: boolean) => void): () => void {
  let offNativeDrop: () => void = () => undefined;
  let offNativeState: () => void = () => undefined;

  if (isNativeHost()) {
    offNativeDrop = onHostEvent<string[]>("drag-drop", (paths) => {
      lastNativeDrop = Date.now();
      onStatus?.(false);
      if (paths?.length) handler(paths);
    });
    offNativeState = onHostEvent<boolean>("drag-state", (active) => onStatus?.(!!active));
  }

  const onOver = (event: DragEvent) => {
    event.preventDefault();
    if (event.dataTransfer?.types?.includes("Files")) onStatus?.(true);
  };
  const onLeave = () => onStatus?.(false);
  const onDrop = async (event: DragEvent) => {
    event.preventDefault();
    onStatus?.(false);
    if (Date.now() - lastNativeDrop < 750) return;
    const files = Array.from(event.dataTransfer?.files ?? []);
    if (files.length) handler(await uploadFiles(files));
  };
  window.addEventListener("dragover", onOver);
  window.addEventListener("dragleave", onLeave);
  window.addEventListener("drop", onDrop);

  return () => {
    offNativeDrop();
    offNativeState();
    window.removeEventListener("dragover", onOver);
    window.removeEventListener("dragleave", onLeave);
    window.removeEventListener("drop", onDrop);
  };
}

/** Menu-bar commands, forwarded by the app as `menu`. */
export function onMenuCommand(handler: (command: string) => void): () => void {
  if (!isNativeHost()) return () => undefined;
  return onHostEvent<string>("menu", (command) => handler(command));
}

export function onBackendReady(handler: (info: Record<string, unknown>) => void): () => void {
  if (!isNativeHost()) return () => undefined;
  return onHostEvent<Record<string, unknown>>("backend-ready", (info) => handler(info));
}

/**
 * Raise a system notification when a long job finishes.
 *
 * Worth having because a 40-step run outlives anyone's patience for watching a progress bar, and
 * once the app is in the background a toast is invisible. The app skips the banner when Refract Image is
 * already the front window, so this never duplicates a toast. There is deliberately no browser
 * branch: a page asking for notification permission on load is exactly the pattern people have
 * learned to click through, and the toast covers the case where they are watching.
 */
export async function notifyFinished(title: string, body: string): Promise<void> {
  if (isNativeHost()) {
    await hostInvoke("notify", { title, body }).catch(() => undefined);
  }
}

/**
 * Ask the machine not to idle out mid-generation.
 *
 * The app holds a power assertion while a job runs. In a browser the equivalent is the Wake Lock
 * API, which keeps the *display* awake; either way the alternative is a run that finishes in a
 * wake-from-sleep screen seven minutes too late.
 */
let wakeLock: { release: () => Promise<void> } | null = null;

export async function keepAwake(on: boolean): Promise<void> {
  if (isNativeHost()) {
    await hostInvoke("keep_awake", { on }).catch(() => undefined);
    return;
  }
  const navigatorWithWakeLock = navigator as Navigator & {
    wakeLock?: { request: (type: "screen") => Promise<{ release: () => Promise<void> }> };
  };
  if (!navigatorWithWakeLock.wakeLock) return;
  try {
    if (on && !wakeLock) wakeLock = await navigatorWithWakeLock.wakeLock.request("screen");
    if (!on && wakeLock) {
      await wakeLock.release();
      wakeLock = null;
    }
  } catch {
    /* the browser decided it knows better */
  }
}

/**
 * Make the top bar drag the window.
 *
 * The UI marks its chrome `data-window-drag`; the app cannot see the page's mouse events, so a
 * mousedown inside one of those elements asks the app to take over the drag. It has to fire on
 * mouse-*down*: a drag started from a click that is already over moves the window when the pointer
 * is released instead.
 */
export const WINDOW_DRAG_MARKER = "[data-window-drag]";

export function installWindowDragRegion(): () => void {
  if (!isNativeHost()) return () => undefined;
  const onDown = (event: MouseEvent) => {
    if (event.button !== 0) return;
    const target = event.target as HTMLElement | null;
    if (!target?.closest?.(WINDOW_DRAG_MARKER)) return;
    void hostInvoke("start_window_drag").catch(() => undefined);
  };
  window.addEventListener("mousedown", onDown, true);
  return () => window.removeEventListener("mousedown", onDown, true);
}
