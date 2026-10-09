<div align="center">
  <img src="assets/app-icon.png" width="128" alt="Refract Image local AI image editing on macOS app icon" />
  <h1>Refract Image</h1>
  <p><strong>Reference-guided AI image editing, running on your Mac.</strong></p>
  <p>Refract Image is a macOS app for local AI image editing on Apple silicon. Add up to ten reference images, describe the change, then compare the result with the source.</p>
  <p>
    <a href="https://github.com/DehydratedFlask/Refract-Image/releases/latest"><img src="https://img.shields.io/badge/Download-macOS%20DMG-2563eb?style=for-the-badge" alt="Download Refract Image for macOS" /></a>
  </p>
  <p>
    <a href="https://github.com/DehydratedFlask/Refract-Image/releases/latest"><img src="https://img.shields.io/github/v/release/DehydratedFlask/Refract-Image?label=latest%20release" alt="Latest Refract Image release" /></a>
    <img src="https://img.shields.io/badge/macOS-13%2B-111827" alt="Requires macOS 13 or later" />
    <img src="https://img.shields.io/badge/Apple%20Silicon-required-111827" alt="Requires Apple silicon" />
    <img src="https://img.shields.io/badge/inference-local-15803d" alt="Local AI image editing on macOS" />
  </p>
</div>

---

## Why Refract Image?

Refract Image is for editing with a visual reference without sending the generation request to a hosted AI editor. After the model is downloaded, generation runs locally through MLX; references, outputs, and model files stay in folders you choose.

| Workflow | Where generation runs | What to expect |
| --- | --- | --- |
| **Refract Image** | On your Apple silicon Mac | One-time runtime and model downloads; local storage and unified memory are required. |
| Hosted image editor | On the provider's service | Convenient setup; data handling and availability depend on that provider. |
| Raw model tooling | Locally, when configured | Direct access to model tools, with more setup and terminal work. |

## Features

- **Reference-guided edits:** use up to ten images to guide a prompt-based edit.
- **Side-by-side and wipe comparison:** inspect what changed against the original references.
- **A local image library:** browse generated results, revisit jobs, and organize work into projects.
- **Model choices:** start with Qwen-Image-2.1 or FLUX.2 Klein 4B; the model manager exposes additional local sources.
- **Storage you control:** choose where the runtime, model downloads, references, and generated images live.
- **A native Mac app:** use Finder file pickers, drag images in and results out, and keep work in a resizable desktop window.

## Download and install

1. Download the latest **Refract Image DMG** from [GitHub Releases](https://github.com/DehydratedFlask/Refract-Image/releases/latest).
2. Open the DMG and drag **Refract Image.app** to **Applications**.
3. Open the app, choose where to keep its runtime and model data, and install a model. The model is downloaded on first setup and is not bundled in the DMG.

The setup wizard uses [`uv`](https://docs.astral.sh/uv/) to prepare the Python runtime. Install it first if needed; with Homebrew, run `brew install uv`. Fresh setup resolves the latest compatible Python packages and downloads the selected model from its upstream default revision. The model is fetched separately after the runtime is ready. The initial runtime and model downloads need an internet connection.

> The app is ad-hoc signed and is not notarized. If macOS blocks the first launch, Control-click **Refract Image.app**, choose **Open**, then confirm in the dialog.

## Compatibility

| Requirement | Details |
| --- | --- |
| macOS | 13 or later |
| Processor | Apple silicon; the current build targets arm64 |
| Unified memory | 32 GB or more recommended; smaller configurations may be constrained by model and image size |
| Free disk space | Allow at least 30 GB for initial setup and model staging |
| Network | Required for the first runtime and model downloads; generation runs locally once setup is complete |

The model weights are downloaded from their upstream publishers and keep their own terms. Refract Image does not include model weights in the source repository or DMG.

## How it works

The entire desktop interface is native SwiftUI and AppKit: Compose, Library, Projects, Avatars, Models, and Settings. There is no web view or UI server in the desktop app. A single owned Python service runs inference through MLX and mflux over authenticated loopback on the same Mac.

Machine status stays pinned beneath the sidebar's scrolling navigation. Elapsed time and the estimated remaining denoising time tick between model steps; previews update after each completed step by default. Preview decoding adds work and can be reduced or disabled in generation settings. Closing the last window or quitting stops the backend and its worker process group; a parent watchdog also cleans up if the app crashes.

The repository keeps the product pieces separate:

| Directory | Contents |
| --- | --- |
| `app/` | Legacy React/TypeScript browser interface, retained for optional web development |
| `backend/` | Python service, model setup, storage, and backend source tests |
| `native/` | Native SwiftUI/AppKit interface, service lifecycle, and app icon |
| `scripts/` | Local setup, development, app-bundle, and DMG build scripts |
| `docs/` | Interface design notes |

## Build from source

Native builds require macOS, Apple silicon, Xcode Command Line Tools, and `uv`. Node.js is needed only for the optional legacy browser interface.

```bash
brew install uv
./scripts/bootstrap.sh
./scripts/make-swift-app.sh
./scripts/make-dmg.sh 0.0.3
```

The SwiftUI interface is compiled directly into the executable. The bundle embeds only the backend and first-run setup payload; Python runtime dependencies and model weights are installed separately. Native builds do not build or bundle React assets.

Run `./scripts/dev.sh` to rebuild and launch the native app, or add `--mock` for placeholder inference. The optional legacy web interface remains available with `cd app && npm ci` followed by `./scripts/dev.sh --web`.

To install backend test tools, run `uv pip install --python .runtime/bin/python -e 'backend[dev]'`. Run `.runtime/bin/python -m pytest backend/tests` for backend coverage. For native end-to-end checks without touching your normal data or preferences:

```bash
./scripts/make-swift-app.sh --out native/build --name 'Refract Native'
REFRACT_ROOT="$PWD/.runtime/native-qa" REFRACT_MOCK_STEP_SECONDS=2 \
  'native/build/Refract Native.app/Contents/MacOS/RefractImage' --mock --smoke
.runtime/bin/python scripts/test-native-lifecycle.py
```

Native smoke checks exercise actual SwiftUI navigation controls, streamed preview files, live timing, saved project sessions, queue cancellation, image clipboard actions, and the pinned status panel at minimum window size. Lifecycle checks verify that closing a busy window and unexpectedly terminating the app both stop the owned backend.

## FAQ

### Does Refract Image send my reference images to a cloud service?

No. The app sends generation requests to its local service on the same Mac. Model files are downloaded from upstream sources during setup; the image-generation call itself runs locally.

### Can I use Refract Image without an internet connection?

After the runtime and a model are installed, image generation runs locally. Internet access is needed to set up or download another model.

### Does Refract Image run on Intel Macs?

No. The app uses MLX and this release targets Apple silicon Macs.

### Are model files included in the download?

No. The DMG contains the app and setup payload. Choose a model in the app and it downloads separately; model publishers' terms apply to those files.

---

<div align="center">
  <strong>Start editing with Refract Image</strong><br />
  <a href="https://github.com/DehydratedFlask/Refract-Image/releases/latest">Download the latest macOS DMG</a>
</div>
