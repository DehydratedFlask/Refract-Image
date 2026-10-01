import AppKit
import WebKit

/// Owns the app's three pieces — service, bridge, window — and the order they come up in.
///
/// The window is there as soon as it can be, and the service starts behind it: a cold start on an
/// external volume takes minutes, and a Dock icon that never opens a window looks like a crash.
/// When the service cannot be reached at all, the app says so in a dialog that names the log file,
/// because a double-clicked app has no terminal to print to.
final class AppDelegate: NSObject, NSApplicationDelegate {
    let service = Service(mock: Launch.mock, devURL: Launch.devURL)
    lazy var bridge = Bridge(service: service)
    lazy var browser = Browser(bridge: bridge, service: service)

    private var otherInstance: NSRunningApplication?
    private var smokeFinished = false
    private var setupWindow: SetupWindow?

    // MARK: - launch

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
            self?.bridge.emit(name, detail)
        }

        // A machine with no runtime has no service to start: it is a Python program, so there
        // is nothing to point a window at yet. Setup runs first, and the service starts after.
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

        startService()
    }

    private func startService() {
        service.start { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let url):
                if Launch.verbose {
                    NSLog("[refract] serving %@", url.absoluteString)
                }
                if Launch.smoke {
                    // Checks wait for the page: an evaluation started against the empty document
                    // is torn down by the navigation that replaces it, which reads as a JavaScript
                    // exception rather than as "you asked too early".
                    var started = false
                    self.browser.onLoaded = { [weak self] in
                        guard !started else { return }
                        started = true
                        self?.runSmokeChecks()
                    }
                    self.browser.onLoadFailed = { [weak self] message in
                        self?.reportFailure("The window could not load: \(message)")
                    }
                }
                self.browser.show(url: url)
            case .failure(let error):
                self.reportFailure(error)
            }
        }
    }

    private func reportFailure(_ message: String) {
        NSLog("[refract] startup failed: %@", message)
        guard !Launch.smoke else {
            Smoke.report(["startup: \(message)"])
            service.stop()
            exit(1)
        }

        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "Refract Image could not start"
        alert.informativeText = "\(message)\n\nLogs: \(service.logsDirectory.path)"
        alert.addButton(withTitle: "Open log folder")
        alert.addButton(withTitle: "Quit")
        if alert.runModal() == .alertFirstButtonReturn {
            NSWorkspace.shared.open(service.logsDirectory)
        }
        NSApp.terminate(nil)
    }

    // MARK: - app lifecycle

    /// Re-opening from the Dock after the window was closed: the service is already warm, so the
    /// window comes back instead of a reload from scratch.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag {
            // Before setup has run there is no window to reopen; the setup window owns the
            // launch, and closing it deliberately quits.
            if let setup = setupWindow {
                setup.reopen()
            } else {
                browser.show()
            }
        }
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    /// The one promise this app has to keep: quitting stops the process holding the weights.
    func applicationWillTerminate(_ notification: Notification) {
        service.stop()
    }

    // MARK: - menu actions

    /// Menu ids the page owns. `quit` is listed here for the sake of the Rust parity, but the Quit
    /// item uses `terminate:` directly, so it never reaches this path.
    @objc func runMenuCommand(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String else { return }
        if id == "quit" {
            NSApp.terminate(nil)
            return
        }
        bridge.noteMenuCommand(id)
    }

    @objc func reloadWindow(_ sender: Any?) {
        browser.reload()
    }

    @objc func openLog(_ sender: NSMenuItem) {
        let which = sender.representedObject as? String
        let url = which == "ui" ? service.uiLogURL : service.backendLogURL
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    @objc func openLogsFolder(_ sender: Any?) {
        NSWorkspace.shared.open(service.logsDirectory)
    }

    // MARK: - smoke checks

    /// Prove the three layers work together without anyone clicking: the window loaded, the page
    /// can call into Swift, and the page can reach the service.
    ///
    /// Each probe returns a string and anything that does not begin with `ok` is a failure, which
    /// keeps the JavaScript free of a second reporting mechanism.
    private func runSmokeChecks() {
        let webView = browser.webView
        let probes: [(label: String, body: String)] = [
            ("bridge", #"""
                const info = await window.__refract.invoke('app_info', {});
                return info && info.data_root ? 'ok shell=' + (info.shell || '?') + ' root=' + info.data_root
                                               : 'FAIL app_info returned nothing';
            """#),
            // The boot screen is a legitimate stage — it stays up while the service answers its
            // first round of requests — so only a "not answering" screen is a failure on sight.
            ("render", #"""
                const deadline = Date.now() + 90000;
                let last = 'nothing rendered yet';
                for (;;) {
                    if (document.querySelector('.topbar')) {
                        return 'ok topbar, ' + (document.querySelector('.content') ? 'views mounted' : 'no content area');
                    }
                    const empty = document.querySelector('.empty-state');
                    if (empty) {
                        const words = (empty.innerText || '').replace(/\s+/g, ' ').slice(0, 200);
                        if (/not answering/i.test(words)) { return 'FAIL ' + words; }
                        last = 'boot screen: ' + words;
                    } else {
                        last = (document.body ? (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 200) : 'no body');
                    }
                    if (Date.now() > deadline) {
                        return 'FAIL timed out, still showing ' + last;
                    }
                    await new Promise(function (resolve) { setTimeout(resolve, 300); });
                }
            """#),
            ("service", #"""
                const stored = localStorage.getItem('refract.connection');
                if (!stored) { return 'FAIL the page recorded no connection'; }
                const connection = JSON.parse(stored);
                const response = await fetch(connection.base + '/api/health', { headers: { 'x-refract-token': connection.token } });
                if (!response.ok) { return 'FAIL health ' + response.status; }
                const health = await response.json();
                return 'ok service v' + health.version + ' mock=' + health.mock;
            """#),
        ]

        var problems: [String] = []
        var index = 0

        let finish = { [weak self] in
            guard let self, !self.smokeFinished else { return }
            self.smokeFinished = true
            Smoke.report(problems)
            self.service.stop()
            exit(problems.isEmpty ? 0 : 1)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 240) {
            problems.append("watchdog: the checks never finished")
            finish()
        }

        func runNext() {
            guard index < probes.count else {
                finish()
                return
            }
            let probe = probes[index]
            index += 1
            webView.callAsyncJavaScript(probe.body, arguments: [:], in: nil, in: .page) { result in
                DispatchQueue.main.async {
                    switch result {
                    case .success(let value):
                        let text = (value as? String) ?? String(describing: value)
                        NSLog("[smoke] %@: %@", probe.label, text)
                        if !text.hasPrefix("ok") {
                            problems.append("\(probe.label): \(text)")
                        }
                    case .failure(let error):
                        problems.append("\(probe.label): \(error.localizedDescription)")
                    }
                    runNext()
                }
            }
        }
        runNext()
    }
}
