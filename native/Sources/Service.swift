import Foundation

/// What the inference service announces on stdout once it is listening.
///
/// The service binds its own socket and prints one line, so there is no race between "a port
/// was chosen" and "a port is listening"; the app reads the port out of that line rather than
/// picking one and hoping it is free.
struct BackendInfo {
    let host: String
    let port: Int
    let token: String
    let pid: Int
    let mock: Bool
    let mflux: String?
    let dataRoot: String?

    var baseURL: String { "http://127.0.0.1:\(port)" }

    /// The shape the frontend receives from `backend_info`. Absent values become `NSNull` rather
    /// than a Swift optional, because `JSONSerialization` refuses to write a boxed optional.
    var dictionary: [String: Any] {
        [
            "host": host,
            "port": port,
            "token": token,
            "pid": pid,
            "mock": mock,
            "mflux": mflux ?? NSNull(),
            "data_root": dataRoot ?? NSNull(),
        ]
    }

    init?(announcement: String, fallbackToken: String) {
        guard let data = announcement.data(using: .utf8) else { return nil }
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        guard let port = object["port"] as? Int, port > 0 else { return nil }
        self.port = port
        host = object["host"] as? String ?? "127.0.0.1"
        token = object["token"] as? String ?? fallbackToken
        pid = object["pid"] as? Int ?? 0
        mock = object["mock"] as? Bool ?? false
        mflux = object["mflux"] as? String
        dataRoot = object["data_root"] as? String
    }
}

/// The app's answer to a request: a value, or a message to show the user.
///
/// `Result`'s failure side has to be an `Error`, but every failure here is a sentence meant to be
/// read as written — usually with a log path in it, because a double-clicked app has no terminal.
/// Wrapping those in an error type would only add a `.localizedDescription` to remember.
enum Outcome<T> {
    case success(T)
    case failure(String)
}

/// Fires its handler exactly once, whichever thread gets there first.
///
/// Readiness can arrive from the child's stdout, from its death, or from a timeout, and those
/// are three different threads; without this the app would report the *second* of them, which
/// is usually the misleading one.
final class Once<T> {
    private let lock = NSLock()
    private var delivered = false
    private let handler: (T) -> Void

    init(_ handler: @escaping (T) -> Void) {
        self.handler = handler
    }

    @discardableResult
    func deliver(_ value: T) -> Bool {
        lock.lock()
        if delivered {
            lock.unlock()
            return false
        }
        delivered = true
        lock.unlock()
        handler(value)
        return true
    }
}

/// Owns the two child processes the window depends on: the inference service, and the static
/// server that hands the built UI to the web view over loopback.
///
/// Their lifecycle is the app's. They are started at launch, given a token and a
/// `REFRACT_PARENT_PID` to watch, and killed on quit; if the app is killed outright instead,
/// both children notice their parent has vanished and exit on their own, so a crash cannot
/// leave tens of gigabytes of weights resident. The UI is served over loopback rather than read
/// with `loadFileURL` because a `file://` page is not allowed to call the API cross-origin
/// without a private WebKit preference — same-origin loopback needs no exceptions at all.
final class Service {
    private(set) var backend: BackendInfo?
    private(set) var uiBase: URL?
    private(set) var lastError: String?

    /// Host events for the page to subscribe to (`backend-ready`, `backend-error`).
    var onEvent: ((String, Any?) -> Void)?

    var isRunning: Bool { backendProcess?.isRunning == true }

    private let mockMode: Bool
    private let devPageURL: URL?
    private var backendProcess: Process?
    private var uiProcess: Process?
    private var stopping = false
    private let io = DispatchQueue(label: "local.refract.service.io", qos: .userInitiated)
    private let logLock = NSLock()
    private var handles: [String: FileHandle] = [:]

    private static let backendReadyPrefix = "REFRACT_READY "
    private static let uiReadyPrefix = "REFRACT_STATIC_READY "

    init(mock: Bool, devURL: URL? = nil) {
        mockMode = mock
        devPageURL = devURL
    }

    // MARK: - lifecycle

    /// Start the children, then hand back the address the window should load.
    func start(completion: @escaping (Outcome<URL>) -> Void) {
        if let devPageURL {
            startDevelopmentPage(devPageURL, completion: completion)
            return
        }
        startBuiltUI(completion: completion)
    }

    /// Development mode: the page comes from vite, so there is no built bundle to serve and no
    /// second child process to run. Only the service is started, and the page still asks the app
    /// for its port and token over the bridge.
    private func startDevelopmentPage(_ page: URL, completion: @escaping (Outcome<URL>) -> Void) {
        guard HostPaths.python() != nil else {
            completion(.failure(HostPaths.runtimeHint()))
            return
        }
        stopping = false
        openLogs()
        startBackend { [weak self] result in
            switch result {
            case .failure(let error):
                completion(.failure(error))
            case .success(let info):
                guard let self else { return }
                self.backend = info
                self.onEvent?("backend-ready", info.dictionary)
                completion(.success(Self.uiURL(base: page, backend: info)))
            }
        }
    }

    /// The normal path: the inference service plus a static server for `app/dist`.
    ///
    /// Every completion runs on the main queue; failures name the log file, because a
    /// double-clicked app has no terminal to print to.
    private func startBuiltUI(completion: @escaping (Outcome<URL>) -> Void) {
        guard let dist = HostPaths.builtUI() else {
            completion(.failure(
                "The Refract Image UI has not been built. Run npm run build in app/, or rebuild the app with "
                    + "scripts/make-swift-app.sh."
            ))
            return
        }
        guard let script = HostPaths.staticServerScript() else {
            completion(.failure("scripts/serve_app.py is missing from \(HostPaths.repoRoot()?.path ?? "the checkout")."))
            return
        }
        guard HostPaths.python() != nil else {
            completion(.failure(HostPaths.runtimeHint()))
            return
        }

        stopping = false
        openLogs()
        startBackend { [weak self] result in
            switch result {
            case .failure(let error):
                completion(.failure(error))
            case .success(let info):
                guard let self else { return }
                self.backend = info
                self.onEvent?("backend-ready", info.dictionary)
                self.startUI(from: dist, with: script) { uiResult in
                    switch uiResult {
                    case .failure(let error):
                        completion(.failure(error))
                    case .success(let base):
                        self.uiBase = base
                        completion(.success(Self.uiURL(base: base, backend: info)))
                    }
                }
            }
        }
    }

    /// Restart the inference service, leaving the UI server alone.
    func restartBackend(completion: @escaping (Outcome<BackendInfo>) -> Void) {
        stopping = true
        Self.terminate(backendProcess)
        backendProcess = nil
        backend = nil
        stopping = false
        startBackend { [weak self] result in
            if case .success(let info) = result {
                self?.backend = info
                self?.lastError = nil
                self?.onEvent?("backend-ready", info.dictionary)
            }
            completion(result)
        }
    }

    /// Stop everything. Bounded on purpose: quitting must feel immediate, and the children also
    /// watch this process's pid, so a straggler cannot outlive us for long.
    func stop() {
        stopping = true
        Self.terminate(backendProcess)
        Self.terminate(uiProcess)
        backendProcess = nil
        uiProcess = nil
        backend = nil
        uiBase = nil
        closeLogs()
    }

    // MARK: - children

    private func startBackend(completion: @escaping (Outcome<BackendInfo>) -> Void) {
        guard let python = HostPaths.python() else {
            completion(.failure(HostPaths.runtimeHint()))
            return
        }
        let token = Self.randomToken()
        var arguments = ["-m", "refract_backend", "--token", token, "--port", "0", "--log-level", "warning"]
        if mockMode {
            arguments.append("--mock")
        }

        var environment = [
            "REFRACT_PARENT_PID": "\(ProcessInfo.processInfo.processIdentifier)",
            "REFRACT_ROOT": HostPaths.dataRoot().path,
        ]
        // A standalone install never pip-installed the backend; it is plain Python sitting in
        // the payload the app unpacked. Prepending to PYTHONPATH makes `python -m
        // refract_backend` find it without an editable install into the venv.
        if let backend = HostPaths.backendDirectory() {
            let existing = ProcessInfo.processInfo.environment["PYTHONPATH"]
            environment["PYTHONPATH"] = existing.map { "\(backend.path):\($0)" } ?? backend.path
        }

        let process = makeProcess(
            executable: python,
            arguments: arguments,
            environment: environment
        )
        let log = openLog(backendLogURL)
        let delivery = Once<Outcome<BackendInfo>> { result in
            if case .failure = result {
                Self.terminate(process)
            }
            completion(result)
        }

        stream(process.standardOutput as? Pipe, to: log) { line in
            guard line.hasPrefix(Self.backendReadyPrefix) else { return }
            let payload = String(line.dropFirst(Self.backendReadyPrefix.count))
            if let info = BackendInfo(announcement: payload, fallbackToken: token) {
                DispatchQueue.main.async { delivery.deliver(.success(info)) }
            } else {
                DispatchQueue.main.async {
                    delivery.deliver(.failure("The service announced unreadable startup information: \(payload)"))
                }
            }
        }
        stream(process.standardError as? Pipe, to: log) { _ in }
        watchExit(of: process) { [weak self] in
            guard let self, !self.stopping, self.backendProcess === process else { return }
            let note = "The local service exited during startup. See \(self.backendLogURL.path)"
            self.noteError(note)
            delivery.deliver(.failure(note))
        }

        do {
            try process.run()
            backendProcess = process
        } catch {
            completion(.failure("Could not start \(python): \(error.localizedDescription)"))
            return
        }

        // Cold starts on an external volume have been known to take minutes; a service that is
        // merely slow should not be killed, so the deadline only applies to the announcement.
        io.asyncAfter(deadline: .now() + 120) {
            delivery.deliver(.failure("The service did not report readiness within 120s. See \(self.backendLogURL.path)"))
        }
    }

    private func startUI(from dist: URL, with script: URL, completion: @escaping (Outcome<URL>) -> Void) {
        guard let python = HostPaths.python() else {
            completion(.failure(HostPaths.runtimeHint()))
            return
        }
        let process = makeProcess(
            executable: python,
            arguments: [script.path, "--directory", dist.path, "--parent-pid", "\(ProcessInfo.processInfo.processIdentifier)"],
            environment: [:]
        )
        let log = openLog(uiLogURL)
        let delivery = Once<Outcome<URL>> { result in
            if case .failure = result {
                Self.terminate(process)
            }
            completion(result)
        }

        stream(process.standardOutput as? Pipe, to: log) { line in
            guard line.hasPrefix(Self.uiReadyPrefix) else { return }
            let payload = String(line.dropFirst(Self.uiReadyPrefix.count))
            guard
                let data = payload.data(using: .utf8),
                let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                let port = object["port"] as? Int,
                let base = URL(string: "http://127.0.0.1:\(port)")
            else {
                DispatchQueue.main.async { delivery.deliver(.failure("The UI server announced unreadable information: \(payload)")) }
                return
            }
            DispatchQueue.main.async { delivery.deliver(.success(base)) }
        }
        stream(process.standardError as? Pipe, to: log) { _ in }
        watchExit(of: process) { [weak self] in
            guard let self, !self.stopping else { return }
            self.deliveryFailure(of: delivery, message: "The UI server stopped. See \(self.uiLogURL.path)")
        }

        do {
            try process.run()
            uiProcess = process
        } catch {
            completion(.failure("Could not start the UI server: \(error.localizedDescription)"))
            return
        }

        io.asyncAfter(deadline: .now() + 30) {
            self.deliveryFailure(of: delivery, message: "The UI server did not start within 30s. See \(self.uiLogURL.path)")
        }
    }

    private func deliveryFailure(of delivery: Once<Outcome<URL>>, message: String) {
        DispatchQueue.main.async {
            // A startup deadline that fires after readiness is not an error.
            if delivery.deliver(.failure(message)) { self.noteError(message) }
        }
    }

    private func noteError(_ message: String) {
        lastError = message
        NSLog("[refract] %@", message)
        DispatchQueue.main.async { self.onEvent?("backend-error", message) }
    }

    /// Report a child that died on its own. Started before `run()`, so a fast failure is caught.
    private func watchExit(of process: Process, _ onExit: @escaping () -> Void) {
        process.terminationHandler = { _ in DispatchQueue.main.async { onExit() } }
    }

    // MARK: - process plumbing

    private func makeProcess(executable: String, arguments: [String], environment: [String: String]) -> Process {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments

        // A Finder-launched app inherits a nearly empty environment, so the children get an
        // explicit one: the runtime's own tools (`git`, `curl`) are found by absolute-path
        // libraries most of the time, but Homebrew and /usr/local belong in PATH regardless.
        var inherited = ProcessInfo.processInfo.environment
        let path = [inherited["PATH"] ?? "", "/usr/bin", "/bin", "/usr/sbin", "/sbin", "/opt/homebrew/bin", "/usr/local/bin"]
            .filter { !$0.isEmpty }
            .joined(separator: ":")
        inherited["PATH"] = path
        inherited["PYTHONUNBUFFERED"] = "1"
        for (key, value) in environment {
            inherited[key] = value
        }
        process.environment = inherited
        process.standardOutput = Pipe()
        process.standardError = Pipe()
        process.standardInput = FileHandle.nullDevice
        return process
    }

    /// Send SIGTERM, wait briefly, then insist.
    private static func terminate(_ process: Process?) {
        guard let process, process.isRunning else { return }
        let pid = process.processIdentifier
        kill(pid, SIGTERM)
        for _ in 0..<30 {
            if !process.isRunning { return }
            usleep(50_000)
        }
        kill(pid, SIGKILL)
    }

    private func stream(_ pipe: Pipe?, to handle: FileHandle?, onLine: @escaping (String) -> Void) {
        guard let pipe else { return }
        var buffer = Data()
        pipe.fileHandleForReading.readabilityHandler = { [weak self] source in
            let data = source.availableData
            if data.isEmpty {
                source.readabilityHandler = nil
                return
            }
            buffer.append(data)
            while let newline = buffer.firstIndex(of: UInt8(ascii: "\n")) {
                let line = buffer[buffer.startIndex..<newline]
                buffer.removeSubrange(buffer.startIndex...newline)
                let text = String(decoding: line, as: UTF8.self)
                self?.write(Data((text + "\n").utf8), to: handle)
                onLine(text.trimmingCharacters(in: .whitespacesAndNewlines))
            }
        }
    }

    /// Writes from the two readability handlers land on different queues, hence the lock.
    private func write(_ data: Data, to handle: FileHandle?) {
        guard let handle else { return }
        logLock.lock()
        defer { logLock.unlock() }
        handle.write(data)
    }

    // MARK: - logs

    var backendLogURL: URL { HostPaths.logsDirectory().appendingPathComponent("native-backend.log") }
    var uiLogURL: URL { HostPaths.logsDirectory().appendingPathComponent("native-ui.log") }
    var logsDirectory: URL { HostPaths.logsDirectory() }

    /// Fresh logs per launch. A double-clicked app has no terminal, so the log is the only
    /// account of what happened — and a stale one from an earlier run is worse than none.
    private func openLogs() {
        try? FileManager.default.createDirectory(at: logsDirectory, withIntermediateDirectories: true)
        for url in [backendLogURL, uiLogURL] {
            try? FileManager.default.removeItem(at: url)
        }
    }

    private func openLog(_ url: URL) -> FileHandle? {
        logLock.lock()
        defer { logLock.unlock() }
        if let existing = handles[url.path] { return existing }
        FileManager.default.createFile(atPath: url.path, contents: Data())
        guard let handle = FileHandle(forWritingAtPath: url.path) else { return nil }
        handles[url.path] = handle
        return handle
    }

    private func closeLogs() {
        logLock.lock()
        defer { logLock.unlock() }
        for handle in handles.values {
            try? handle.close()
        }
        handles.removeAll()
    }

    // MARK: - addresses

    /// The address the window loads. The query string is what the *browser* fallback uses to
    /// find the service; the native bridge answers the same question, and wins.
    static func uiURL(base: URL, backend: BackendInfo) -> URL {
        guard var components = URLComponents(url: base, resolvingAgainstBaseURL: false) else { return base }
        components.queryItems = [
            URLQueryItem(name: "api", value: backend.baseURL),
            URLQueryItem(name: "token", value: backend.token),
            URLQueryItem(name: "mock", value: backend.mock ? "1" : "0"),
        ]
        return components.url ?? base
    }

    static func randomToken() -> String {
        var bytes = [UInt8](repeating: 0, count: 24)
        if let handle = FileHandle(forReadingAtPath: "/dev/urandom"),
           let data = try? handle.read(upToCount: 24), data.count == 24 {
            bytes = [UInt8](data)
        } else {
            let seed = UInt64(ProcessInfo.processInfo.processIdentifier) &* 1_000_000_007
            for index in 0..<24 { bytes[index] = UInt8(truncatingIfNeeded: seed &>> ((index % 8) * 8)) }
        }
        return bytes.map { String(format: "%02x", $0) }.joined()
    }
}
