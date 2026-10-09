import AppKit
import SwiftUI
import UniformTypeIdentifiers
import UserNotifications

/// Flexible metadata stays lossless when opening old sessions or new backend model packs.
struct NativeRecord: Identifiable {
    var values: [String: Any]
    var id: String { string("id") }
    func string(_ key: String, _ fallback: String = "") -> String { values[key] as? String ?? fallback }
    func number(_ key: String, _ fallback: Double = 0) -> Double { (values[key] as? NSNumber)?.doubleValue ?? fallback }
    func bool(_ key: String) -> Bool { values[key] as? Bool ?? false }
    func strings(_ key: String) -> [String] { values[key] as? [String] ?? [] }
    func object(_ key: String) -> [String: Any] { values[key] as? [String: Any] ?? [:] }
    func records(_ key: String) -> [NativeRecord] { (values[key] as? [[String: Any]] ?? []).map { NativeRecord(values: $0) } }
    var pending: Bool { ["queued", "running"].contains(string("status")) }
    var prompt: String { string("prompt", object("payload")["prompt"] as? String ?? "") }
}

struct NativeSessionEntry: Identifiable {
    let project: NativeRecord
    let session: NativeRecord
    var id: String { project.id + "/" + session.id }
}

enum NativePage: String, CaseIterable, Identifiable {
    case compose = "Compose", library = "Library", projects = "Projects", avatars = "Avatars", models = "Models"
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .compose: return "sparkles"
        case .library: return "photo.on.rectangle.angled"
        case .projects: return "folder"
        case .avatars: return "person.crop.square"
        case .models: return "cube.transparent"
        }
    }
}

struct NativeDraft: Codable {
    var prompt = ""
    var negative_prompt = ""
    var reference_paths: [String] = []
    var width: Int? = nil
    var height: Int? = nil
    var match_reference_size = true
    var output_resolution = 1024
    var steps = 40
    var guidance = 1.0
    var seed: Int? = nil
    var quantize: Int? = 4
    var use_kv_cache = true
    var low_ram = false
    var vae_tiling = false
    var mlx_cache_limit_gb: Double? = nil
    var preview_interval = 1
    var model_source = "mlx-q4"
    var model_path: String? = nil
    var project_id: String? = nil
    var project_session_id: String? = nil
    var save_metadata = true
    var output_dir: String? = nil
    var output_name: String? = nil

    var payload: [String: Any] {
        get throws {
            let encoded = try JSONEncoder().encode(self)
            return try JSONSerialization.jsonObject(with: encoded) as? [String: Any] ?? [:]
        }
    }

    static func restore(_ values: [String: Any]) throws -> NativeDraft {
        var defaults = try NativeDraft().payload
        defaults.merge(values) { _, new in new }
        // The Python schema stores an absent negative prompt as JSON null.
        if defaults["negative_prompt"] is NSNull { defaults["negative_prompt"] = "" }
        return try JSONDecoder().decode(NativeDraft.self, from: JSONSerialization.data(withJSONObject: defaults))
    }
}

struct NativeAPI {
    let info: BackendInfo
    func request(_ path: String, method: String = "GET", body: [String: Any]? = nil) async throws -> NativeRecord {
        guard let url = URL(string: info.baseURL + path) else { throw NativeError("Invalid local service address") }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 120
        request.setValue(info.token, forHTTPHeaderField: "X-Refract-Token")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse else { throw NativeError("The service returned no response") }
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
        guard (200..<300).contains(response.statusCode) else {
            let detail = object["detail"] as? String ?? (object["detail"] as? [[String: Any]])?.compactMap { $0["msg"] as? String }.joined(separator: "; ")
            throw NativeError(detail ?? "Local service error \(response.statusCode)")
        }
        return NativeRecord(values: object)
    }
    func eventsURL(_ id: String, since: Int) -> URL {
        var url = URLComponents(string: info.baseURL + "/api/jobs/\(id)/events")!
        url.queryItems = [URLQueryItem(name: "token", value: info.token), URLQueryItem(name: "since", value: String(since))]
        return url.url!
    }
}

struct NativeError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

@MainActor
final class NativeStore: ObservableObject {
    @Published var page: NativePage = .compose
    @Published var draft = NativeDraft() { didSet { scheduleSave() } }
    @Published var ready = false
    @Published var error: String?
    @Published var notice: String?
    @Published var health = NativeRecord(values: [:])
    @Published var system = NativeRecord(values: [:])
    @Published var sources: [NativeRecord] = []
    @Published var projects: [NativeRecord] = []
    @Published var avatars: [NativeRecord] = []
    @Published var library: [NativeRecord] = []
    @Published private var sessionHistory: [String: [NativeRecord]] = [:]
    @Published var jobs: [NativeRecord] = []
    @Published var encoders: [NativeRecord] = []
    @Published var selectedResult: NativeRecord?
    @Published var viewingImage: NativeRecord?
    @Published var openingSession: String?
    @Published var settingsOpen = false
    @Published var queueOpen = false
    @Published var shortcutsOpen = false
    @Published var submitting = false
    @Published var search = ""
    @Published var favoritesOnly = false
    @Published var libraryProject: String?
    @Published var appearance: String = "system" {
        didSet { preferences.set(appearance, forKey: "native.appearance") }
    }
    @Published var advancedOpen = false
    private(set) var api: NativeAPI?
    private var streams: [String: Task<Void, Never>] = [:]
    private var monitor: Task<Void, Never>?
    private var autosave: Task<Void, Never>?
    private var pendingSave: Task<NativeRecord, Error>?
    private var navigationID = UUID()
    private var managedReferences: [String: String] = [:]
    private var restoring = false
    private var activity: NSObjectProtocol?
    private var etaAnchors: [String: (seconds: Double, received: Date)] = [:]
    private var notificationsRequested = false
    private var noticeTask: Task<Void, Never>?
    private let preferences = (Launch.smoke || Launch.lifecycleClose) ? UserDefaults(suiteName: "local.refract.smoke.\(ProcessInfo.processInfo.processIdentifier)")! : UserDefaults.standard

    var pending: [NativeRecord] { jobs.filter { $0.string("kind") == "generate" && $0.pending }.sorted { $0.number("created_at") < $1.number("created_at") } }
    var currentJob: NativeRecord? { pending.first { $0.string("status") == "running" } ?? pending.first }
    var modelTasks: [NativeRecord] { jobs.filter { $0.string("kind") != "generate" && $0.pending } }
    var queueTasks: [NativeRecord] {
        jobs.filter(\.pending).sorted {
            if $0.string("status") != $1.string("status") { return $0.string("status") == "running" }
            return $0.number("created_at") < $1.number("created_at")
        }
    }
    var recentTasks: [NativeRecord] {
        Array(jobs.filter { !$0.pending }.sorted { $0.number("created_at") > $1.number("created_at") }.prefix(20))
    }
    var lastRun: NativeRecord? { jobs.filter { $0.string("kind") == "generate" && !$0.pending }.max { $0.number("finished_at") < $1.number("finished_at") } }
    var workspaceJob: NativeRecord? {
        if let selectedResult { return jobs.first { $0.id == selectedResult.id } ?? selectedResult }
        if let project = draft.project_id {
            return results(project: project, session: draft.project_session_id ?? "s1").first
        }
        return currentJob ?? lastRun
    }
    var activeProject: NativeRecord? { projects.first { $0.id == draft.project_id } }
    var visibleLibrary: [NativeRecord] {
        library.filter { item in
            (!favoritesOnly || item.bool("favorite")) &&
            (libraryProject == nil || item.string("project_id") == libraryProject) &&
            (search.isEmpty || item.prompt.localizedCaseInsensitiveContains(search))
        }
    }
    var sessions: [NativeSessionEntry] {
        projects.flatMap { project in project.records("sessions").map { NativeSessionEntry(project: project, session: $0) } }
            .sorted { $0.session.number("updated_at") > $1.session.number("updated_at") }
    }

    func results(project: String, session: String) -> [NativeRecord] {
        // The library survives restarts and contains results outside the recent-job window.
        var records = sessionHistory[project + "/" + session] ?? []
        for item in library.filter({
            $0.string("project_id") == project && ($0.object("params")["project_session_id"] as? String ?? "s1") == session
        }) { records.removeAll { $0.id == item.id }; records.append(item) }
        for job in jobs where job.string("kind") == "generate" &&
            job.object("payload")["project_id"] as? String == project &&
            (job.object("payload")["project_session_id"] as? String ?? "s1") == session {
            records.removeAll { $0.id == job.id }; records.append(job)
        }
        return records.sorted { $0.number("created_at") > $1.number("created_at") }
    }

    func sessionTitle(_ session: NativeRecord) -> String {
        let prompt = (session.object("session")["prompt"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return prompt.isEmpty ? "New draft" : prompt
    }

    func sessionThumbnail(project: String, session: NativeRecord) -> String {
        results(project: project, session: session.id).first?.strings("outputs").first ?? session.strings("references").first ?? ""
    }

    init() {
        appearance = preferences.string(forKey: "native.appearance") ?? "system"
        if let data = preferences.data(forKey: "native.draft"), let saved = try? JSONDecoder().decode(NativeDraft.self, from: data) { draft = saved }
        else if let model = Install.load()?.model { draft.model_source = model; draft.steps = model == "flux2-klein-4b" ? 4 : 40 }
    }

    func connect(_ info: BackendInfo) async {
        api = NativeAPI(info: info)
        await perform {
            try await self.refresh()
            if let id = self.draft.project_id, self.projects.contains(where: { $0.id == id }) {
                try await self.openProject(id, session: self.draft.project_session_id, saveCurrent: false)
            } else if self.draft.project_id != nil {
                self.draft.project_id = nil
                self.draft.project_session_id = nil
            }
            self.ready = true
            for job in self.jobs where job.pending { self.track(job) }
            self.startMonitor()
        }
    }

    func refresh() async throws {
        guard let api else { throw NativeError("The local service is not connected") }
        async let h = api.request("/api/health")
        async let s = api.request("/api/system")
        async let m = api.request("/api/sources")
        async let p = api.request("/api/projects")
        async let a = api.request("/api/avatars")
        async let l = api.request("/api/library")
        async let j = api.request("/api/jobs?limit=200")
        async let e = api.request("/api/encoders")
        let results = try await (h, s, m, p, a, l, j, e)
        health = results.0; system = results.1; sources = results.2.records("sources")
        projects = results.3.records("projects"); avatars = results.4.records("avatars")
        library = results.5.records("items"); jobs = results.6.records("jobs"); encoders = results.7.records("encoders")
        for job in jobs where job.pending { updateETA(job) }
    }

    func perform(_ action: @escaping () async throws -> Void) async {
        do { try await action() } catch is CancellationError { } catch { self.error = error.localizedDescription }
    }

    func message(_ text: String) {
        noticeTask?.cancel()
        notice = text
        noticeTask = Task { try? await Task.sleep(nanoseconds: 4_000_000_000); if !Task.isCancelled { notice = nil } }
    }

    private func scheduleSave() {
        guard !restoring else { return }
        if let data = try? JSONEncoder().encode(draft) { preferences.set(data, forKey: "native.draft") }
        autosave?.cancel()
        guard ready, draft.project_id != nil else { return }
        autosave = Task {
            do {
                try await Task.sleep(nanoseconds: 900_000_000)
                try Task.checkCancellation()
                try await saveProject()
            } catch is CancellationError { } catch { self.error = "Could not save session: \(error.localizedDescription)" }
        }
    }

    func saveProject() async throws {
        guard let save = try enqueueSave(draft) else { return }
        _ = try await save.value
    }

    private func enqueueSave(_ snapshot: NativeDraft) throws -> Task<NativeRecord, Error>? {
        guard let api, let id = snapshot.project_id else { return nil }
        var session = try snapshot.payload
        session.removeValue(forKey: "reference_paths")
        let sessionID = snapshot.project_session_id ?? "s1"
        // Update the cached draft before navigation. Rapid clicks never restore stale text.
        if var project = projects.first(where: { $0.id == id }) {
            var entries = project.records("sessions").map(\.values)
            if let index = entries.firstIndex(where: { $0["id"] as? String == sessionID }) {
                entries[index]["session"] = session; entries[index]["references"] = snapshot.reference_paths
                project.values["sessions"] = entries
                replaceProject(project)
            }
        }
        let previous = pendingSave
        // Writes are ordered and independent of the cancellable debounce task. Cancelling a
        // navigation must not cancel a save halfway through copying managed references.
        let task = Task { @MainActor in
            if let previous { _ = await previous.result }
            let references = snapshot.reference_paths.map { original in
                let key = id + "/" + sessionID + "/" + original
                if let managed = managedReferences[key], FileManager.default.fileExists(atPath: managed) { return managed }
                return original
            }
            let saved = try await api.request("/api/projects/\(id)", method: "PUT", body: [
                "session": session, "references": references, "session_id": sessionID
            ])
            if saved.strings("references").count == snapshot.reference_paths.count {
                for (original, managed) in zip(snapshot.reference_paths, saved.strings("references")) {
                    managedReferences[id + "/" + sessionID + "/" + original] = managed
                }
            }
            // Merge only this saved session; another session may have unsaved local edits.
            if var cached = projects.first(where: { $0.id == id }) {
                var entries = cached.records("sessions").map(\.values)
                if let index = entries.firstIndex(where: { $0["id"] as? String == sessionID }),
                   let entry = saved.records("sessions").first(where: { $0.id == sessionID }),
                   (entries[index]["references"] as? [String]) == snapshot.reference_paths,
                   NSDictionary(dictionary: entries[index]["session"] as? [String: Any] ?? [:]).isEqual(to: session) {
                    entries[index] = entry.values
                }
                cached.values["sessions"] = entries
                if cached.string("active_session_id", "s1") == sessionID, let active = entries.first(where: { $0["id"] as? String == sessionID }) {
                    cached.values["session"] = active["session"]
                    cached.values["references"] = active["references"]
                    cached.values["prompt"] = (active["session"] as? [String: Any])?["prompt"]
                }
                replaceProject(cached)
            }
            if draft.project_id == id && (draft.project_session_id ?? "s1") == sessionID && draft.reference_paths == snapshot.reference_paths {
                restoring = true; draft.reference_paths = saved.strings("references"); restoring = false
                persistDraft()
            }
            return saved
        }
        pendingSave = task
        return task
    }

    private func persistDraft() {
        if let data = try? JSONEncoder().encode(draft) { preferences.set(data, forKey: "native.draft") }
    }

    private func replaceProject(_ project: NativeRecord) {
        // Keep rows stable while saving or opening; moving the clicked row feels like a jump.
        if let index = projects.firstIndex(where: { $0.id == project.id }) { projects[index] = project }
        else { projects.insert(project, at: 0) }
    }

    func openProject(_ id: String, session: String? = nil, saveCurrent: Bool = true) async throws {
        guard let api else { return }
        let requestID = UUID()
        navigationID = requestID
        if saveCurrent, draft.project_id == id, session == draft.project_session_id {
            selectedResult = nil; page = .compose
            return
        }
        autosave?.cancel()
        let save = saveCurrent ? try enqueueSave(draft) : nil
        openingSession = id + "/" + (session ?? "")
        defer { if navigationID == requestID { openingSession = nil } }
        // Existing sessions open from memory immediately rather than waiting for disk/network.
        let cached = projects.first(where: { $0.id == id })
        var project: NativeRecord
        if let cached { project = cached } else { project = try await api.request("/api/projects/\(id)") }
        guard navigationID == requestID else { return }
        let targetSession = session ?? project.string("active_session_id", "s1")
        guard let entry = project.records("sessions").first(where: { $0.id == targetSession }) else {
            throw NativeError("This session is no longer available.")
        }
        project.values["session"] = entry.object("session")
        project.values["references"] = entry.strings("references")
        project.values["active_session_id"] = targetSession
        var values = project.object("session")
        values["reference_paths"] = project.strings("references")
        values["project_id"] = id
        values["project_session_id"] = project.string("active_session_id", "s1")
        let restored = try NativeDraft.restore(values)
        restoring = true; draft = restored; restoring = false
        selectedResult = nil
        replaceProject(project)
        persistDraft()
        page = .compose
        // Fetch this session even when its output is older than the global library page.
        let history = try await api.request("/api/library?project_id=\(id)&project_session_id=\(targetSession)")
        sessionHistory[id + "/" + targetSession] = history.records("items")
        // Saving the departed session continues without holding the visible workspace hostage.
        if let save { _ = try await save.value }
    }

    func createProject(name: String = "Untitled project") async throws {
        guard let api else { return }
        autosave?.cancel(); try await saveProject()
        let project = try await api.request("/api/projects", method: "POST", body: ["name": name])
        replaceProject(project)
        try await openProject(project.id, saveCurrent: false)
        page = .compose
    }

    func newSession() async throws {
        guard let api else { return }
        autosave?.cancel(); try await saveProject()
        if let id = draft.project_id {
            let project = try await api.request("/api/projects/\(id)/sessions", method: "POST")
            replaceProject(project)
            try await openProject(id, session: project.string("active_session_id"), saveCurrent: false)
        } else {
            let model = draft.model_source, output = draft.output_dir
            draft = NativeDraft(); draft.model_source = model; draft.output_dir = output
            if model == "flux2-klein-4b" { draft.steps = 4 }
            selectedResult = nil; page = .compose
        }
    }

    func addReferences(_ paths: [String]) {
        let valid = paths.filter { NSImage(contentsOfFile: $0) != nil }
        let combined = draft.reference_paths + valid.filter { !draft.reference_paths.contains($0) }
        if combined.count > 10 { error = "The model supports at most 10 reference images." }
        draft.reference_paths = Array(combined.prefix(10)); selectedResult = nil; page = .compose
    }

    func generate() async throws {
        guard !submitting, !draft.prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, let api else { return }
        submitting = true; defer { submitting = false }
        autosave?.cancel()
        let snapshot = draft
        if draft.project_id == nil {
            let project = try await api.request("/api/projects", method: "POST", body: ["name": String(draft.prompt.prefix(60))])
            replaceProject(project)
            restoring = true
            draft.project_id = project.id; draft.project_session_id = project.string("active_session_id", "s1")
            restoring = false
        }
        // Creating a project must not erase the prompt or references being submitted.
        draft.prompt = snapshot.prompt; draft.reference_paths = snapshot.reference_paths
        autosave?.cancel(); try await saveProject()
        let job = try await api.request("/api/jobs", method: "POST", body: ["kind": "generate", "payload": try draft.payload])
        selectedResult = nil; page = .compose; track(job)
        message(job.string("status") == "queued" ? "Added to the generation queue" : "Generation started")
    }

    func cancel(_ job: NativeRecord? = nil) async throws {
        guard let api, let job = job ?? currentJob, job.pending else { return }
        _ = try await api.request("/api/jobs/\(job.id)/cancel", method: "POST")
        message("Cancelling task…")
    }

    func selectModel(_ source: NativeRecord, path: String? = nil) async throws {
        guard let api else { return }
        draft.model_source = source.id; draft.model_path = path
        draft.steps = source.id == "flux2-klein-4b" ? 4 : 40
        _ = try await api.request("/api/models/select", method: "POST", body: ["model_source": source.id, "model_path": path ?? NSNull()])
    }

    func submit(_ kind: String, payload: [String: Any]) async throws {
        guard let api else { return }
        track(try await api.request("/api/jobs", method: "POST", body: ["kind": kind, "payload": payload]))
    }

    func mutate(_ path: String, method: String = "POST", body: [String: Any]? = nil) async throws {
        guard let api else { return }
        _ = try await api.request(path, method: method, body: body)
        if method == "DELETE", path.hasPrefix("/api/library/") {
            let id = String(path.dropFirst("/api/library/".count).split(separator: "?")[0])
            for key in Array(sessionHistory.keys) { sessionHistory[key]?.removeAll { $0.id == id } }
        }
        try await refreshCatalogs()
    }

    func refreshCatalogs() async throws {
        guard let api else { return }
        async let l = api.request("/api/library")
        async let p = api.request("/api/projects")
        async let a = api.request("/api/avatars")
        async let s = api.request("/api/sources")
        async let e = api.request("/api/encoders")
        let values = try await (l, p, a, s, e)
        library = values.0.records("items"); projects = values.1.records("projects")
        avatars = values.2.records("avatars"); sources = values.3.records("sources"); encoders = values.4.records("encoders")
    }

    func refine(_ item: NativeRecord) async throws {
        autosave?.cancel(); try await saveProject()
        let source = item.object("params").isEmpty ? item.object("payload") : item.object("params")
        var next = try NativeDraft.restore(source)
        next.prompt = ""; next.reference_paths = Array(item.strings("outputs").prefix(1)); next.seed = nil
        // Refinement is a new session, not an overwrite of the original saved setup.
        if let project = item.string("project_id").isEmpty ? draft.project_id : item.string("project_id") {
            try await openProject(project, saveCurrent: false)
            try await newSession()
            next.project_id = draft.project_id; next.project_session_id = draft.project_session_id
        } else {
            next.project_id = nil; next.project_session_id = nil
        }
        draft = next; selectedResult = nil; viewingImage = nil; page = .compose
    }

    func replay(_ item: NativeRecord) async throws {
        guard let api else { return }
        track(try await api.request("/api/library/\(item.id)/replay", method: "POST"))
        message("Added variation to the queue")
    }

    func showActiveJob() async throws {
        guard let job = currentJob else { return }
        try await inspectJob(job)
    }

    func inspectJob(_ job: NativeRecord) async throws {
        if job.string("kind") != "generate" { queueOpen = false; page = .models; return }
        if let project = job.object("payload")["project_id"] as? String {
            try await openProject(project, session: job.object("payload")["project_session_id"] as? String)
        }
        selectedResult = job; queueOpen = false; page = .compose
    }

    func elapsed(_ job: NativeRecord, now: Date) -> Double {
        if !job.pending { return job.number("elapsed_seconds", job.number("duration_s")) }
        guard job.string("status") == "running", job.number("started_at") > 0 else { return 0 }
        return max(0, now.timeIntervalSince1970 - job.number("started_at"))
    }

    func remaining(_ job: NativeRecord, now: Date) -> String {
        guard job.string("status") == "running" else { return "Waiting in queue" }
        guard job.string("phase") == "denoise" else { return phaseName(job.string("phase")) }
        guard job.number("step") < job.number("total_steps") else { return "Finishing preview…" }
        guard let anchor = etaAnchors[job.id] else { return "Estimating after first step…" }
        let left = anchor.seconds - now.timeIntervalSince(anchor.received)
        return left > 0 ? "≈\(duration(left)) left" : "Updating estimate…"
    }

    private func updateETA(_ job: NativeRecord) {
        if let value = job.values["eta_seconds"] as? NSNumber {
            etaAnchors[job.id] = (value.doubleValue, Date())
        } else { etaAnchors.removeValue(forKey: job.id) }
    }

    private func fold(_ event: [String: Any], id: String) {
        guard let index = jobs.firstIndex(where: { $0.id == id }) else { return }
        var job = jobs[index]
        job.values.merge(event) { _, new in new }
        if event["eta_seconds"] != nil { updateETA(job) }
        jobs[index] = job
    }

    private func upsert(_ job: NativeRecord) {
        jobs.removeAll { $0.id == job.id }; jobs.append(job)
        updateETA(job); updateActivity()
    }

    func track(_ job: NativeRecord) {
        upsert(job)
        guard job.pending, streams[job.id] == nil, let api else { return }
        streams[job.id] = Task { [weak self] in
            guard let self else { return }
            var cursor = Int(job.number("seq"))
            var disconnected = false
            while !Task.isCancelled {
                do {
                    var request = URLRequest(url: api.eventsURL(job.id, since: cursor))
                    request.timeoutInterval = 3600
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw NativeError("Progress stream was rejected") }
                    for try await line in bytes.lines {
                        try Task.checkCancellation()
                        guard line.hasPrefix("data: "), let data = String(line.dropFirst(6)).data(using: .utf8),
                              let event = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
                        cursor = max(cursor, (event["seq"] as? Int) ?? cursor)
                        fold(event, id: job.id)
                        if event["final"] as? Bool == true {
                            let finished = try await api.request("/api/jobs/\(job.id)")
                            upsert(finished)
                            if finished.string("status") == "done", finished.string("kind") == "install", let path = finished.object("result")["path"] as? String {
                                draft.model_source = "custom"; draft.model_path = path
                            }
                            try await refreshCatalogs()
                            message(finished.string("status") == "done" ? (job.string("kind") == "generate" ? "Image ready — saved locally" : "Model task finished") : finished.string("message"))
                            if finished.string("status") == "failed" { error = finished.string("error", finished.string("message")) }
                            notify(finished)
                            streams.removeValue(forKey: job.id)
                            return
                        }
                    }
                } catch is CancellationError { return } catch {
                    if !disconnected { message("Progress connection interrupted; reconnecting…"); disconnected = true }
                }
                do {
                    let snapshot = try await api.request("/api/jobs/\(job.id)")
                    for event in snapshot.records("events") where Int(event.number("seq")) > cursor {
                        cursor = Int(event.number("seq")); fold(event.values, id: job.id)
                    }
                    upsert(snapshot)
                    if !snapshot.pending {
                        try await refreshCatalogs(); notify(snapshot); streams.removeValue(forKey: job.id); return
                    }
                    try await Task.sleep(nanoseconds: 1_000_000_000)
                } catch is CancellationError { return } catch { self.error = "Progress unavailable: \(error.localizedDescription)"; try? await Task.sleep(nanoseconds: 2_000_000_000) }
            }
        }
    }

    private func startMonitor() {
        monitor?.cancel()
        monitor = Task { [weak self] in
            while !Task.isCancelled {
                do {
                    try await Task.sleep(nanoseconds: 5_000_000_000)
                    guard let self, let api = self.api else { return }
                    self.system = try await api.request("/api/system")
                } catch is CancellationError { return } catch { self?.error = "Machine status unavailable: \(error.localizedDescription)" }
            }
        }
    }

    private func updateActivity() {
        let busy = jobs.contains(where: \.pending)
        if busy, activity == nil {
            activity = ProcessInfo.processInfo.beginActivity(options: [.userInitiated, .idleSystemSleepDisabled], reason: "Local image generation")
        } else if !busy, let activity { ProcessInfo.processInfo.endActivity(activity); self.activity = nil }
    }

    private func notify(_ job: NativeRecord) {
        guard !Launch.smoke, !NSApp.isActive, Bundle.main.bundleIdentifier != nil else { return }
        let center = UNUserNotificationCenter.current()
        if !notificationsRequested { notificationsRequested = true; center.requestAuthorization(options: [.alert, .sound]) { _, _ in } }
        let content = UNMutableNotificationContent()
        content.title = "Refract Image · \(phaseName(job.string("status")))"
        content.body = job.string("error", job.string("kind") == "generate" ? "Your image is ready." : "Model task finished.")
        center.add(UNNotificationRequest(identifier: job.id, content: content, trigger: nil))
    }

    func stop() {
        monitor?.cancel(); autosave?.cancel(); noticeTask?.cancel()
        for task in streams.values { task.cancel() }; streams.removeAll()
        if let activity { ProcessInfo.processInfo.endActivity(activity); self.activity = nil }
        if Launch.smoke || Launch.lifecycleClose { preferences.removePersistentDomain(forName: "local.refract.smoke.\(ProcessInfo.processInfo.processIdentifier)") }
    }

    func menu(_ command: String) {
        Task { await perform {
            switch command {
            case "new": try await self.newSession()
            case "new-project": try await self.createProject()
            case "next-project":
                if let index = self.projects.firstIndex(where: { $0.id == self.draft.project_id }), !self.projects.isEmpty {
                    try await self.openProject(self.projects[(index + 1) % self.projects.count].id)
                } else { self.page = .projects }
            case "generate": try await self.generate()
            case "cancel": try await self.cancel()
            case "settings": self.settingsOpen = true
            case "shortcuts": self.shortcutsOpen = true
            case "save-as": if let path = self.workspaceJob?.strings("outputs").first { NativeFiles.save(path, store: self) }
            case "reveal": if let path = self.workspaceJob?.strings("outputs").first { NativeFiles.reveal(path) }
            default: if let page = NativePage.allCases.first(where: { $0.rawValue.lowercased() == command }) { self.page = page }
            }
        } }
    }
}

@MainActor
enum NativeFiles {
    static func pickImages(_ completion: @escaping ([String]) -> Void) {
        let panel = NSOpenPanel(); panel.allowedContentTypes = [.image]; panel.allowsMultipleSelection = true
        panel.prompt = "Add references"
        panel.begin { if $0 == .OK { completion(panel.urls.map(\.path)) } }
    }
    static func pickDirectory(_ completion: @escaping (String) -> Void) {
        let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.canCreateDirectories = true
        panel.begin { if $0 == .OK, let path = panel.url?.path { completion(path) } }
    }
    static func pickPack(_ completion: @escaping (String) -> Void) {
        let panel = NSOpenPanel(); panel.allowedContentTypes = [UTType(filenameExtension: "safetensors") ?? .data]
        panel.begin { if $0 == .OK, let path = panel.url?.path { completion(path) } }
    }
    static func reveal(_ path: String) { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)]) }
    static func copy(_ path: String, store: NativeStore) {
        guard let image = NSImage(contentsOfFile: path) else { store.error = "The image could not be read."; return }
        NSPasteboard.general.clearContents()
        if NSPasteboard.general.writeObjects([image]) { store.message("Image copied") } else { store.error = "Could not copy image." }
    }
    static func save(_ path: String, store: NativeStore) {
        let panel = NSSavePanel(); panel.allowedContentTypes = [.png]; panel.nameFieldStringValue = URL(fileURLWithPath: path).lastPathComponent
        panel.begin { response in
            guard response == .OK, let target = panel.url else { return }
            do {
                let source = URL(fileURLWithPath: path)
                if source.standardizedFileURL == target.standardizedFileURL { return }
                // Atomic write honors the Save panel's overwrite confirmation without deleting the source.
                try Data(contentsOf: source).write(to: target, options: .atomic)
                store.message("Image saved")
            } catch { store.error = error.localizedDescription }
        }
    }
}

func duration(_ seconds: Double) -> String {
    let whole = max(0, Int(ceil(seconds)))
    return whole >= 60 ? "\(whole / 60)m \(String(format: "%02d", whole % 60))s" : "\(whole)s"
}

func phaseName(_ phase: String) -> String {
    ["queued": "Queued", "resolving": "Starting", "loading": "Loading model", "encoding": "Reading prompt & references", "denoise": "Denoising", "decoding": "Decoding image", "saving": "Saving", "done": "Done", "failed": "Failed", "cancelled": "Cancelled", "downloading": "Downloading", "installing": "Installing", "validating": "Validating"][phase] ?? phase.capitalized
}
