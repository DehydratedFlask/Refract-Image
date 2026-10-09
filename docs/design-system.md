# Native desktop design

Refract Image's desktop interface is SwiftUI hosted by AppKit. The desktop executable does not
compile the legacy [Browser.swift](../native/Sources/Browser.swift) or
[Bridge.swift](../native/Sources/Bridge.swift), does not render React, and does not start a UI server.
The optional browser interface is retained separately under `app/`.

## Workspace structure

- A 236-point material sidebar holds the five native destinations: Compose, Library, Projects,
  Avatars, and Models. Project and session navigation scroll independently of the machine panel.
- Machine status is a non-scrolling footer: MLX peak, installed memory, free disk, elapsed/last run,
  engine version, and chip. The active generation's phase and progress also remain visible here
  when switching screens.
- Compose uses a native resizable split view. Reference images, prompt, advanced settings, and
  queue occupy the form column; the Generate button and output destination are anchored outside
  its scroll view. The preview/comparison canvas receives the remaining space.
- The native title bar owns window dragging and traffic lights. No fake traffic-light spacers,
  HTML drag regions, or duplicate top-level navigation are needed.
- Default window content is 1280×850; minimum window size is 980×620. The minimum preserves
  the sidebar, controls, and a useful preview rather than collapsing status into hidden menus.

Implemented in [NativeViews.swift](../native/Sources/NativeViews.swift) and
[NativeWindow.swift](../native/Sources/NativeWindow.swift).

## Live progress

- `TimelineView` refreshes canvas timing every 250 ms and sidebar timing every second. Elapsed
  time uses the backend's actual job start timestamp, including model loading and encoding.
- Remaining denoising time counts down from the latest measured ETA, then recalibrates after
  each completed step. Before timing samples exist it says "Estimating"; when a step overruns
  the estimate it says "Updating estimate" rather than pretending the job has finished.
- Progress percentage represents actual completed steps. It is not artificially interpolated.
- Previews decode after every completed step by default. This is live *per-step* feedback, not
  a fabricated image between steps. Qwen and FLUX use their own installed latent unpackers and
  VAE paths; tiling follows the generation's decode policy.
- Completed-step progress is published before preview decoding, and ETA measurements include
  preview decode work. Three recent preview files are retained to accommodate lagging readers;
  job scratch space is removed at completion.
- The native image view loads file data off the main thread and retains the previous image
  until its replacement arrives. Preview files are versioned by step, avoiding stale cache hits.

State, authenticated streaming, reconnect/poll fallback, and durable sessions live in
[NativeStore.swift](../native/Sources/NativeStore.swift). Model hooks live in
[callbacks.py](../backend/refract_backend/callbacks.py).

## Visual language

Use system typography, semantic foreground colors, native blue tint, native controls, material
chrome, and opaque image canvases. macOS supplies light/dark control appearances and accessibility
behavior. Primary actions use prominent native buttons; secondary actions remain quiet. Spacing
is generous where prompts and images need attention; metadata uses small monospaced digits.

Advanced generation settings are progressively disclosed. Empty states explain the next useful
action instead of showing a blank workspace. Avatars expose reusable @handles and face/body roles.
Library tiles keep readable captions visible, with native context menus for image actions.

Results offer original aspect-ratio fitting, side-by-side comparison, a draggable wipe plus an
accessible slider, 2× zoom, Copy, Reveal, Save As, Refine, and Variation. Multiple references and
outputs can be selected. Image files drag out through native item providers.

## Lifecycle

Closing the last window terminates the app and invokes `Service.stop()`. The native host terminates
its owned inference process group, waits briefly, then force-stops stragglers. Python establishes
that group and starts its parent watchdog **before importing MLX**. If the app vanishes, the watchdog
kills its owned inference/download group even when GPU cancellation cannot unwind.

No UI server is started by the desktop app. Independently launched development services are not
owned by it and are deliberately left alone.

## Shortcuts

| Shortcut | Action |
| --- | --- |
| ⌘Return | Generate / add to queue |
| Escape / ⌘. | Cancel current generation |
| ⌘N | New generation session |
| ⌘P | New project |
| ⌘J | Cycle projects |
| ⌘1 / ⌘2 / ⌘3 / ⌘4 | Compose / Library / Projects / Models |
| ⌘, | Settings |
| ⌘/ | Shortcut sheet |
| ⇧⌘S | Save result as |
| ⇧⌘R | Reveal result in Finder |

Standard editing, undo, selection, and clipboard shortcuts use the native responder chain.

## Verification

The native `--mock --smoke` workflow checks actual hosted SwiftUI navigation through accessibility
press actions, streamed image readability, ticking timing between steps, durable session reopening,
clipboard image support, queue cancellation, and machine-panel bounds at two window sizes.
Screenshots are captured from the actual NSHostingController view when `REFRACT_QA_DIR` is supplied.
They are not web mockups. A separate [lifecycle test](../scripts/test-native-lifecycle.py) closes a
busy native window and kills a native app process, then asserts all its backend processes exit.

[Preview regression tests](../backend/tests/test_live_previews.py) exercise both families' real
latent layouts without downloading weights, the correct VAE calls, bounded frame retention,
preview disabling, cancellation, decode-inclusive ETA, and clearing stale ETA after denoising.

**Verification boundary:** mock/native integration and tensor-layout tests do not prove real
inference quality or performance for every installed model. A full production Qwen and FLUX run
is still needed to measure the extra cost of per-step VAE previews on the target Mac.
