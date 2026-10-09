import AppKit
import SwiftUI

@MainActor
final class NativeWindow {
    let store = NativeStore()
    private(set) var window: NSWindow?
    private var keyMonitor: Any?

    func show() {
        if window == nil {
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 850), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
            window.title = "Refract Image"
            window.minSize = NSSize(width: 980, height: 620)
            window.titlebarAppearsTransparent = true
            window.toolbarStyle = .unified
            window.isReleasedWhenClosed = false
            window.setFrameAutosaveName("RefractNativeWorkspace")
            window.contentViewController = NSHostingController(rootView: NativeRootView(store: store))
            window.center()
            self.window = window
            keyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                guard let self, event.keyCode == 53, !self.store.settingsOpen, !self.store.shortcutsOpen, self.window?.attachedSheet == nil else { return event }
                if self.store.viewingImage != nil { self.store.viewingImage = nil; return nil }
                if self.store.currentJob != nil { self.store.menu("cancel"); return nil }
                return event
            }
        }
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func stop() {
        store.stop()
        if let keyMonitor { NSEvent.removeMonitor(keyMonitor); self.keyMonitor = nil }
    }

    /// Uses the same store commands and hosted views as user interactions, not a web document.
    /// Mock mode additionally verifies streamed preview files, timing between events, queueing,
    /// session persistence, all native screens, and clipboard/image export.
    func smoke() async -> [String] {
        var problems: [String] = []
        func check(_ condition: Bool, _ message: String) { if !condition { problems.append(message) } }
        check(window?.contentViewController is NSHostingController<NativeRootView>, "window is not a native SwiftUI host")
        check(store.ready, "native store did not connect")
        check(store.health.bool("ok"), "backend health failed")
        guard store.health.bool("mock") else { return problems }
        // Enable the same accessibility tree that macOS requests for assistive clients.
        // Without a connected client SwiftUI may leave the hosting view's AX children lazy.
        NSApp.accessibilitySetValue(true, forAttribute: NSAccessibility.Attribute(rawValue: "AXEnhancedUserInterface"))
        do {
            try await store.createProject(name: "Native smoke")
            let referenceURL = FileManager.default.temporaryDirectory.appendingPathComponent("refract-smoke-\(UUID().uuidString).png")
            defer { try? FileManager.default.removeItem(at: referenceURL) }
            let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 32, pixelsHigh: 32, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
            try bitmap.representation(using: .png, properties: [:])!.write(to: referenceURL)
            store.addReferences([referenceURL.path])
            store.draft.prompt = "a native smoke lighthouse"
            store.draft.width = 256; store.draft.height = 256
            store.draft.steps = 6; store.draft.preview_interval = 1
            // SwiftUI publishes accessibility lazily after the newly populated reference grid
            // lays out. Wait for the real button rather than assuming a single 200 ms frame.
            try await Task.sleep(nanoseconds: 300_000_000)
            let controlDeadline = Date().addingTimeInterval(5)
            while let root = window?.contentView, accessibilityMatches(root, identifier: "generate-image").isEmpty, Date() < controlDeadline {
                root.layoutSubtreeIfNeeded()
                try await Task.sleep(nanoseconds: 100_000_000)
            }
            guard let root = window?.contentView,
                  let generate = accessibilityMatches(root, identifier: "generate-image").first as? NSObject,
                  generate.responds(to: NSSelectorFromString("accessibilityPerformPress")) else { throw NativeError("Native Generate control is not accessible: \(accessibilitySummary(window?.contentView))") }
            _ = generate.perform(NSSelectorFromString("accessibilityPerformPress"))
            let submissionDeadline = Date().addingTimeInterval(10)
            while store.currentJob == nil && Date() < submissionDeadline { try await Task.sleep(nanoseconds: 50_000_000) }
            guard let jobID = store.currentJob?.id else { throw NativeError("Generate control submitted no job: \(store.error ?? "unknown error")") }
            let deadline = Date().addingTimeInterval(30)
            var previewCount = 0
            var previousPreview = ""
            var ticked = false
            var testedETA = false
            while Date() < deadline {
                guard let job = store.jobs.first(where: { $0.id == jobID }) else { throw NativeError("Job disappeared") }
                let path = job.string("preview_path")
                if job.pending, !path.isEmpty, path != previousPreview {
                    check(NSImage(contentsOfFile: path) != nil, "streamed preview is unreadable")
                    previewCount += 1; previousPreview = path
                    let now = Date()
                    check(store.elapsed(job, now: now.addingTimeInterval(0.5)) > store.elapsed(job, now: now), "elapsed time does not tick between steps")
                    ticked = true
                    if previewCount == 2 { store.appearance = "light"; try capture("live-generation") }
                    if job.string("phase") == "denoise", job.number("step") < job.number("total_steps"), job.number("eta_seconds") > 2 {
                        check(store.remaining(job, now: now) != store.remaining(job, now: now.addingTimeInterval(1.1)), "remaining time does not tick between steps")
                        testedETA = true
                    }
                }
                if !job.pending {
                    check(job.string("status") == "done", "generation did not finish: \(job.string("error"))")
                    check(!job.strings("outputs").isEmpty, "generation produced no image")
                    if let path = job.strings("outputs").first {
                        check(NSImage(contentsOfFile: path) != nil, "final output is not an image")
                        NativeFiles.copy(path, store: store)
                        check(NSPasteboard.general.canReadObject(forClasses: [NSImage.self], options: nil), "native image clipboard failed")
                        try await store.refine(job)
                        check(store.draft.reference_paths == [path] && store.draft.prompt.isEmpty, "refinement lost its output reference")
                        store.draft.prompt = "make the lighthouse warmer"
                        try await store.saveProject()
                    }
                    break
                }
                try await Task.sleep(nanoseconds: 75_000_000)
            }
            check(previewCount >= 2, "fewer than two live preview frames arrived")
            check(ticked, "live timing was not exercised")
            check(testedETA, "live ETA was not exercised")
            check(store.jobs.first { $0.id == jobID }?.pending == false, "generation timed out")
            try await store.refreshCatalogs()
            check(store.library.contains { $0.id == jobID }, "output did not reach Library")
            if let id = store.draft.project_id {
                try await store.newSession()
                check(store.activeProject?.records("sessions").count == 3, "refinement and new session were not saved independently")
                try await store.openProject(id, session: "s1")
                check(store.draft.prompt == "a native smoke lighthouse", "refinement overwrote the original session")
                check(store.draft.steps == 6 && store.draft.preview_interval == 1 && store.draft.width == 256, "saved settings did not reopen")
                check(store.draft.reference_paths.count == 1 && NSImage(contentsOfFile: store.draft.reference_paths[0]) != nil, "saved reference did not reopen")
                let rememberedJobs = store.jobs, rememberedLibrary = store.library
                store.jobs = []; store.library = []
                try await store.openProject(id, session: "s1", saveCurrent: false)
                check(store.workspaceJob?.id == jobID, "old saved output requires a recent job/library record")
                store.jobs = rememberedJobs; store.library = rememberedLibrary
                let entries = store.activeProject!.records("sessions")
                let other = entries.last!.id
                store.draft.prompt = "edited before rapid navigation"
                let firstClick = Task { try await self.store.openProject(id, session: other) }
                await Task.yield()
                let lastClick = Task { try await self.store.openProject(id, session: "s1") }
                try await firstClick.value; try await lastClick.value
                check(store.draft.project_session_id == "s1" && store.draft.prompt == "edited before rapid navigation", "rapid session clicks restored stale work")
                store.page = .projects
                try await Task.sleep(nanoseconds: 200_000_000)
                try press("session-\(id)-\(other)")
                try await Task.sleep(nanoseconds: 200_000_000)
                check(store.draft.project_session_id == other && store.page == .compose, "session row did not restore its workspace")
                try await store.openProject(id, session: "s1")
                try await Task.sleep(nanoseconds: 200_000_000)
                // Same hosted image controls used in Compose and the Library viewer.
                if let viewport = findViewport(window?.contentView) {
                    let center = CGPoint(x: viewport.bounds.midX, y: viewport.bounds.midY)
                    viewport.changeZoom(2, anchor: center)
                    check(viewport.zoom == 2, "native zoom in failed")
                    viewport.changeZoom(0.5, anchor: center)
                    check(viewport.zoom == 0.5, "native zoom cannot go below Fit")
                    viewport.pan = CGPoint(x: 55, y: -32)
                    viewport.fitImage()
                    check(viewport.zoom == 1 && viewport.pan == .zero, "Fit did not reset pan and zoom")
                } else { problems.append("Compose image viewport is missing") }
                store.page = .library
                try await Task.sleep(nanoseconds: 250_000_000)
                let originalPrompt = store.draft.prompt
                try press("library-image-\(jobID)")
                try await Task.sleep(nanoseconds: 300_000_000)
                check(store.viewingImage?.id == jobID && store.page == .library && store.draft.prompt == originalPrompt, "Library click navigated to or changed the session prompt")
                if let sheetRoot = window?.attachedSheet?.contentView {
                    try press("image-zoom-in", root: sheetRoot)
                    try await Task.sleep(nanoseconds: 100_000_000)
                    check(findViewport(sheetRoot)?.zoom == 1.25, "viewer zoom-in control failed")
                    try press("image-zoom-out", root: sheetRoot)
                    try await Task.sleep(nanoseconds: 100_000_000)
                    check(findViewport(sheetRoot)?.zoom == 1, "viewer zoom-out control failed")
                    if let viewport = findViewport(sheetRoot),
                       let event = CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 2, wheel1: 12, wheel2: 6, wheel3: 0),
                       let wheel = NSEvent(cgEvent: event) {
                        viewport.scrollWheel(with: wheel)
                        check(viewport.zoom != 1 || viewport.pan != .zero, "scroll event did not reach native image interaction")
                    }
                    try press("image-fit", root: sheetRoot)
                    try await Task.sleep(nanoseconds: 100_000_000)
                    check(findViewport(sheetRoot)?.zoom == 1 && findViewport(sheetRoot)?.pan == .zero, "viewer Fit control failed")
                    try capture("library-viewer")
                    try press("close-image-viewer", root: sheetRoot)
                    try await Task.sleep(nanoseconds: 200_000_000)
                    check(store.viewingImage == nil && store.page == .library, "closing image viewer did not return to Library")
                } else { problems.append("Library did not present an image viewer sheet") }
            }
            // Exercise populated avatar persistence and native editor restoration.
            if let output = store.library.first(where: { $0.id == jobID })?.strings("outputs").first, let api = store.api {
                let avatar = try await api.request("/api/avatars", method: "POST", body: ["name": "Smoke subject", "handle": "smoke-subject", "description": "A lighthouse", "references": [output], "reference_roles": ["reference"]])
                try await store.refreshCatalogs()
                check(store.avatars.contains { $0.id == avatar.id && $0.strings("reference_roles") == ["reference"] }, "native avatar profile did not persist")
                let reopened = try NativeDraft.restore(["negative_prompt": NSNull(), "prompt": "legacy prompt", "preview_interval": 5])
                check(reopened.prompt == "legacy prompt" && reopened.preview_interval == 5, "legacy session restoration changed saved settings")
            }
            store.appearance = "light"
            for page in NativePage.allCases {
                if let root = window?.contentView, let button = accessibilityMatches(root, identifier: "nav-\(page.rawValue.lowercased())").first as? NSObject {
                    let press = NSSelectorFromString("accessibilityPerformPress")
                    check(button.responds(to: press), "\(page.rawValue) navigation is not accessible")
                    if button.responds(to: press) { _ = button.perform(press) }
                } else { problems.append("\(page.rawValue) navigation button is missing") }
                try await Task.sleep(nanoseconds: 250_000_000)
                window?.contentView?.layoutSubtreeIfNeeded()
                check(store.page == page, "\(page.rawValue) navigation did not open its screen")
                try capture("\(page.rawValue.lowercased())-light")
            }
            // Check the pinned panel at both native minimum size and a large window.
            for size in [NSSize(width: 980, height: 620), NSSize(width: 1280, height: 850)] {
                window?.setContentSize(size)
                try await Task.sleep(nanoseconds: 150_000_000)
                window?.contentView?.layoutSubtreeIfNeeded()
                if let root = window?.contentView {
                    let matches = accessibilityMatches(root, identifier: "pinned-machine-status")
                    check(!matches.isEmpty, "pinned status is missing from native accessibility hierarchy")
                    if let element = matches.first as? NSObject {
                        let frame = element.value(forKey: "accessibilityFrame") as? NSRect ?? .zero
                        check(frame.height > 0 && window?.frame.contains(frame) == true, "pinned machine status is clipped at \(size)")
                    }
                }
            }
            store.page = .compose
            store.appearance = "dark"
            try await Task.sleep(nanoseconds: 300_000_000)
            try capture("compose-dark")
            store.settingsOpen = true
            try await Task.sleep(nanoseconds: 300_000_000)
            store.settingsOpen = false
            for page in NativePage.allCases {
                store.page = page
                try await Task.sleep(nanoseconds: 150_000_000)
                try capture("\(page.rawValue.lowercased())-dark")
            }
            store.page = .compose
            // Queueing and cancellation use the same native commands as the controls.
            store.draft.prompt = "first queued smoke task"; store.draft.steps = 40
            try await store.generate()
            store.draft.prompt = "second queued smoke task"
            try await store.generate()
            check(store.pending.count == 2, "second task was not queued")
            store.page = .library
            try await Task.sleep(nanoseconds: 200_000_000)
            try press("open-queue")
            try await Task.sleep(nanoseconds: 300_000_000)
            check(store.queueOpen, "queue could not be opened outside Compose")
            if let sheetRoot = window?.attachedSheet?.contentView {
                for job in store.pending {
                    check(!accessibilityMatches(sheetRoot, identifier: "queue-task-\(job.id)").isEmpty, "task missing from queue panel")
                    check(!accessibilityMatches(sheetRoot, identifier: "queue-status-\(job.id)").isEmpty, "task status missing from queue panel")
                    if job.string("status") == "running" && job.number("total_steps") > 0 {
                        check(!accessibilityMatches(sheetRoot, identifier: "queue-progress-\(job.id)").isEmpty, "running task progress missing")
                    }
                }
                try capture("queue")
                if let waiting = store.pending.first(where: { $0.string("status") == "queued" }) {
                    try press("queue-cancel-\(waiting.id)", root: sheetRoot)
                    let removeDeadline = Date().addingTimeInterval(5)
                    while store.pending.contains(where: { $0.id == waiting.id }) && Date() < removeDeadline { try await Task.sleep(nanoseconds: 100_000_000) }
                    check(!store.pending.contains { $0.id == waiting.id }, "queue Remove did not cancel waiting task")
                }
                try press("close-queue", root: sheetRoot)
                try await Task.sleep(nanoseconds: 200_000_000)
                check(!store.queueOpen, "queue Done did not dismiss panel")
            } else { problems.append("queue sheet was not presented") }
            for job in store.pending { try await store.cancel(job) }
            let cancellationDeadline = Date().addingTimeInterval(15)
            while !store.pending.isEmpty && Date() < cancellationDeadline { try await Task.sleep(nanoseconds: 100_000_000) }
            check(store.pending.isEmpty, "native cancellation did not drain the queue")
            NSLog("[smoke] native window, \(previewCount) live frames, ticking ETA, persisted sessions, clipboard, five screens")
        } catch { problems.append(error.localizedDescription) }
        return problems
    }

    private func accessibilitySummary(_ node: Any?, depth: Int = 0) -> String {
        guard depth < 12, let element = node as? NSObject else { return "" }
        let id = NSSelectorFromString("accessibilityIdentifier"), children = NSSelectorFromString("accessibilityChildren")
        var text = String(describing: type(of: element))
        if element.responds(to: id) { text += ":" + ((element.perform(id)?.takeUnretainedValue() as? String) ?? "-") }
        if element.responds(to: children), let nodes = element.perform(children)?.takeUnretainedValue() as? [Any] {
            text += "[" + nodes.map { accessibilitySummary($0, depth: depth + 1) }.joined(separator: ",") + "]"
        }
        return text
    }

    private func press(_ identifier: String, root: NSView? = nil) throws {
        guard let root = root ?? window?.contentView,
              let control = accessibilityMatches(root, identifier: identifier).first as? NSObject,
              control.responds(to: NSSelectorFromString("accessibilityPerformPress")) else {
            throw NativeError("Native control missing: \(identifier)")
        }
        _ = control.perform(NSSelectorFromString("accessibilityPerformPress"))
    }

    private func findViewport(_ view: NSView?) -> NativeViewportView? {
        guard let view else { return nil }
        if let viewport = view as? NativeViewportView { return viewport }
        for child in view.subviews { if let found = findViewport(child) { return found } }
        return nil
    }

    private func capture(_ name: String) throws {
        guard let directory = HostPaths.environmentValue("REFRACT_QA_DIR"), let view = window?.attachedSheet?.contentView ?? window?.contentView else { return }
        view.layoutSubtreeIfNeeded()
        guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { throw NativeError("Could not capture native view") }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        guard let data = bitmap.representation(using: .png, properties: [:]) else { throw NativeError("Could not encode native screenshot") }
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: true)
        try data.write(to: URL(fileURLWithPath: directory).appendingPathComponent(name + ".png"), options: .atomic)
    }

    private func accessibilityMatches(_ node: Any, identifier: String, depth: Int = 0) -> [Any] {
        guard depth < 40, let element = node as? NSObject else { return [] }
        let idSelector = NSSelectorFromString("accessibilityIdentifier")
        let childrenSelector = NSSelectorFromString("accessibilityChildren")
        let found = element.responds(to: idSelector) ? element.perform(idSelector)?.takeUnretainedValue() as? String : nil
        var matches: [Any] = found == identifier ? [node] : []
        if element.responds(to: childrenSelector), let children = element.perform(childrenSelector)?.takeUnretainedValue() as? [Any] {
            for child in children { matches += accessibilityMatches(child, identifier: identifier, depth: depth + 1) }
        }
        return matches
    }
}
