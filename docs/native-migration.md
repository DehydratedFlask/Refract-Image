# Native SwiftUI migration handoff

## Delivery

The desktop entry point now hosts SwiftUI, not WKWebView. Native screens cover Compose, Library,
Projects, Avatars, Models, and Settings. The React tree is retained as an optional legacy browser
interface; existing local React changes were left untouched.

Build output: [`Refract Native.app`](../native/build/Refract%20Native.app) (local, ignored by Git).
Quit any older running Refract Image instance before opening it: the app intentionally permits
only one normal instance to prevent two inference services competing for unified memory.

```bash
open 'native/build/Refract Native.app'
```

To rebuild the standard bundle later, run `./scripts/make-swift-app.sh` after quitting it.
No Node.js build, WebKit linkage, static UI server, or React assets are required by the native app.
The Python MLX/mflux backend remains local and owned by the Swift application.

## Requested behaviors

- Machine panel is outside the sidebar scroll view and remains visible across destinations.
- Compose controls and image canvas are a native resizable split view.
- Generate and output destination remain fixed below the form's scrolling content.
- Elapsed time ticks from the real job start timestamp; denoising ETA counts down between
  measurements and recalibrates on completed steps. Unknown phases and overruns are labelled.
- Previews decode every completed step by default, with first/last previews for slower strides.
  Both Qwen and FLUX use their own latent layouts. Actual model steps are not interpolated.
- Closing the last window or quitting stops the backend. It owns a separate worker process group;
  stragglers are force-stopped after a bounded wait. An early parent watchdog handles unexpected
  app death. Native mode starts only inference, not the legacy UI server.
- First-run installer shutdown is now also tied to application termination.

## Verification

- Native Swift build targets arm64 macOS 13 and compiles without warnings.
- Native smoke checks press the actual Generate and navigation accessibility controls, receive
  multiple readable preview frames, verify ticking timing, reopen saved sessions, persist an
  avatar profile, exercise queue cancellation, and check native clipboard support.
- The fixed status panel's accessibility frame is within window bounds at 980×620 and 1280×850.
- Actual native view captures are saved under `assets/qa/native/` (ignored QA artifacts), covering
  live generation plus the five destinations in light/dark appearance.
- Lifecycle integration tests close a busy native window and forcibly kill the app, then verify
  no owned backend service remains. Unrelated already-running applications are left alone.
- Backend suite: 163 tests passed. One pre-existing Starlette/httpx deprecation warning remains.
- Legacy TypeScript typecheck passed; shell syntax and Git whitespace checks passed.
- App signature verified; linked frameworks contain no WebKit and embedded payload contains no
  `app/dist` or `serve_app.py`.

## Verification boundaries

Live visual feedback is per completed denoising step: the model cannot supply a new latent before
that step finishes. Timing is continuous, but the progress bar represents only actual work.
Frequent full VAE decodes add inference time and temporary allocations; their cost is included
in the measured ETA. Existing saved sessions preserve their explicit preview stride.

Real-weight Qwen/FLUX end-to-end inference and performance were not run in this pass. Tests use
real latent-unpacking shapes with mocked VAE decode plus full mock backend/native UI integration.
Model download/prepare/custom install, system Save/Open panels, and notification permission flows
were implemented but not all exercised with production artifacts. First-run installer process
cleanup was inspected and compiled, not executed against a live runtime installation.

The new native preferences are separate from the former web UI's localStorage. Backend projects,
images, avatars, and model directories are reused; unsaved browser-only draft/settings do not
migrate automatically. No commit, push, deployment, or release publication was performed.
