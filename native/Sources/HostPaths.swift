import Foundation

/// Where the checkout, the runtime and the data live.
///
/// These are the rules `scripts/_env.sh` and `refract_backend.paths` already follow, restated
/// here because an app launched from Finder inherits no shell environment whatsoever: there is
/// no `PATH`, no activated virtualenv and no `REFRACT_ROOT`, so the layout has to be worked out
/// from the binary's own position on disk. Getting this wrong is expensive rather than merely
/// untidy — the data root is what decides whether ~14 GB of weights land on the external SSD or
/// on the internal disk.
enum HostPaths {
    static let homeDirectory = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)

    // MARK: - checkout

    /// The directory the running binary's checkout occupies, when it can be worked out.
    ///
    /// Walking up from the executable covers both layouts the app ships in: a bundle sitting
    /// beside the checkout (`Refract Image.app/Contents/MacOS/RefractImage` is three levels down),
    /// and a binary still inside `native/build`.
    static func repoRoot() -> URL? {
        if let explicit = environmentValue("REFRACT_REPO_ROOT") {
            return URL(fileURLWithPath: explicit, isDirectory: true)
        }
        var directory = containingDirectory
        for _ in 0..<12 {
            if FileManager.default.isExecutableFile(atPath: directory.appendingPathComponent(".runtime/bin/python").path)
                || fileExists(directory.appendingPathComponent("scripts/bootstrap.sh")) {
                return directory
            }
            let parent = directory.deletingLastPathComponent()
            if parent.path == directory.path { break }
            directory = parent
        }
        return nil
    }

    /// The built React bundle the window renders, if there is one.
    static func builtUI() -> URL? {
        for root in programRoots() {
            let dist = root.appendingPathComponent("app/dist", isDirectory: true)
            if fileExists(dist.appendingPathComponent("index.html")) { return dist }
        }
        return nil
    }

    /// `scripts/serve_app.py`, the static server the window's content is served by.
    static func staticServerScript() -> URL? {
        for root in programRoots() {
            let script = root.appendingPathComponent("scripts/serve_app.py")
            if fileExists(script) { return script }
        }
        return nil
    }

    /// The checkout holding the backend package, for PYTHONPATH.
    ///
    /// A standalone install runs the backend straight out of the extracted payload rather
    /// than installing it into the venv, so the app has to be able to point at a package that
    /// was never pip-installed.
    static func backendDirectory() -> URL? {
        for root in programRoots() {
            let package = root.appendingPathComponent("backend", isDirectory: true)
            if fileExists(package.appendingPathComponent("refract_backend/app.py")) { return package }
        }
        return nil
    }

    /// Where the app's own code might live, most specific first: the payload a first-run setup
    /// extracted, then the checkout beside the bundle.
    private static func programRoots() -> [URL] {
        var roots: [URL] = []
        if let program = Install.load()?.programRoot {
            roots.append(URL(fileURLWithPath: program, isDirectory: true))
        }
        if let repo = repoRoot() { roots.append(repo) }
        return roots
    }

    // MARK: - data

    /// Where models, downloads, jobs, outputs and the library go.
    ///
    /// Same precedence as `refract_backend.paths.data_root`: an explicit override, then
    /// `.refract` beside a checkout that is *not* on the boot volume, then Application Support.
    /// The service is handed this as `REFRACT_ROOT` so the two can never disagree.
    static func dataRoot() -> URL {
        if let explicit = environmentValue("REFRACT_ROOT") ?? environmentValue("REFRACT_DATA_DIR") {
            return URL(fileURLWithPath: explicit, isDirectory: true)
        }
        // What the user chose at first run beats what we would have guessed. Without this the
        // app would silently ignore a deliberate choice and start writing 14 GB somewhere else.
        if let chosen = Install.load()?.dataRoot {
            return URL(fileURLWithPath: chosen, isDirectory: true)
        }
        if let root = repoRoot(), !isOnInternalDisk(root) {
            return root.appendingPathComponent(".refract", isDirectory: true)
        }
        return homeDirectory
            .appendingPathComponent("Library/Application Support", isDirectory: true)
            .appendingPathComponent("Refract", isDirectory: true)
    }

    /// Where the children's logs go: the runtime directory when there is one, so a
    /// terminal-launched run and a double-clicked run write the same files.
    static func logsDirectory() -> URL {
        if let root = repoRoot() {
            let runtime = root.appendingPathComponent(".runtime", isDirectory: true)
            if isDirectory(runtime) { return runtime }
        }
        if let runtime = Install.load()?.runtimeRoot {
            let chosen = URL(fileURLWithPath: runtime, isDirectory: true)
            if isDirectory(chosen) { return chosen }
        }
        return dataRoot().appendingPathComponent("logs", isDirectory: true)
    }

    // MARK: - interpreter

    /// The interpreter that has mflux installed, or nil when the runtime is not set up yet.
    static func python() -> String? {
        for candidate in pythonCandidates() where isExecutable(candidate) {
            return candidate
        }
        return nil
    }

    /// Every interpreter worth trying, in the order the native shell prefers them.
    static func pythonCandidates() -> [String] {
        var candidates: [String] = []
        if let explicit = environmentValue("REFRACT_PYTHON") {
            candidates.append(explicit)
        }

        // runtime.json records the interpreter `scripts/bootstrap.sh` actually created. It is
        // looked for in the data root first, then beside the checkout, then in the legacy
        // Application Support location, so a runtime installed by an older build keeps working.
        var manifests = [dataRoot().appendingPathComponent("runtime.json").path]
        if let root = repoRoot() {
            manifests.append(root.appendingPathComponent(".runtime/runtime.json").path)
        }
        manifests.append(
            homeDirectory
                .appendingPathComponent("Library/Application Support/Refract", isDirectory: true)
                .appendingPathComponent("runtime.json").path
        )
        for manifest in manifests {
            if let recorded = interpreterRecordedIn(manifest) {
                candidates.append(recorded)
            }
        }

        if let runtime = Install.load()?.runtimeRoot {
            candidates.append(URL(fileURLWithPath: runtime, isDirectory: true).appendingPathComponent("bin/python").path)
        }

        if let root = repoRoot() {
            candidates.append(root.appendingPathComponent(".runtime/bin/python").path)
        }
        // Last resort: climb from the executable, which still finds a runtime when the bundle
        // was moved away from the checkout it was built beside.
        var directory = containingDirectory
        for _ in 0..<12 {
            candidates.append(directory.appendingPathComponent(".runtime/bin/python").path)
            let parent = directory.deletingLastPathComponent()
            if parent.path == directory.path { break }
            directory = parent
        }
        return candidates
    }

    /// Where is the runtime, and why could it not be found? For the alert a failed start shows.
    static func runtimeHint() -> String {
        "No Refract Image Python runtime found. Run scripts/bootstrap.sh (or double-click "
            + "\"Install Refract Image.command\") once, then open Refract Image again. Looked for: "
            + pythonCandidates().prefix(4).joined(separator: ", ")
    }

    // MARK: - primitives

    static func environmentValue(_ name: String) -> String? {
        guard let value = ProcessInfo.processInfo.environment[name], !value.isEmpty else { return nil }
        return value
    }

    static func fileExists(_ url: URL) -> Bool {
        FileManager.default.fileExists(atPath: url.path)
    }

    static func isDirectory(_ url: URL) -> Bool {
        var isDirectory: ObjCBool = false
        return FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory) && isDirectory.boolValue
    }

    static func isExecutable(_ path: String) -> Bool {
        FileManager.default.isExecutableFile(atPath: path)
    }

    /// The device a path lives on. Paths that do not exist yet resolve to their closest existing
    /// ancestor, because the data root is only created on first launch.
    static func device(of path: URL) -> UInt64? {
        var probe = path
        while !FileManager.default.fileExists(atPath: probe.path) {
            let parent = probe.deletingLastPathComponent()
            if parent.path == probe.path { return nil }
            probe = parent
        }
        var attributes = stat()
        guard stat(probe.path, &attributes) == 0 else { return nil }
        return UInt64(bitPattern: Int64(attributes.st_dev))
    }

    /// True when the path shares a device with the home folder, i.e. it is the boot volume.
    ///
    /// `st_dev` rather than `volumeIdentifierForPath` because that key hands back an opaque
    /// `NSCopying` existential that is not `Hashable`, so it cannot even be compared.
    static func isOnInternalDisk(_ path: URL) -> Bool {
        guard let target = device(of: path), let home = device(of: homeDirectory) else { return true }
        return target == home
    }

    /// `Contents/MacOS` of the running bundle, or the directory of the invoked binary.
    private static var containingDirectory: URL {
        if let executable = Bundle.main.executableURL {
            return executable.deletingLastPathComponent()
        }
        return URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
    }

    private static func interpreterRecordedIn(_ manifest: String) -> String? {
        guard let data = FileManager.default.contents(atPath: manifest) else { return nil }
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        guard let python = object["python"] as? String, !python.isEmpty else { return nil }
        return python
    }
}
