# Design system

Refract Image is a native macOS app, not a web page in a window. This document records its
interface rules, where each one is implemented, and what was checked visually. Platform
decisions follow Apple's macOS Human Interface Guidelines, with product-specific exceptions
called out in [Deliberate deviations](#deliberate-deviations).

## The layout formula

```
┌──────────────────────────────────────────────────────────┐
│ ● ● ●   Refract Image   [Compose|Library|Models]   …   ⚙   ⌘⏎ │  ← 50px, draggable
├───────────────┬──────────────────────────────────────────┤
│               │                                          │
│   Sidebar     │              Content                     │
│   240px       │        (compose / library / models)      │
│   vibrancy    │                                          │
│               │                                          │
└───────────────┴──────────────────────────────────────────┘
```

- The window asks macOS for `.fullSizeContentView` with `titlebarAppearsTransparent` and
  `titleVisibility = .hidden` ([Browser.swift](../native/Sources/Browser.swift)), so the real
  traffic lights are drawn over the top bar and the content runs underneath them. A 68px
  `.traffic-lights` spacer ([global.css](../app/src/styles/global.css)) keeps the title clear of
  them — the buttons are integrated into the bar rather than floated above the UI.
- The whole top bar carries `data-window-drag`, and the title, segmented control and spacer
  repeat it, so the top ~50px moves the window. The page cannot move a window it does not own, so
  a mousedown inside one of those elements calls `start_window_drag` and the app runs
  `window.performDrag` with the event that is still being tracked
  ([ipc.ts](../app/src/lib/ipc.ts), [Browser.swift](../native/Sources/Browser.swift)).
- Navigation lives in the sidebar (240px) as labelled groups — `COMPOSE`, `LIBRARY`,
  `MODELS` — with the same three destinations mirrored in the top bar's segmented control for
  a one-click jump from any view. Row height is 30px, active rows use a soft fill rather
  than a saturated block (`--surface-hover` / `--surface-active`).
- The main content area is **opaque** (`--bg-primary`); blur stays in window chrome and panels.

## Checklist

| Interface rule | Implementation |
| --- | --- |
| Top bar for global actions, sidebar for navigation, content in the centre | [TopBar.tsx](../app/src/components/TopBar.tsx), [Sidebar.tsx](../app/src/components/Sidebar.tsx), view switch in [App.tsx](../app/src/App.tsx) |
| Traffic lights integrated into the UI | Overlay title bar + reserved 68px spacer; never overlapping controls |
| Top ~50px draggable | `data-window-drag` on the header and its non-interactive children |
| Empty states, progressive disclosure | `EmptyState` in [ui.tsx](../app/src/components/ui.tsx); the compose canvas only shows the compare panel once there is something to compare; the library's filter row appears only when items exist |
| Keyboard shortcut for every primary action, with visible hints | See [Shortcuts](#shortcuts); hints render as `<kbd>` chips next to the controls, and `⌘/` opens the cheat sheet |
| Light **and** dark, designed separately, never inverted | [tokens.css](../app/src/styles/tokens.css) — light keeps surfaces close, dark spreads three greys and denser shadows |
| Search prominent and accessible | Search field appears in the top bar in Library view and focuses on `⌘F`; filters the grid live |
| Drag and drop in and out | Files dropped anywhere in the window are added as references; results and library items are draggable out to Finder |
| Micro-animation on every state change | Shared `--duration-*` / `--ease-*` tokens applied to hover, selection, panels, toasts |
| Brief onboarding that teaches by doing | [OnboardingSheet](../app/src/components/Sheets.tsx) offers the first download and names the shortcut that dismisses it |

## Colour, type and depth

Three visual rules shape the token file:

1. **Light and dark are separate palettes.** Light keeps its four surfaces within a few
   percent of each other (`#ffffff` → `#f5f5f7` → `#e8e8ed`); dark pulls them apart
   (`#1c1c1e` → `#2c2c2e` → `#3a3a3c`) and never uses pure black. The accent also changes:
   `#007aff` in light, the brighter `#0a84ff` in dark.
2. **Depth comes from layered shadows**, and every shadow starts with the
   `0 0 0 0.5px` edge that gives macOS its hairline definition — there are no thick borders
   anywhere in the app.
3. **Vibrancy is reserved for chrome.** The sidebar, top bar and floating panels use
   `backdrop-filter: saturate(180%) blur(20px)` over a ~72% translucent fill; content,
   inputs and images stay solid.

Type is the macOS scale, 13px body and up, in the `-apple-system` stack, with `SF Mono` for
paths and seeds. Spacing is on the 8px grid (`--space-1` … `--space-10`), and radii stay
consistent: 10px window, 12px panel, 8px card, 6px button/input, 4px tag.

## Shortcuts

| Shortcut | Action |
| --- | --- |
| `⌘⏎` | Generate |
| `Esc` | Cancel the running job / dismiss a sheet |
| `⌘N` | New generation |
| `⌘F` | Focus library search |
| `⌘1` / `⌘2` / `⌘3` | Compose / Library / Models |
| `⌘,` | Settings |
| `⌘/` | Shortcut cheat sheet |
| `⌘⇧R` | Reveal the output in Finder |
| `⌘⇧S` | Save the result as… |

`⌘S`, `⌘A` and the other editing keys are left to the webview so text fields behave normally.

## Interaction details

- **Compare, not just display.** The result sits beside the reference images
  ([CompareView.tsx](../app/src/features/compare/CompareView.tsx)) with a *Side by side* /
  *Wipe* toggle: side by side pairs each reference with the output, and wipe overlays them so
  a drag of the divider reveals how much of the reference survived. Zoom and pan are
  pointer-driven, so the same gesture works on a trackpad.
- **Optimistic feedback.** Saving, revealing and favouriting act immediately and confirm with
  a toast that slides up and auto-dismisses; the same pattern shows job progress so a long
  run never looks frozen.
- **Progressive disclosure.** Advanced sampler settings (steps, size, guidance, seed) stay
  behind a disclosure; the reference strip explains itself only when empty.
- **The destination is named before the run.** The compose bar shows the folder the image
  will be written to, with *Choose…* and *Default* beside it, because a five-minute run that
  ends up somewhere unexpected is a five-minute run wasted. That one setting drives the
  Library footer and both *Open outputs folder* buttons, so no part of the app can disagree
  about where the images are. It is deliberately not repeated in the Advanced panel.
- **Transparency about the runner.** When the mock runner is active the app says so in the
  banner *and* the top bar, rather than presenting placeholders as results.

## Deliberate deviations

- **The sidebar is always visible.** With three destinations plus the library filters it carries
  real state; the 1080px minimum window width keeps it from crowding the canvas.
- **No `⌘S` quick-save.** A generation writes its output to disk automatically, so there is
  nothing to save. `⌘⇧S` exports a copy, which is the only case that needs a dialog.
- **Sheets instead of popovers.** Settings, the cheat sheet and onboarding are centred sheets
  ([Sheets.tsx](../app/src/components/Sheets.tsx)) because they are read at length; popovers
  are reserved for transient menus.

## Reviewing the interface without the model

The built UI is bundled into one self-contained file, which is what the screenshots in the QA
pass are taken from:

```bash
cd app && npm run build
../.runtime/bin/python scripts/make-preview.py   # writes assets/ui-preview.html
```

Opening [`assets/ui-preview.html`](../assets/ui-preview.html) serves the whole interface from
the placeholder dataset (`mock: true`) — no Python service, no weights — which is how the
light and dark, empty and populated states of Compose, Library and Models were checked side by
side. The in-app **Settings → Appearance** switch does the same thing live, and persists the
choice.

## QA pass

Every screen was reviewed in both appearances at 1280×860 and at the 1080px minimum window
width. The pass caught four things, all fixed rather than papered over:

| Symptom | Cause | Fix |
| --- | --- | --- |
| The favourite star on a Library tile was an invisible white square in dark mode | the tile reused `className="remove"`, which is only styled inside `.thumb` | gave the toggle its own `.grid-card .fav` chip, gold when on, with a `--favorite` token per appearance |
| The top bar wrapped to two lines ("mock / runner") and, once fixed, clipped the badge | the status readout had no white-space or shrink rules | added `.topbar-status`: only the model name shrinks and ellipsises, and below 1200px the name and disk figure step aside for the search field |
| The compose bar promised "≈13 GB peak RAM" for a run whose own comment records ~15.6 GB | the estimator's base was 7.0 GB, unrelated to the measurement it cited | re-anchored it on the measured 512px run, so the default 1024×1024 now reads ≈19 GB |
| The demo's "Prepared local model" pointed at `~/Library/Application Support/Refract/...` | stale placeholder data, and its size contradicted its own installed entry | pointed it at the resolved external root and gave the demo coherent sizes, durations and peaks |
| The output folder could be chosen but only Settings knew: *Open outputs folder* and the Library footer still named the default | three separate copies of one setting, one of them stale | one destination control on the compose bar and in Settings; the Library and sidebar follow it, and the Advanced panel's duplicate was removed |

The release app embeds its interface and first-run setup payload, then stores its runtime and
model data separately under the locations selected in the setup window.
