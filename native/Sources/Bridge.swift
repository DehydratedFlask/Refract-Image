import AppKit
import UniformTypeIdentifiers
import UserNotifications
import WebKit

/// The command channel between the page and the app.
///
/// The command *names* are the ones the frontend's routing table (`app/src/lib/ipc.ts`) already
/// speaks — `backend_info`, `reveal_in_finder`, `copy_file`, … — and every one of them has a
/// browser fallback there, so the same UI runs in a tab with the native answers missing.
/// Only the transport differs here: `webkit.messageHandlers` going up, `callAsyncJavaScript`
/// coming back down, with a promise registry in a page-world script so `await` works normally.
///
/// Everything here runs on the main queue: message handlers are delivered there, panels complete
/// there, and the web view must only be touched from there.
final class Bridge: NSObject, WKScriptMessageHandler {
    private weak var webView: WKWebView?
    private let service: Service
    private var sleepAssertion: NSObjectProtocol?
    private var notificationsRequested = false

    init(service: Service) {
        self.service = service
    }

    // MARK: - installation

    static func userScript(verbose: Bool) -> WKUserScript {
        WKUserScript(source: source(verbose: verbose), injectionTime: .atDocumentStart, forMainFrameOnly: true)
    }

    func configure(_ configuration: WKWebViewConfiguration) {
        // Handlers and user scripts have to be on the configuration before the view is created:
        // `WKWebView` copies it, so anything added afterwards is silently ignored.
        configuration.userContentController.add(self, name: "refract")
        configuration.userContentController.add(self, name: "refract-log")
        configuration.userContentController.addUserScript(Bridge.userScript(verbose: Launch.verbose))
    }

    func attach(to webView: WKWebView) {
        self.webView = webView
    }

    // MARK: - WKScriptMessageHandler

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "refract-log" {
            guard Launch.verbose, let body = message.body as? [String: Any] else { return }
            NSLog("[page:%@] %@", body["level"] as? String ?? "?", body["text"] as? String ?? "")
            return
        }
        guard let body = message.body as? [String: Any],
              let id = body["id"] as? Int,
              let command = body["command"] as? String
        else {
            NSLog("[refract] malformed bridge message: %@", String(describing: message.body))
            return
        }
        dispatch(id: id, command: command, args: body["args"] as? [String: Any] ?? [:])
    }

    // MARK: - commands

    private func dispatch(id: Int, command: String, args: [String: Any]) {
        switch command {
        case "backend_info":
            backendInfo(id)

        case "backend_error":
            succeed(id, service.lastError)

        case "restart_backend":
            service.restartBackend { [weak self] result in
                DispatchQueue.main.async {
                    switch result {
                    case .success(let info):
                        self?.succeed(id, info.dictionary)
                        // The token changed, so the page has to start over; reloading is the only
                        // way to be sure nothing is still holding the dead connection.
                        self?.reloadSoon()
                    case .failure(let error):
                        self?.fail(id, error)
                    }
                }
            }

        case "app_info":
            succeed(id, appInfo())

        case "reveal_in_finder":
            guard let path = string(args, "path"), !path.isEmpty else {
                fail(id, "Nothing to reveal.")
                return
            }
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
            succeed(id, path)

        case "open_path":
            guard let path = string(args, "path"), !path.isEmpty else {
                fail(id, "Nothing to open.")
                return
            }
            let url = URL(fileURLWithPath: path)
            if !NSWorkspace.shared.open(url) {
                fail(id, "macOS could not open \(path).")
                return
            }
            succeed(id, path)

        case "open_log":
            let url = args["which"] as? String == "ui" ? service.uiLogURL : service.backendLogURL
            NSWorkspace.shared.activateFileViewerSelecting([url])
            succeed(id, url.path)

        case "copy_text":
            NSPasteboard.general.clearContents()
            succeed(id, NSPasteboard.general.setString(string(args, "text") ?? "", forType: .string))

        case "copy_file":
            copyFile(id: id, args: args)

        case "pick_images":
            presentOpenPanel(multiple: true, directories: false, message: string(args, "title"), types: [.image]) { paths in
                self.succeed(id, paths)
            }

        case "pick_directory":
            presentOpenPanel(multiple: false, directories: true, message: string(args, "title"), types: []) { paths in
                self.succeed(id, paths.first)
            }

        case "pick_file":
            let extensions = (args["extensions"] as? [String]) ?? []
            presentOpenPanel(
                multiple: false,
                directories: false,
                message: string(args, "title"),
                types: Bridge.contentTypes(extensions: extensions)
            ) { paths in
                self.succeed(id, paths.first)
            }

        case "save_as":
            let panel = NSSavePanel()
            panel.nameFieldStringValue = string(args, "defaultPath") ?? "image.png"
            let types = Bridge.contentTypes(extensions: (args["extensions"] as? [String]) ?? [])
            if !types.isEmpty {
                panel.allowedContentTypes = types
            }
            if let source = string(args, "source"), !source.isEmpty {
                panel.directoryURL = URL(fileURLWithPath: source).deletingLastPathComponent()
            }
            NSApp.activate(ignoringOtherApps: true)
            panel.begin { response in
                self.succeed(id, response == .OK ? panel.url?.path : nil)
            }

        case "copy_image":
            guard let source = string(args, "source"), let image = NSImage(contentsOfFile: source) else {
                fail(id, "That file could not be read as an image.")
                return
            }
            let pasteboard = NSPasteboard.general
            pasteboard.clearContents()
            succeed(id, pasteboard.writeObjects([image]))

        case "notify":
            // Only worth a banner when Refract Image is not already the thing on screen.
            if !Launch.smoke, !NSApp.isActive || NSApp.keyWindow == nil {
                notify(title: string(args, "title") ?? "Refract Image", body: string(args, "body") ?? "")
            }
            succeed(id, nil)

        case "keep_awake":
            keepAwake(args["on"] as? Bool ?? false)
            succeed(id, nil)

        case "start_window_drag":
            beginWindowDrag()
            succeed(id, nil)

        case "window_title":
            if let title = string(args, "title") {
                webView?.window?.title = title
            }
            succeed(id, nil)

        default:
            fail(id, "Unknown host command: \(command)")
        }
    }

    /// Where the service is. The app starts it at launch, so this normally just reports the
    /// answer; when the service has died it brings it back rather than handing the page a 404.
    private func backendInfo(_ id: Int) {
        if let info = service.backend {
            succeed(id, info.dictionary)
            return
        }
        service.restartBackend { [weak self] result in
            DispatchQueue.main.async {
                switch result {
                case .success(let info):
                    self?.succeed(id, info.dictionary)
                case .failure(let error):
                    self?.fail(id, error)
                }
            }
        }
    }

    private func appInfo() -> [String: Any] {
        let install = Install.load()
        return [
            "runtime": HostPaths.python() ?? HostPaths.runtimeHint(),
            "version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.2",
            "mock": service.backend.map { $0.mock ? "true" : "false" } ?? "unknown",
            "data_root": HostPaths.dataRoot().path,
            "logs": service.logsDirectory.path,
            "shell": "swift",
            // What the first-run setup recorded, so the UI can open on the model the user
            // chose and start downloading it without being asked twice.
            "first_run_model": install?.model ?? NSNull(),
            "runtime_root": install?.runtimeRoot ?? NSNull(),
            "program_root": install?.programRoot ?? NSNull(),
        ]
    }

    private func copyFile(id: Int, args: [String: Any]) {
        guard let source = string(args, "source"), let target = string(args, "target") else {
            fail(id, "copy_file needs a source and a target.")
            return
        }
        let from = URL(fileURLWithPath: source)
        let to = URL(fileURLWithPath: target)
        guard FileManager.default.fileExists(atPath: from.path) else {
            fail(id, "\(source) is not a file.")
            return
        }
        do {
            try FileManager.default.createDirectory(at: to.deletingLastPathComponent(), withIntermediateDirectories: true)
            // The save panel has already asked permission to overwrite, and `copyItem` refuses to.
            if FileManager.default.fileExists(atPath: to.path) {
                try FileManager.default.removeItem(at: to)
            }
            try FileManager.default.copyItem(at: from, to: to)
            succeed(id, target)
        } catch {
            fail(id, "Could not copy to \(target): \(error.localizedDescription)")
        }
    }

    // MARK: - panels

    private func presentOpenPanel(
        multiple: Bool,
        directories: Bool,
        message: String?,
        types: [UTType],
        completion: @escaping ([String]) -> Void
    ) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = !directories
        panel.canChooseDirectories = directories
        panel.canCreateDirectories = directories
        panel.allowsMultipleSelection = multiple
        panel.treatsFilePackagesAsDirectories = false
        if let message {
            panel.message = message
        }
        panel.prompt = multiple ? "Add" : "Choose"
        if !directories, !types.isEmpty {
            panel.allowedContentTypes = types
        }
        NSApp.activate(ignoringOtherApps: true)
        panel.begin { response in
            completion(response == .OK ? panel.urls.map(\.path) : [])
        }
    }

    private static func contentTypes(extensions: [String]) -> [UTType] {
        extensions.reduce(into: [UTType]()) { types, ext in
            if let type = UTType(filenameExtension: ext.lowercased()) {
                types.append(type)
            }
        }
    }

    // MARK: - system services

    /// Guarded because `UNUserNotificationCenter` raises an Objective-C exception when the
    /// process is not a real bundle — and Swift cannot catch an Objective-C exception. Running
    /// the sources with `swift run` is otherwise useful, so it degrades to the log instead.
    private func notify(title: String, body: String) {
        guard Bridge.canUseUserNotifications else {
            NSLog("[refract] notification: %@ — %@", title, body)
            return
        }
        let center = UNUserNotificationCenter.current()
        if !notificationsRequested {
            notificationsRequested = true
            center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
        }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        center.add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
    }

    static var canUseUserNotifications: Bool {
        Bundle.main.bundlePath.hasSuffix(".app") && Bundle.main.bundleIdentifier != nil
    }

    /// Stop the machine going idle mid-generation. A five-minute denoise that lands during
    /// sleep is a lost run, and unlike the browser case the app knows exactly when it is busy.
    private func keepAwake(_ on: Bool) {
        if on {
            if sleepAssertion == nil {
                sleepAssertion = ProcessInfo.processInfo.beginActivity(
                    options: [.userInitiated, .idleSystemSleepDisabled],
                    reason: "Refract Image is generating an image"
                )
            }
        } else if let assertion = sleepAssertion {
            ProcessInfo.processInfo.endActivity(assertion)
            sleepAssertion = nil
        }
    }

    // MARK: - replies

    private func succeed(_ id: Int, _ payload: Any?) {
        reply(id: id, ok: true, payload: payload)
    }

    private func fail(_ id: Int, _ message: String) {
        NSLog("[refract] command failed: %@", message)
        reply(id: id, ok: false, payload: message)
    }

    private func reply(id: Int, ok: Bool, payload: Any?) {
        call(
            "window.__refract && window.__refract._reply(id, ok, text)",
            ["id": id, "ok": ok, "text": Bridge.encode(payload)]
        )
    }

    /// Send a host event (`menu`, `backend-ready`, `drag-drop`) to the page.
    func emit(_ name: String, _ detail: Any?) {
        call("window.__refract && window.__refract._event(name, text)", ["name": name, "text": Bridge.encode(detail)])
    }

    private func call(_ javascript: String, _ arguments: [String: Any]) {
        guard let webView else { return }
        webView.callAsyncJavaScript(javascript, arguments: arguments, in: nil, in: .page) { result in
            if case .failure(let error) = result {
                NSLog("[refract] bridge reply rejected: %@", String(describing: error))
            }
        }
    }

    /// Payloads travel as JSON text: `callAsyncJavaScript` encodes arguments faithfully and the
    /// page does the one `JSON.parse`, so no value has to be escaped into source code.
    /// Fragments are allowed because a reply is often just a string or a boolean.
    private static func encode(_ value: Any?) -> String {
        guard let value else { return "null" }
        guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.fragmentsAllowed]) else {
            NSLog("[refract] cannot encode %@", String(describing: type(of: value)))
            return "null"
        }
        return String(data: data, encoding: .utf8) ?? "null"
    }

    private func string(_ args: [String: Any], _ key: String) -> String? {
        args[key] as? String
    }

    private func reloadSoon() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { [weak webView] in
            webView?.reload()
        }
    }

    private func beginWindowDrag() {
        webView?.window.map { window in
            if let event = NSApp.currentEvent {
                window.performDrag(with: event)
            }
        }
    }

    /// Notify the page that Finder dropped files onto the window.
    func noteDrop(paths: [String]) {
        emit("drag-drop", paths)
    }

    /// …and that a drag has entered or left, which is what lights up the drop zone.
    func noteDrag(_ active: Bool) {
        emit("drag-state", active)
    }

    /// Forward a menu selection, using the same command ids the Rust menu emitted.
    func noteMenuCommand(_ id: String) {
        emit("menu", id)
    }

    // MARK: - page script

    private static func source(verbose: Bool) -> String {
        """
        (function () {
          "use strict";
          if (window.__refract) { return; }
          var pending = new Map();
          var listeners = new Map();
          var nextId = 1;

          function parse(text) {
            if (text == null || text === "null") { return null; }
            try { return JSON.parse(text); } catch (error) { return text; }
          }

          function invoke(command, args) {
            return new Promise(function (resolve, reject) {
              var id = nextId++;
              pending.set(id, { resolve: resolve, reject: reject });
              try {
                window.webkit.messageHandlers.refract.postMessage({ id: id, command: command, args: args || {} });
              } catch (error) {
                pending.delete(id);
                reject(error);
              }
            });
          }

          function on(name, handler) {
            var set = listeners.get(name);
            if (!set) { set = new Set(); listeners.set(name, set); }
            set.add(handler);
            return function () { set.delete(handler); };
          }

          var bridge = {
            host: "swift",
            invoke: invoke,
            on: on,
            _reply: function (id, ok, text) {
              var entry = pending.get(id);
              if (!entry) { return; }
              pending.delete(id);
              var value = parse(text);
              if (ok) { entry.resolve(value); }
              else { entry.reject(new Error(typeof value === "string" && value ? value : "Refract Image could not run " + id)); }
            },
            _event: function (name, text) {
              var set = listeners.get(name);
              if (!set) { return; }
              var detail = parse(text);
              set.forEach(function (handler) {
                try { handler(detail); } catch (error) { console.error(error); }
              });
            }
          };

          Object.defineProperty(window, "__refract", { value: bridge });

          if (\(verbose ? "true" : "false")) {
            var post = function (level, text) {
              try { window.webkit.messageHandlers["refract-log"].postMessage({ level: level, text: text }); } catch (error) {}
            };
            ["log", "info", "warn", "error"].forEach(function (level) {
              var original = console[level] ? console[level].bind(console) : null;
              console[level] = function () {
                var parts = [];
                for (var i = 0; i < arguments.length; i++) {
                  var value = arguments[i];
                  if (typeof value === "string") { parts.push(value); }
                  else { try { parts.push(JSON.stringify(value)); } catch (error) { parts.push(String(value)); } }
                }
                post(level, parts.join(" "));
                if (original) { original.apply(null, arguments); }
              };
            });
            window.addEventListener("error", function (event) {
              post("error", event.message + " at " + (event.filename || "?") + ":" + (event.lineno || 0));
            });
          }
        })();
        """
    }
}
