# Changelog

## 0.0.3 — 2026-10-06

- Replace the desktop web interface with native SwiftUI/AppKit workspaces.
- Restore saved references, prompts, settings, and results directly from session rows.
- Improve rapid session navigation and preserve the original session when refining.
- Expand Library images in a separate viewer without changing the working prompt.
- Add trackpad zoom, panning, zoom-out, and Fit/reset controls to image viewers.
- Add a global task queue with per-task progress, timing, waiting positions, and cancellation.
- Keep native service ownership and cleanup tied to the app/window lifetime.

## 0.0.2 — 2026-10-02

- Add avatar reference support and project generation sessions.

## 0.0.1 — 2026-10-01

First public release of Refract Image.

- Native macOS app for local, reference-guided image editing on Apple silicon.
- Compose, compare, projects, library, and model management views.
- First-run setup for the local Python runtime and model storage.
- Compressed DMG installer for macOS 13 or later.
