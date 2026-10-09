import AppKit

/// The native window and the local inference process have the same lifetime.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let service = Service(mock: Launch.mock)
    let workspace = NativeWindow()
    private var otherInstance: NSRunningApplication?
    private var setupWindow: SetupWindow?

    func applicationWillFinishLaunching(_ notification: Notification) {
        otherInstance = alreadyRunningCopy()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let other = otherInstance {
            other.activate(options: [.activateAllWindows])
            NSApp.terminate(nil)
            return
        }
        NSApp.mainMenu = MainMenu.build(target: self)
        service.onEvent = { [weak self] name, detail in
            if name == "backend-error", let message = detail as? String {
                self?.workspace.store.error = message
            }
        }
        if Install.needsSetup {
            NSApp.setActivationPolicy(.regular)
            let setup = SetupWindow()
            setupWindow = setup
            setup.onFinished = { [weak self] _ in
                self?.setupWindow = nil
                self?.startService()
            }
            setup.show()
            return
        }
        if !Launch.smoke, !Launch.lifecycleClose, let program = Install.load()?.programRoot {
            if case .failure(let message) = Install.extractPayload(to: URL(fileURLWithPath: program, isDirectory: true)) { reportFailure(message); return }
        }
        startService()
    }

    private func startService() {
        workspace.show()
        service.startNative { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let info):
                Task {
                    await self.workspace.store.connect(info)
                    if Launch.lifecycleClose {
                        // Integration QA invokes the actual window Close action while busy.
                        await self.workspace.store.perform {
                            self.workspace.store.draft.prompt = "lifecycle cancellation probe"
                            self.workspace.store.draft.steps = 100
                            try await self.workspace.store.generate()
                        }
                        try? await Task.sleep(nanoseconds: 500_000_000)
                        self.workspace.window?.performClose(nil)
                    }
                    if Launch.smoke {
                        let problems = await self.workspace.smoke()
                        Smoke.report(problems)
                        self.workspace.stop()
                        self.service.stop()
                        exit(problems.isEmpty ? 0 : 1)
                    }
                }
            case .failure(let message): self.reportFailure(message)
            }
        }
    }

    private func reportFailure(_ message: String) {
        NSLog("[refract] startup failed: %@", message)
        if Launch.smoke {
            Smoke.report(["startup: \(message)"])
            service.stop()
            exit(1)
        }
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "Refract Image could not start"
        alert.informativeText = "\(message)\n\nLogs: \(service.logsDirectory.path)"
        alert.addButton(withTitle: "Open log folder")
        alert.addButton(withTitle: "Quit")
        if alert.runModal() == .alertFirstButtonReturn { NSWorkspace.shared.open(service.logsDirectory) }
        NSApp.terminate(nil)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag {
            if let setup = setupWindow { setup.reopen() } else { workspace.show() }
        }
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        setupWindow?.stop()
        workspace.stop()
        service.stop()
    }

    @objc func runMenuCommand(_ sender: NSMenuItem) {
        guard let command = sender.representedObject as? String else { return }
        if command == "quit" { NSApp.terminate(nil) } else { workspace.store.menu(command) }
    }

    @objc func reloadWindow(_ sender: Any?) {
        Task { await workspace.store.perform { try await self.workspace.store.refresh() } }
    }

    @objc func openLog(_ sender: NSMenuItem) { NSWorkspace.shared.activateFileViewerSelecting([service.backendLogURL]) }
    @objc func openLogsFolder(_ sender: Any?) { NSWorkspace.shared.open(service.logsDirectory) }
}
