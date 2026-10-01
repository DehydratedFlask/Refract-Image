import Foundation

/// What the first-run setup window recorded, and where the app's pieces now live.
///
/// This file is the app's answer to "has this machine been set up yet?". It is deliberately
/// separate from `runtime.json`, which the *shell* script writes: that one records what a
/// terminal install produced, and it does not exist at all until the runtime is built. The
/// choice of folders is made before there is a runtime to write anything with, so it has to
/// live somewhere that needs no interpreter.
///
/// It lives in Application Support rather than beside the data, because it is a few hundred
/// bytes of preferences and must survive the user moving or wiping the disk the weights are on.
enum Install {
    /// One row per choice, recorded only once setup completes.
    struct Record: Codable {
        var dataRoot: String
        var runtimeRoot: String
        var programRoot: String
        /// The source id the user picked at first run; the app opens on it afterwards.
        var model: String?
        var completedAt: Date?
    }

    // MARK: - location

    /// Always on the boot volume, and always the same place regardless of where the user
    /// pointed the data. It is preferences, not data.
    static var preferencesDirectory: URL {
        HostPaths.homeDirectory
            .appendingPathComponent("Library/Application Support", isDirectory: true)
            .appendingPathComponent("Refract", isDirectory: true)
    }

    static var recordURL: URL { preferencesDirectory.appendingPathComponent("install.json") }

    /// The record, or nil when this machine has never been set up.
    static func load() -> Record? {
        guard let data = FileManager.default.contents(atPath: recordURL.path) else { return nil }
        // A malformed record is treated as absent rather than fatal: the user gets the setup
        // window again, which is a recoverable state, instead of a launch failure.
        return try? JSONDecoder().decode(Record.self, from: data)
    }

    static func save(_ record: Record) throws {
        try FileManager.default.createDirectory(at: preferencesDirectory, withIntermediateDirectories: true)
        let data = try JSONEncoder().encode(record)
        try data.write(to: recordURL, options: .atomic)
    }

    static func clear() {
        try? FileManager.default.removeItem(at: recordURL)
    }

    // MARK: - first run

    /// Whether the setup window should be shown.
    ///
    /// The runtime is the deciding test rather than the record's existence, because a record
    /// can be written before the install that it describes actually succeeds. Someone who
    /// quits halfway through setup is asked again, which is the right outcome; someone whose
    /// install finished is not, even if their models were later deleted.
    static var needsSetup: Bool {
        if Launch.devURL != nil { return false }
        return HostPaths.python() == nil
    }

    // MARK: - payload

    /// The zip the build drops into the bundle: the UI, the backend package and the scripts
    /// needed to install them. About 1.3 MB, which is the whole reason the app can be this small.
    static var payloadArchive: URL? {
        Bundle.main.resourceURL?.appendingPathComponent("payload.zip")
    }

    /// Marker written once extraction has completed, so the ~1.3 MB is unpacked once rather
    /// than on every launch.
    private static let extractedMarker = ".extracted"

    /// Unpack the embedded payload into the program root. Idempotent.
    ///
    /// `ditto` rather than a hand-rolled unzipper: it is present on every macOS, it handles
    /// symlinks and extended attributes correctly, and re-running it over an existing tree is
    /// safe — which is exactly the behaviour wanted when an app update replaces the payload.
    @discardableResult
    static func extractPayload(to programRoot: URL) -> Outcome<URL> {
        guard let archive = payloadArchive, FileManager.default.fileExists(atPath: archive.path) else {
            // A checkout beside the bundle has everything already; this is not an error.
            return .success(programRoot)
        }
        let marker = programRoot.appendingPathComponent(extractedMarker)
        let stamp = modificationDate(archive)
        if let done = modificationDate(marker), let stamp, done >= stamp {
            return .success(programRoot)
        }

        try? FileManager.default.createDirectory(at: programRoot, withIntermediateDirectories: true)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
        process.arguments = ["-x", "-k", archive.path, programRoot.path]
        let error = Pipe()
        process.standardError = error
        process.standardOutput = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            return .failure("Could not unpack the app's own files: \(error.localizedDescription)")
        }
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            let detail = String(data: error.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            return .failure("Could not unpack the app's own files: \(detail.trimmingCharacters(in: .whitespacesAndNewlines))")
        }
        try? "extracted from payload.zip\n".write(to: marker, atomically: true, encoding: .utf8)
        try? FileManager.default.setAttributes([.modificationDate: stamp ?? Date()], ofItemAtPath: marker.path)
        return .success(programRoot)
    }

    private static func modificationDate(_ url: URL) -> Date? {
        try? FileManager.default.attributesOfItem(atPath: url.path)[.modificationDate] as? Date
    }
}