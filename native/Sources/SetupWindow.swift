import AppKit

/// The first-run window: shown once, on a machine that has never been set up.
///
/// It has to exist before anything else can. The inference service is a Python program, so
/// there is no service, no UI and no API until the runtime is installed — the onboarding a new
/// user sees cannot be the web UI, because the web UI is what the runtime provides. That is
/// why this is native AppKit rather than a sheet.
///
/// It installs the runtime and nothing else. The model is fetched by the app afterwards,
/// through the download progress UI that already exists, rather than a second copy of it here.
///
/// The model list comes from `setup.json`, which the app build generates from the backend's
/// own source registry. It cannot come from the service, because there is no service until the
/// runtime this window is installing exists.
final class SetupWindow: NSObject, NSWindowDelegate {
    /// Free space below which a choice is refused rather than accepted and discovered later.
    private static let headroomGB = 8.0

    private var window: NSWindow!
    private var programRoot = Install.preferencesDirectory.appendingPathComponent("program", isDirectory: true)
    private let modelPopUp = NSPopUpButton(frame: .zero, pullsDown: false)
    private let dataButton = NSButton()
    private let runtimeButton = NSButton()
    private let dataLabel = NSTextField(labelWithString: "")
    private let runtimeLabel = NSTextField(labelWithString: "")
    private let detailLabel = NSTextField(wrappingLabelWithString: "")
    private let progress = NSProgressIndicator()
    private let logView = NSTextView()
    private var logScroll = NSScrollView()
    private let installButton = NSButton()
    private let cancelButton = NSButton()
    private let summaryLabel = NSTextField(labelWithString: "")

    private var dataRoot: URL
    private var runtimeRoot: URL
    private var choices: [ModelChoice] = []
    private var running = false

    private struct ModelChoice {
        let id: String
        let label: String
        let detail: String
        let approxBytes: Int64
    }

    /// Bytes the runtime install needs, from the generated catalog.
    private var runtimeBytes: Int64 = 3_500_000_000

    var onFinished: ((String?) -> Void)?

    // MARK: - defaults

    /// Sensible starting points: models on the internal disk, runtime beside the app.
    ///
    /// The runtime is ~1.1 GB and the models 13–14 GB, so they default to different places and
    /// the user is told as much. A machine with a large external drive is exactly the case this
    /// exists for, and the internal disk is rarely the right answer for either.
    private static var defaultDataRoot: URL {
        if let repo = HostPaths.repoRoot(), !HostPaths.isOnInternalDisk(repo) {
            return repo.appendingPathComponent(".refract", isDirectory: true)
        }
        return Install.preferencesDirectory
    }

    private static var defaultRuntimeRoot: URL {
        if let repo = HostPaths.repoRoot() {
            return repo.appendingPathComponent(".runtime", isDirectory: true)
        }
        return Install.preferencesDirectory.appendingPathComponent("runtime", isDirectory: true)
    }

    // MARK: - lifecycle

    override init() {
        dataRoot = Self.defaultDataRoot
        runtimeRoot = Self.defaultRuntimeRoot
        super.init()
    }

    /// Bring the window back if the user closed it without quitting.
func reopen() {
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    func show() {
        let content = NSView(frame: NSRect(x: 0, y: 0, width: 620, height: 560))
        content.wantsLayer = true

        build(in: content)
        if case .failure(let message) = Install.extractPayload(to: programRoot) {
            NSLog("[refract] setup payload: %@", message)
        }
        loadChoices()

        window = NSWindow(
            contentRect: content.frame,
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.title = "Set up Refract Image"
        window.contentView = content
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.center()

        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        // Quitting halfway is allowed and correct: nothing is recorded, so the next launch
        // simply asks again. Blocking the close button here would trap a user who opened the
        // app by mistake on a machine that is already set up elsewhere.
        !running
    }

    // MARK: - layout

    private func build(in content: NSView) {
        let title = NSTextField(labelWithString: "Welcome to Refract Image")
        title.font = .systemFont(ofSize: 22, weight: .semibold)

        let blurb = NSTextField(wrappingLabelWithString:
            """
            Refract Image runs an image model entirely on this Mac. It needs two things set up \
            before it can start: the Python runtime that hosts the model, and the model \
            itself. This window installs the runtime and puts your files where you want them; \
            Refract Image then downloads the model you pick.
            """)
        blurb.font = .systemFont(ofSize: 12)
        blurb.textColor = .secondaryLabelColor

        let heading = NSFont.systemFont(ofSize: 12, weight: .semibold)

        let modelTitle = NSTextField(labelWithString: "Which model?")
        modelTitle.font = heading
        modelPopUp.target = self
        modelPopUp.action = #selector(modelChanged)
        detailLabel.font = .systemFont(ofSize: 11)
        detailLabel.textColor = .secondaryLabelColor
        detailLabel.maximumNumberOfLines = 3

        let dataTitle = NSTextField(labelWithString: "Where should models, downloads and images go?")
        dataTitle.font = heading
        style(.rounded, dataButton, title: "Choose…", action: #selector(chooseDataRoot))
        dataLabel.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        dataLabel.textColor = .secondaryLabelColor
        dataLabel.lineBreakMode = .byTruncatingMiddle

        let runtimeTitle = NSTextField(labelWithString: "Where should the Python runtime go?")
        runtimeTitle.font = heading
        style(.rounded, runtimeButton, title: "Choose…", action: #selector(chooseRuntimeRoot))
        runtimeLabel.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        runtimeLabel.textColor = .secondaryLabelColor
        runtimeLabel.lineBreakMode = .byTruncatingMiddle

        summaryLabel.font = .systemFont(ofSize: 11)
        summaryLabel.textColor = .secondaryLabelColor
        summaryLabel.maximumNumberOfLines = 2

        progress.style = .spinning
        progress.isDisplayedWhenStopped = false
        progress.controlSize = .small

        logView.isEditable = false
        logView.font = .monospacedSystemFont(ofSize: 10, weight: .regular)
        logView.backgroundColor = .textBackgroundColor
        logView.textColor = .textColor
        logView.isVerticallyResizable = true
        logView.autoresizingMask = [.width]
        let scroll = NSScrollView(frame: .zero)
        logScroll = scroll
        scroll.documentView = logView
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder

        style(.rounded, installButton, title: "Install and Continue", action: #selector(install))
        installButton.keyEquivalent = "\r"
        style(.rounded, cancelButton, title: "Quit", action: #selector(quit))

        let stack = NSStackView(views: [
            title, blurb,
            divider(), modelTitle, modelPopUp, detailLabel,
            divider(), dataTitle, row(dataButton, dataLabel), summaryLabel,
            runtimeTitle, row(runtimeButton, runtimeLabel),
            divider(),
            row(progress, logScroll),
            row(cancelButton, installButton),
        ])
        stack.orientation = .vertical
        stack.alignment = NSLayoutConstraint.Attribute.leading
        stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 22, left: 26, bottom: 22, right: 26)
        stack.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(stack)

        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            stack.topAnchor.constraint(equalTo: content.topAnchor),
            stack.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            scroll.heightAnchor.constraint(greaterThanOrEqualToConstant: 130),
            scroll.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -52),
            modelPopUp.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -52),
            dataButton.widthAnchor.constraint(equalToConstant: 90),
            runtimeButton.widthAnchor.constraint(equalToConstant: 90),
            dataLabel.widthAnchor.constraint(lessThanOrEqualToConstant: 380),
            runtimeLabel.widthAnchor.constraint(lessThanOrEqualToConstant: 380),
        ])
    }

    private func style(_ style: NSButton.BezelStyle, _ button: NSButton, title: String, action: Selector) {
        button.bezelStyle = style
        button.title = title
        button.target = self
        button.action = action
    }

    private func row(_ views: NSView...) -> NSView {
        let stack = NSStackView(views: views)
        stack.orientation = .horizontal
        stack.alignment = .centerY
        stack.spacing = 8
        return stack
    }

    private func divider() -> NSView {
        let line = NSBox()
        line.boxType = .separator
        return line
    }

    // MARK: - choices

    @objc private func chooseDataRoot() {
        pick(into: \SetupWindow.dataRoot, from: dataButton, kind: .data)
    }

    @objc private func chooseRuntimeRoot() {
        pick(into: \SetupWindow.runtimeRoot, from: runtimeButton, kind: .runtime)
    }

    private enum PickKind { case data, runtime }

    private func pick(into keyPath: ReferenceWritableKeyPath<SetupWindow, URL>, from button: NSButton, kind: PickKind) {
        guard !running else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.prompt = "Choose"
        // The default is the drive with the most room, which is nearly always the external one.
        panel.directoryURL = largestVolume() ?? FileManager.default.homeDirectoryForCurrentUser
        panel.beginSheetModal(for: window) { [weak self] response in
            guard let self, response == .OK, let url = panel.url else { return }
            self[keyPath: keyPath] = url
            self.refresh()
        }
    }

    /// The volume with the most free space, which is the one worth defaulting a 14 GB
    /// download to. Ties go to the boot volume.
    private func largestVolume() -> URL? {
        var best: (URL, Int64)?
        let mounted = FileManager.default.mountedVolumeURLs(
            includingResourceValuesForKeys: [URLResourceKey.volumeAvailableCapacityForImportantUsageKey]
        ) ?? []
        let volumes = mounted + [URL(fileURLWithPath: NSHomeDirectory())]
        for volume in volumes {
            let values = try? volume.resourceValues(forKeys: [URLResourceKey.volumeAvailableCapacityForImportantUsageKey])
            let free = Int64(values?.volumeAvailableCapacityForImportantUsage ?? 0)
            if best == nil || free > best!.1 { best = (volume, free) }
        }
        return best?.0
    }

    @objc private func modelChanged() {
        refresh()
    }

    /// Re-read the free space and redraw everything that depends on the current choices.
    private func refresh() {
        dataLabel.stringValue = dataRoot.path
        runtimeLabel.stringValue = runtimeRoot.path

        guard !choices.isEmpty else {
            summaryLabel.stringValue = "Reading system information…"
            return
        }
        let model = choices[modelPopUp.indexOfSelectedItem]
        detailLabel.stringValue = "\(model.detail)\nAbout \(byteText(model.approxBytes)) to download, plus "
            + "\(byteText(runtimeBytes)) for the runtime."

        let dataFree = freeSpace(at: dataRoot)
        let runtimeFree = freeSpace(at: runtimeRoot)
        var problems: [String] = []
        if dataFree < model.approxBytes + Int64(Self.headroomGB * 1_000_000_000) {
            problems.append("Not enough room where the models will go (\(byteText(dataFree)) free).")
        }
        if runtimeFree < runtimeBytes + Int64(Self.headroomGB * 1_000_000_000) {
            problems.append("Not enough room where the runtime will go (\(byteText(runtimeFree)) free).")
        }
        if dataRoot == runtimeRoot {
            problems.append("Choose different folders for the runtime and the models.")
        }

        if problems.isEmpty {
            summaryLabel.stringValue = "Ready. Everything fits, with room to spare."
            summaryLabel.textColor = .secondaryLabelColor
            installButton.isEnabled = true
        } else {
            summaryLabel.stringValue = problems.joined(separator: " ")
            summaryLabel.textColor = .systemRed
            installButton.isEnabled = false
        }
    }

    // MARK: - install

    @objc private func install() {
        guard !running, !choices.isEmpty else { return }
        running = true
        installButton.isEnabled = false
        modelPopUp.isEnabled = false
        dataButton.isEnabled = false
        runtimeButton.isEnabled = false
        progress.startAnimation(nil)
        logView.string = ""

        let programRoot = self.programRoot
        let bootstrap = programRoot.appendingPathComponent("scripts/bootstrap.sh")

        guard FileManager.default.isExecutableFile(atPath: bootstrap.path)
            || HostPaths.repoRoot() != nil else {
            fail("Refract Image could not find its own installer. Reinstall the app from a fresh download.")
            return
        }

        switch Install.extractPayload(to: programRoot) {
        case .failure(let message):
            fail(message)
            return
        case .success:
            break
        }

        let script = FileManager.default.isExecutableFile(atPath: bootstrap.path)
            ? bootstrap
            : HostPaths.repoRoot()!.appendingPathComponent("scripts/bootstrap.sh")

        // Recorded before the install starts, not after: the app's own paths have to resolve
        // to these the instant the runtime appears, and a half-finished install is simply
        // re-detected next launch because the runtime still will not be found.
        let record = Install.Record(
            dataRoot: dataRoot.path,
            runtimeRoot: runtimeRoot.path,
            programRoot: programRoot.path,
            model: choices[modelPopUp.indexOfSelectedItem].id,
            completedAt: nil
        )
        do {
            try Install.save(record)
        } catch {
            fail("Could not save the setup choices: \(error.localizedDescription)")
            return
        }

        runBootstrap(script, programRoot: programRoot)
    }

    private func runBootstrap(_ script: URL, programRoot: URL) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [
            script.path,
            "--runtime", runtimeRoot.path,
            "--data-root", dataRoot.path,
            "--program", programRoot.path,
        ]
        var environment = ProcessInfo.processInfo.environment
        var path = environment["PATH"] ?? ""
        for directory in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"] where !path.contains(directory) {
            path = "\(path):\(directory)"
        }
        environment["PATH"] = path
        environment["REFRACT_DATA_DIR"] = dataRoot.path
        process.environment = environment

        let out = Pipe()
        process.standardOutput = out
        process.standardError = out
        process.standardInput = FileHandle.nullDevice
        append("Installing the runtime in \(runtimeRoot.path)\n")

        out.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async { self?.append(text) }
        }

        process.terminationHandler = { [weak self] finished in
            DispatchQueue.main.async {
                guard let self else { return }
                self.progress.stopAnimation(nil)
                if finished.terminationStatus == 0 {
                    let record = Install.Record(
                        dataRoot: self.dataRoot.path,
                        runtimeRoot: self.runtimeRoot.path,
                        programRoot: programRoot.path,
                        model: self.choices[self.modelPopUp.indexOfSelectedItem].id,
                        completedAt: Date()
                    )
                    try? Install.save(record)
                    self.append("\nDone. Refract Image will start and download your model.\n")
                    self.window.close()
                    self.onFinished?(record.model)
                } else {
                    self.fail("The runtime install did not finish (exit \(finished.terminationStatus)). "
                        + "The log above says where it stopped; running it again is safe.")
                }
            }
        }

        do {
            try process.run()
        } catch {
            fail("Could not start the installer: \(error.localizedDescription)")
        }
    }

    private func append(_ text: String) {
        logView.textStorage?.append(NSAttributedString(string: text))
        logView.scrollRangeToVisible(NSRange(location: logView.textStorage?.length ?? 0, length: 0))
    }

    private func fail(_ message: String) {
        running = false
        progress.stopAnimation(nil)
        installButton.isEnabled = true
        modelPopUp.isEnabled = true
        dataButton.isEnabled = true
        runtimeButton.isEnabled = true
        append("\n\(message)\n")
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Setup could not finish"
        alert.informativeText = message
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window, completionHandler: nil)
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    // MARK: - model list

    /// The choices, read from the catalog the build generated into the payload.
    func loadChoices() {
        let catalog = programRoot.appendingPathComponent("setup.json")
        guard
            let data = FileManager.default.contents(atPath: catalog.path),
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let raw = object["choices"] as? [[String: Any]]
        else {
            detailLabel.stringValue = "This copy of Refract Image is missing its setup catalog and cannot be installed."
            installButton.isEnabled = false
            return
        }
        runtimeBytes = (object["runtime_bytes"] as? NSNumber)?.int64Value ?? runtimeBytes

        let parsed = raw.compactMap { entry -> ModelChoice? in
            guard let id = entry["id"] as? String, let label = entry["label"] as? String else { return nil }
            let detail = (entry["detail"] as? String) ?? ""
            let notes = (entry["notes"] as? [String]) ?? []
            return ModelChoice(
                id: id,
                label: label,
                detail: ([detail] + notes.prefix(2)).joined(separator: " "),
                approxBytes: (entry["approx_bytes"] as? NSNumber)?.int64Value ?? 0
            )
        }
        guard !parsed.isEmpty else {
            detailLabel.stringValue = "No models are available to install."
            installButton.isEnabled = false
            return
        }
        choices = parsed
        modelPopUp.removeAllItems()
        for choice in parsed { modelPopUp.addItem(withTitle: choice.label) }

        // Pre-select whatever the user picked last time, so reopening setup after a failed
        // install does not silently default them back to the other model.
        if let previous = Install.load()?.model,
           let index = parsed.firstIndex(where: { $0.id == previous }) {
            modelPopUp.selectItem(at: index)
        }
        refresh()
    }

    // MARK: - helpers

    private func freeSpace(at url: URL) -> Int64 {
        var probe = url
        while !FileManager.default.fileExists(atPath: probe.path) {
            let parent = probe.deletingLastPathComponent()
            if parent.path == probe.path { return 0 }
            probe = parent
        }
        let values = try? probe.resourceValues(forKeys: [URLResourceKey.volumeAvailableCapacityForImportantUsageKey])
        return Int64(values?.volumeAvailableCapacityForImportantUsage ?? 0)
    }

    private func byteText(_ bytes: Int64) -> String {
        guard bytes > 0 else { return "an unknown amount" }
        let units = ["B", "KB", "MB", "GB", "TB"]
        var value = Double(bytes)
        var index = 0
        while value >= 1024 && index < units.count - 1 {
            value /= 1024
            index += 1
        }
        let rounded = index == 0 ? String(Int(value.rounded())) : String(format: "%.1f", value)
        return "\(rounded) \(units[index])"
    }
}
