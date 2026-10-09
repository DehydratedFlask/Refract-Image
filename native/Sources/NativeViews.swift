import AppKit
import SwiftUI
import UniformTypeIdentifiers

private let panelColor = Color(nsColor: .controlBackgroundColor)
private let canvasColor = Color(nsColor: .textBackgroundColor)

struct NativeRootView: View {
    @ObservedObject var store: NativeStore
    var body: some View {
        HStack(spacing: 0) {
            NativeSidebar(store: store).frame(width: 236)
            Divider()
            VStack(spacing: 0) {
                workspaceHeader
                Divider()
                if !store.ready {
                    NativeEmpty(symbol: "sparkles", title: "Starting your local studio", detail: "Connecting to the image engine on this Mac…")
                } else {
                    switch store.page {
                    case .compose: NativeCompose(store: store)
                    case .library: NativeLibrary(store: store)
                    case .projects: NativeProjects(store: store)
                    case .avatars: NativeAvatars(store: store)
                    case .models: NativeModels(store: store)
                    }
                }
                if let notice = store.notice {
                    HStack { Image(systemName: "info.circle"); Text(notice); Spacer(); Button { store.notice = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain) }
                        .font(.callout).padding(10).background(panelColor)
                }
            }.frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .tint(.blue)
        .preferredColorScheme(store.appearance == "system" ? nil : store.appearance == "dark" ? .dark : .light)
        .sheet(item: $store.viewingImage) { NativeImageViewer(store: store, item: $0) }
        .sheet(isPresented: $store.queueOpen) { NativeQueuePanel(store: store) }
        .sheet(isPresented: $store.settingsOpen) { NativeSettings(store: store) }
        .sheet(isPresented: $store.shortcutsOpen) {
            VStack(alignment: .leading, spacing: 16) {
                Text("Keyboard shortcuts").font(.title2.bold())
                ForEach(["⌘N · New generation", "⌘P · New project", "⌘J · Next project", "⌘Return · Generate", "⌘. / Escape · Cancel generation", "⌘1–4 · Switch workspace", "⇧⌘S · Save result as", "⇧⌘R · Reveal in Finder", "⌘, · Settings"], id: \.self) { Text($0).monospaced() }
                Button("Done") { store.shortcutsOpen = false }.keyboardShortcut(.defaultAction)
            }.padding(28).frame(width: 420)
        }
        .alert("Refract Image", isPresented: Binding(get: { store.error != nil }, set: { if !$0 { store.error = nil } })) {
            Button("OK", role: .cancel) { store.error = nil }
        } message: { Text(store.error ?? "") }
    }
    private var workspaceHeader: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(store.page == .compose ? (store.activeProject?.string("name") ?? "Compose") : store.page.rawValue).font(.title3.weight(.semibold))
                Text(store.page == .compose ? "Reference-guided image editing · on your Mac" : subtitles[store.page] ?? "").font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            if store.health.bool("mock") { Label("Mock engine", systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange) }
            if let job = store.currentJob, store.page != .compose {
                Button { run { try await store.showActiveJob() } } label: { Label("Show live preview", systemImage: "waveform.path") }
                    .help("Return to the running generation")
                    .accessibilityIdentifier("show-live-preview")
                Text("\(Int(job.number("step")))/\(Int(job.number("total_steps")))").monospacedDigit().font(.caption).foregroundStyle(.secondary)
            }
            Button { store.queueOpen = true } label: { Label(store.queueTasks.isEmpty ? "Queue" : "Queue · \(store.queueTasks.count)", systemImage: "list.bullet.rectangle") }.accessibilityIdentifier("open-queue").help("Progress of every queued task")
            Button { run { try await store.newSession() } } label: { Label("New session", systemImage: "plus") }.help("New generation session (⌘N)")
            Button { store.settingsOpen = true } label: { Image(systemName: "gearshape") }.help("Settings (⌘,)").accessibilityLabel("Settings")
        }.padding(.horizontal, 20).padding(.vertical, 14)
    }
    private var subtitles: [NativePage: String] { [.library: "Your images, saved locally", .projects: "Pick up exactly where you left off", .avatars: "Consistent subjects with @handles", .models: "Local models & text encoders"] }
    private func run(_ action: @escaping () async throws -> Void) { Task { await store.perform(action) } }
}

struct NativeSidebar: View {
    @ObservedObject var store: NativeStore
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 9) {
                Image(systemName: "camera.filters").font(.title2).foregroundStyle(.tint)
                VStack(alignment: .leading, spacing: 2) { Text("Refract Image").font(.headline); Text("LOCAL IMAGE STUDIO").font(.system(size: 9, weight: .semibold)).tracking(1.1).foregroundStyle(.secondary) }
                Spacer()
            }.padding(18)
            // Only navigation scrolls. Machine facts never participate in that scroll view.
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    VStack(spacing: 3) {
                        ForEach(NativePage.allCases) { page in
                            Button { store.page = page } label: {
                                HStack { Label(page.rawValue, systemImage: page.symbol); Spacer(); if page == .library { Text("\(store.library.count)").font(.caption).foregroundStyle(.secondary) } }
                                    .padding(.horizontal, 10).padding(.vertical, 9)
                                    .contentShape(Rectangle())
                                    .background(store.page == page ? Color.accentColor.opacity(0.15) : .clear, in: RoundedRectangle(cornerRadius: 7))
                            }.buttonStyle(.plain).accessibilityIdentifier("nav-\(page.rawValue.lowercased())")
                        }
                    }
                    VStack(alignment: .leading, spacing: 5) {
                        HStack { section("Projects"); Spacer(); Button { Task { await store.perform { try await store.createProject() } } } label: { Image(systemName: "plus") }.buttonStyle(.plain).help("New project (⌘P)") }
                        ForEach(store.projects) { project in
                            Button { Task { await store.perform { try await store.openProject(project.id) } } } label: {
                                HStack { Image(systemName: "folder"); Text(project.string("name")).lineLimit(1); Spacer(); Text("\(Int(project.number("generation_count")))").font(.caption).foregroundStyle(.secondary) }.padding(.vertical, 6)
                            }.buttonStyle(.plain).foregroundStyle(store.draft.project_id == project.id ? Color.accentColor : Color.primary)
                        }
                        if store.projects.isEmpty { Text("Create a project to save your work.").font(.caption).foregroundStyle(.secondary) }
                    }.padding(.horizontal, 10)
                    VStack(alignment: .leading, spacing: 6) {
                        section("Past sessions")
                        ForEach(store.sessions) { entry in
                            NativeSessionRow(store: store, project: entry.project, session: entry.session, compact: true)
                        }
                        if store.sessions.isEmpty { Text("Saved sessions will appear here.").font(.caption).foregroundStyle(.secondary) }
                    }.padding(.horizontal, 10)
                }.padding(.horizontal, 8).padding(.bottom, 14)
            }.frame(maxHeight: .infinity)
            Divider()
            NativeSystemStatus(store: store).padding(16).fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("pinned-machine-status")
        }
        .background(.regularMaterial)
    }
    private func section(_ text: String) -> some View { Text(text.uppercased()).font(.system(size: 10, weight: .semibold)).tracking(0.8).foregroundStyle(.secondary) }
}

struct NativeSessionRow: View {
    @ObservedObject var store: NativeStore
    let project: NativeRecord
    let session: NativeRecord
    var compact = false
    private var selected: Bool { store.draft.project_id == project.id && store.draft.project_session_id == session.id }
    var body: some View {
        Button { Task { await store.perform { try await store.openProject(project.id, session: session.id) } } } label: {
            HStack(spacing: 9) {
                NativeImage(path: store.sessionThumbnail(project: project.id, session: session), fit: false)
                    .frame(width: compact ? 40 : 60, height: compact ? 40 : 60).clipped().clipShape(RoundedRectangle(cornerRadius: 6))
                VStack(alignment: .leading, spacing: 4) {
                    Text(store.sessionTitle(session)).font(compact ? .caption : .callout.weight(.medium)).lineLimit(2)
                    if compact { Text(project.string("name")).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(1) }
                    else {
                        Text("\(session.strings("references").count) refs · \(store.results(project: project.id, session: session.id).filter { !$0.strings("outputs").isEmpty }.count) results · \((session.object("session")["steps"] as? Int) ?? 40) steps")
                            .font(.caption).foregroundStyle(.secondary)
                        Text(Date(timeIntervalSince1970: session.number("updated_at")), style: .relative).font(.caption2).foregroundStyle(.secondary)
                    }
                }
                Spacer(minLength: 0)
                if selected { Image(systemName: "checkmark").font(.caption).foregroundStyle(.tint) }
            }.frame(maxWidth: .infinity, alignment: .leading).padding(8).contentShape(Rectangle())
                .background(selected ? Color.accentColor.opacity(0.12) : Color.secondary.opacity(0.04), in: RoundedRectangle(cornerRadius: 7))
        }.buttonStyle(.plain).accessibilityIdentifier("session-\(project.id)-\(session.id)")
            .help("Restore prompt, references, settings, and results")
    }
}

struct NativeSystemStatus: View {
    @ObservedObject var store: NativeStore
    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            VStack(alignment: .leading, spacing: 9) {
                if let job = store.currentJob {
                    Button { Task { await store.perform { try await store.showActiveJob() } } } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            HStack { Circle().fill(.green).frame(width: 6, height: 6); Text(phaseName(job.string("phase"))).font(.caption.weight(.semibold)); Spacer(); Text("\(Int(job.number("step")))/\(Int(job.number("total_steps")))").font(.caption.monospacedDigit()) }
                            Text(store.remaining(job, now: context.date)).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                            ProgressView(value: job.number("step"), total: max(1, job.number("total_steps"))).controlSize(.small)
                        }
                    }.buttonStyle(.plain)
                    Divider()
                }
                fact("MLX peak", String(format: "%.1f GB", store.currentJob?.number("peak_memory_gb", store.system.number("mlx_peak_memory_gb")) ?? store.system.number("mlx_peak_memory_gb")))
                fact("Memory", store.ready ? "\(Int(store.system.number("total_ram_gb"))) GB" : "—")
                fact("Free disk", store.ready ? String(format: "%.1f GB", store.system.number("free_disk_gb")) : "—")
                if let job = store.currentJob { fact("Elapsed", duration(store.elapsed(job, now: context.date))) }
                else { fact("Last run", store.lastRun.map { duration($0.number("elapsed_seconds")) } ?? "—") }
                HStack(spacing: 6) {
                    Label(store.health.bool("mock") ? "mock" : "mflux \(store.health.string("mflux_version", "—"))", systemImage: "circle.fill")
                        .font(.system(size: 10)).foregroundStyle(store.health.bool("mock") ? .orange : .green)
                        .padding(.horizontal, 8).padding(.vertical, 4).background(Color.green.opacity(0.08), in: Capsule())
                    Text(store.system.string("chip", "Apple silicon").replacingOccurrences(of: "Apple ", with: "")).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }.padding(.top, 3)
            }
        }
    }
    private func fact(_ label: String, _ value: String) -> some View {
        HStack { Text(label).foregroundStyle(.secondary); Spacer(); Text(value).monospacedDigit() }.font(.caption)
    }
}

struct NativeCompose: View {
    @ObservedObject var store: NativeStore
    var body: some View {
        HSplitView {
            VStack(spacing: 0) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 22) {
                        if store.activeProject != nil {
                            Label("Session saved automatically", systemImage: "checkmark.circle").font(.caption).foregroundStyle(.secondary)
                        }
                        references
                        prompt
                        DisclosureGroup("Generation settings", isExpanded: $store.advancedOpen) { NativeGenerationSettings(store: store).padding(.top, 12) }
                        if !store.pending.isEmpty { queue }
                    }.padding(20)
                }
                Divider()
                generateFooter
            }.frame(minWidth: 320, idealWidth: 380, maxWidth: 480)
            NativeCanvas(store: store).frame(minWidth: 340, maxWidth: .infinity, maxHeight: .infinity)
        }
    }
    private var references: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack { Text("References").font(.headline); Text("\(store.draft.reference_paths.count)/10").font(.caption).foregroundStyle(.secondary); Spacer(); Button { NativeFiles.pickImages { store.addReferences($0) } } label: { Label("Add", systemImage: "plus") }.accessibilityIdentifier("add-references") }
            if store.draft.reference_paths.isEmpty {
                Button { NativeFiles.pickImages { store.addReferences($0) } } label: {
                    VStack(spacing: 8) { Image(systemName: "photo.badge.plus").font(.title2); Text("Drop images here").font(.callout.weight(.medium)); Text("Optional · edit or combine up to 10 images").font(.caption) }
                        .foregroundStyle(.secondary).frame(maxWidth: .infinity).padding(.vertical, 24)
                        .background(canvasColor, in: RoundedRectangle(cornerRadius: 10))
                        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Color.secondary.opacity(0.3), style: StrokeStyle(lineWidth: 1, dash: [5, 4])))
                }.buttonStyle(.plain)
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 78))], spacing: 10) {
                    ForEach(Array(store.draft.reference_paths.enumerated()), id: \.offset) { index, path in
                        ZStack(alignment: .topTrailing) {
                            NativeImage(path: path, fit: false).frame(height: 86).clipped().clipShape(RoundedRectangle(cornerRadius: 7))
                            Button { store.draft.reference_paths.remove(at: index) } label: { Image(systemName: "xmark.circle.fill").symbolRenderingMode(.palette).foregroundStyle(.white, .black.opacity(0.7)) }.buttonStyle(.plain).padding(4).accessibilityLabel("Remove reference \(index + 1)")
                        }.help(path).onDrag { NSItemProvider(contentsOf: URL(fileURLWithPath: path)) ?? NSItemProvider() }
                    }
                }
            }
        }.onDrop(of: [.fileURL], isTargeted: nil) { providers in
            for provider in providers { _ = provider.loadObject(ofClass: URL.self) { url, _ in if let url { Task { @MainActor in store.addReferences([url.path]) } } } }
            return !providers.isEmpty
        }
    }
    private var prompt: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack { Text("Your prompt").font(.headline); Spacer(); Text("⌘Return to generate").font(.caption).foregroundStyle(.secondary) }
            TextEditor(text: $store.draft.prompt).font(.body).scrollContentBackground(.hidden)
                .frame(minHeight: 145).padding(10).background(canvasColor, in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.secondary.opacity(0.2)))
                .accessibilityLabel("Generation prompt").accessibilityIdentifier("generation-prompt")
            Text(store.draft.reference_paths.isEmpty ? "Describe an image, or add references to guide an edit." : "Describe the change. Tell the model what to preserve.").font(.caption).foregroundStyle(.secondary)
            if !store.avatars.isEmpty {
                ScrollView(.horizontal) { HStack { ForEach(store.avatars) { avatar in Button("@\(avatar.string("handle"))") { store.draft.prompt += (store.draft.prompt.isEmpty ? "" : " ") + "@" + avatar.string("handle") }.buttonStyle(.bordered).controlSize(.small) } } }
            }
        }
    }
    private var queue: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack { Text("Queue · \(store.pending.count) tasks").font(.headline); Spacer(); Button("Show all") { store.queueOpen = true }.controlSize(.small) }
            TimelineView(.periodic(from: .now, by: 1)) { context in
                ForEach(store.pending) { job in
                    NativeQueueRow(store: store, job: job, now: context.date, compact: true)
                }
            }
        }.padding(12).background(panelColor, in: RoundedRectangle(cornerRadius: 8))
    }
    private var generateFooter: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack { Image(systemName: "cube").foregroundStyle(.secondary); Text(store.sources.first { $0.id == store.draft.model_source }?.string("label") ?? store.draft.model_source).lineLimit(1); Spacer(); Text("\(store.draft.steps) steps").foregroundStyle(.secondary) }.font(.caption)
            Button { run { try await store.generate() } } label: {
                HStack { Image(systemName: "sparkles"); Text(store.submitting ? "Adding task…" : store.pending.isEmpty ? "Generate image" : "Add to queue"); Spacer(); Text("⌘↵").opacity(0.75) }.frame(maxWidth: .infinity).padding(.vertical, 4)
            }.buttonStyle(.borderedProminent).controlSize(.large).disabled(!store.ready || store.submitting || store.draft.prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityIdentifier("generate-image")
            HStack { Text("Save to").foregroundStyle(.secondary); Text(URL(fileURLWithPath: store.draft.output_dir ?? store.system.string("outputs_dir", "Outputs")).lastPathComponent).lineLimit(1); Spacer(); Button("Choose…") { NativeFiles.pickDirectory { store.draft.output_dir = $0 } }.buttonStyle(.link) }.font(.caption)
        }.padding(16).background(.regularMaterial)
    }
    private func run(_ action: @escaping () async throws -> Void) { Task { await store.perform(action) } }
}

struct NativeGenerationSettings: View {
    @ObservedObject var store: NativeStore
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Picker("Model", selection: Binding(get: { store.draft.model_source }, set: { id in if let source = store.sources.first(where: { $0.id == id }) { Task { await store.perform { try await store.selectModel(source) } } } })) {
                ForEach(store.sources) { Text($0.string("label") + ($0.bool("available") ? "" : " · download needed")).tag($0.id) }
            }
            if ["prepared", "custom"].contains(store.draft.model_source) {
                HStack { TextField("Model directory", text: optionalString($store.draft.model_path)); Button("Choose…") { NativeFiles.pickDirectory { store.draft.model_path = $0 } } }
            }
            HStack { Stepper("Steps: \(store.draft.steps)", value: $store.draft.steps, in: 1...100); Spacer(); TextField("Guidance", value: $store.draft.guidance, format: .number).frame(width: 60).help("Guidance, from 1 to 20") }
            Text("Guidance").font(.caption).foregroundStyle(.secondary)
            Slider(value: $store.draft.guidance, in: 1...20, step: 0.5).accessibilityLabel("Guidance")
            Picker("Size", selection: $store.draft.output_resolution) { ForEach([256, 384, 512, 640, 768, 896, 1024, 1280, 1536, 2048], id: \.self) { Text("\($0) px").tag($0) } }
            Toggle("Match original reference size", isOn: $store.draft.match_reference_size)
            HStack { TextField("Width · auto", value: $store.draft.width, format: .number); Text("×"); TextField("Height · auto", value: $store.draft.height, format: .number) }.textFieldStyle(.roundedBorder)
            Text("Custom dimensions: 256–2048, in multiples of 32.").font(.caption).foregroundStyle(.secondary)
            TextField("Seed · blank for random", value: $store.draft.seed, format: .number).textFieldStyle(.roundedBorder)
            if store.draft.guidance > 1 { TextField("Negative prompt", text: $store.draft.negative_prompt).textFieldStyle(.roundedBorder) }
            Picker("Preview updates", selection: $store.draft.preview_interval) {
                Text("Live · every step").tag(1); Text("Every 2 steps").tag(2); Text("Every 5 steps").tag(5); Text("Every 10 steps").tag(10); Text("Off").tag(0)
                if ![0, 1, 2, 5, 10].contains(store.draft.preview_interval) { Text("Every \(store.draft.preview_interval) steps").tag(store.draft.preview_interval) }
            }
            Text("Previews decode each completed step. More frequent updates add VAE work.").font(.caption).foregroundStyle(.secondary)
            Toggle("Reuse prompt & reference cache", isOn: $store.draft.use_kv_cache)
            Toggle("Tile image decoding", isOn: $store.draft.vae_tiling)
            Toggle("Save metadata sidecar", isOn: $store.draft.save_metadata)
            TextField("MLX cache cap · GB, blank = uncapped", value: $store.draft.mlx_cache_limit_gb, format: .number).textFieldStyle(.roundedBorder)
        }.font(.callout)
    }
}

struct NativeCanvas: View {
    @ObservedObject var store: NativeStore
    var body: some View {
        VStack(spacing: 14) {
            if let job = store.workspaceJob, job.pending {
                TimelineView(.periodic(from: .now, by: 0.25)) { context in
                    VStack(spacing: 10) {
                        HStack(alignment: .top) {
                            VStack(alignment: .leading, spacing: 5) {
                                Label(phaseName(job.string("phase")), systemImage: "waveform.path").font(.headline)
                                Text(job.number("total_steps") > 0 ? "Step \(Int(job.number("step"))) of \(Int(job.number("total_steps")))" : "Preparing your generation").font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            VStack(alignment: .trailing, spacing: 5) {
                                Text(store.remaining(job, now: context.date)).font(.callout.weight(.medium)).monospacedDigit().accessibilityIdentifier("live-time-remaining")
                                Text("\(duration(store.elapsed(job, now: context.date))) elapsed").font(.caption).foregroundStyle(.secondary).monospacedDigit().accessibilityIdentifier("live-time-elapsed")
                            }
                            Button("Cancel", role: .destructive) { run { try await store.cancel(job) } }.keyboardShortcut(.cancelAction)
                        }
                        ProgressView(value: job.number("step"), total: max(1, job.number("total_steps")))
                    }
                }
                ZStack(alignment: .bottomLeading) {
                    RoundedRectangle(cornerRadius: 12).fill(canvasColor)
                    if !job.string("preview_path").isEmpty {
                        NativeImage(path: job.string("preview_path")).padding(12).accessibilityLabel("Live generation preview").accessibilityIdentifier("live-generation-preview")
                        Label("LIVE PREVIEW · STEP \(Int(job.number("step")))", systemImage: "circle.fill").font(.system(size: 10, weight: .semibold)).foregroundStyle(.green).padding(8).background(.regularMaterial, in: Capsule()).padding(20)
                    } else {
                        NativeEmpty(symbol: "sparkles", title: job.string("phase") == "queued" ? "Waiting in queue" : "Your image is taking shape", detail: job.object("payload")["preview_interval"] as? Int == 0 ? "Previews are disabled for this run." : "The first preview arrives after the first denoising step.")
                    }
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
                HStack { Text(job.string("message")).lineLimit(1); Spacer(); if job.number("seconds_per_step") > 0 { Text(String(format: "%.2f s/step", job.number("seconds_per_step"))).monospacedDigit() } }.font(.caption).foregroundStyle(.secondary)
            } else if let job = store.workspaceJob, !job.strings("outputs").isEmpty {
                NativeComparison(store: store, item: job).id(job.id)
            } else if let job = store.workspaceJob, ["failed", "cancelled"].contains(job.string("status")) {
                NativeEmpty(symbol: job.string("status") == "failed" ? "exclamationmark.triangle" : "stop.circle", title: phaseName(job.string("status")), detail: job.string("error", job.string("message")))
            } else {
                NativeEmpty(symbol: "camera.filters", title: "Make something your own", detail: "Describe an image or drop in references. Your result will appear here, ready to compare and refine.")
            }
        }.padding(20).frame(maxWidth: .infinity, maxHeight: .infinity).background(Color(nsColor: .windowBackgroundColor))
    }
    private func run(_ action: @escaping () async throws -> Void) { Task { await store.perform(action) } }
}

/// Loads away from the main thread, retains the last frame until its replacement is decoded,
/// and ignores stale completions. It never polls or reloads an unchanged preview path.
struct NativeImage: View {
    let path: String
    var fit = true
    @State private var image: NSImage?
    var body: some View {
        ZStack {
            if let image { Image(nsImage: image).resizable().aspectRatio(image.size.width / max(1, image.size.height), contentMode: fit ? .fit : .fill) }
            else { Rectangle().fill(Color.secondary.opacity(0.08)).overlay(Image(systemName: "photo").foregroundStyle(.secondary)) }
        }
        .task(id: path) {
            let loaded = await Task.detached(priority: .userInitiated) { () -> Data? in
                try? Data(contentsOf: URL(fileURLWithPath: path))
            }.value
            guard !Task.isCancelled else { return }
            if let loaded, let next = NSImage(data: loaded) { image = next }
        }
    }
}

struct NativeComparison: View {
    @ObservedObject var store: NativeStore
    let item: NativeRecord
    @State private var mode = "Result"
    @State private var reference = 0
    @State private var output = 0
    @State private var wipe = 0.5
    @State private var zoom = 1.0
    @State private var pan = CGPoint.zero
    var body: some View {
        VStack(spacing: 12) {
            Text(item.prompt).font(.callout).lineLimit(3).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
            HStack {
                Picker("Compare", selection: $mode) { Text("Result").tag("Result"); if !item.strings("references").isEmpty { Text("Side by side").tag("Side by side"); Text("Wipe").tag("Wipe") } }.pickerStyle(.segmented).frame(maxWidth: 280)
                if item.strings("references").count > 1 { Picker("Reference", selection: $reference) { ForEach(item.strings("references").indices, id: \.self) { Text("Ref \($0 + 1)").tag($0) } }.frame(width: 95) }
                if item.strings("outputs").count > 1 { Picker("Result", selection: $output) { ForEach(item.strings("outputs").indices, id: \.self) { Text("Result \($0 + 1)").tag($0) } }.frame(width: 100) }
            }
            HStack {
                NativeZoomControls(zoom: $zoom, pan: $pan)
                Spacer()
                Button { store.viewingImage = item } label: { Image(systemName: "arrow.up.left.and.arrow.down.right") }.help("Expand image").accessibilityLabel("Expand image")
            }
            HStack(spacing: 10) {
                if mode == "Side by side", let ref = item.strings("references").safe(reference) {
                    stage(ref, label: "REFERENCE")
                    stage(outputPath, label: "RESULT")
                } else {
                    stage(outputPath, label: "RESULT", reference: mode == "Wipe" ? item.strings("references").safe(reference) : nil)
                }
            }.frame(maxWidth: .infinity, maxHeight: .infinity)
            Text("Pinch to zoom · Scroll or drag to pan · Double-click or Fit to reset").font(.caption).foregroundStyle(.secondary)
            if mode == "Wipe" { Slider(value: $wipe, in: 0...1).accessibilityLabel("Comparison wipe position") }
            HStack { Text("\(Int(item.number("width")))×\(Int(item.number("height"))) · \(duration(item.number("elapsed_seconds", item.number("duration_s")))) · seed \((item.values["seeds"] as? [Int] ?? []).map(String.init).joined(separator: ", "))").lineLimit(1); Spacer() }.font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            HStack(spacing: 8) {
                Button { NativeFiles.copy(outputPath, store: store) } label: { Label("Copy", systemImage: "doc.on.doc") }
                Button { NativeFiles.reveal(outputPath) } label: { Label("Reveal", systemImage: "folder") }
                Button { NativeFiles.save(outputPath, store: store) } label: { Label("Save As", systemImage: "square.and.arrow.down") }
                Spacer(minLength: 0)
            }.controlSize(.small)
            HStack {
                Button("Refine as new session") { run { try await store.refine(item) } }
                Spacer()
                Button("Variation") { run { try await store.replay(item) } }
            }.controlSize(.small)
        }
    }
    private var outputPath: String { item.strings("outputs").safe(output) ?? item.strings("outputs").first ?? "" }
    private func stage(_ path: String, label: String, reference: String? = nil) -> some View {
        ZStack(alignment: .topLeading) {
            NativeImageViewport(path: path, referencePath: reference, wipe: wipe, zoom: $zoom, pan: $pan)
            Text(label).font(.system(size: 9, weight: .semibold)).tracking(1).padding(7).background(.regularMaterial, in: Capsule()).padding(10).allowsHitTesting(false)
        }.clipShape(RoundedRectangle(cornerRadius: 10))
    }
    private func run(_ action: @escaping () async throws -> Void) { Task { await store.perform(action) } }
}

struct NativeLibrary: View {
    @ObservedObject var store: NativeStore
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                TextField("Search prompts…", text: $store.search).textFieldStyle(.roundedBorder).frame(maxWidth: 300).accessibilityIdentifier("library-search")
                Toggle("Favourites", isOn: $store.favoritesOnly).toggleStyle(.button)
                Picker("Project", selection: Binding(get: { store.libraryProject ?? "" }, set: { store.libraryProject = $0.isEmpty ? nil : $0 })) { Text("All projects").tag(""); ForEach(store.projects) { Text($0.string("name")).tag($0.id) } }.frame(maxWidth: 230)
                Spacer(); Text("\(store.visibleLibrary.count) images").font(.caption).foregroundStyle(.secondary)
            }.padding(16)
            if store.visibleLibrary.isEmpty { NativeEmpty(symbol: "photo.on.rectangle", title: "No images here yet", detail: "Generate an image, or adjust your filters.") }
            else { ScrollView { LazyVGrid(columns: [GridItem(.adaptive(minimum: 170, maximum: 260))], spacing: 16) { ForEach(store.visibleLibrary) { item in tile(item) } }.padding(20) } }
        }
    }
    private func tile(_ item: NativeRecord) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Button { store.viewingImage = item } label: {
                NativeImage(path: item.strings("outputs").first ?? "", fit: false).frame(height: 170).clipped().clipShape(RoundedRectangle(cornerRadius: 9))
            }.buttonStyle(.plain).accessibilityLabel("Open image: \(item.prompt)").accessibilityIdentifier("library-image-\(item.id)")
            Text(item.prompt).lineLimit(2).font(.callout).frame(maxWidth: .infinity, alignment: .leading)
            HStack {
                Text(Date(timeIntervalSince1970: item.number("created_at")), style: .date).font(.caption).foregroundStyle(.secondary)
                Spacer(); Button { run { try await store.mutate("/api/library/\(item.id)/favorite", body: ["favorite": !item.bool("favorite")]) } } label: { Image(systemName: item.bool("favorite") ? "star.fill" : "star").foregroundStyle(item.bool("favorite") ? .yellow : .secondary) }.buttonStyle(.plain).accessibilityLabel("Toggle favourite")
            }
        }.padding(10).background(panelColor, in: RoundedRectangle(cornerRadius: 12))
        .contextMenu {
            Button("View image") { store.viewingImage = item }
            if !item.string("project_id").isEmpty {
                Button("Open saved session") { run { try await store.openProject(item.string("project_id"), session: item.object("params")["project_session_id"] as? String ?? "s1") } }
            }
            Button("Refine") { run { try await store.refine(item) } }
            Button("Run again") { run { try await store.replay(item) } }
            if let path = item.strings("outputs").first { Button("Reveal in Finder") { NativeFiles.reveal(path) }; Button("Save As…") { NativeFiles.save(path, store: store) } }
            Divider()
            Button("Remove from library…", role: .destructive) { confirmDelete(item, files: false) }
            Button("Delete image and metadata…", role: .destructive) { confirmDelete(item, files: true) }
        }
    }
    private func confirmDelete(_ item: NativeRecord, files: Bool) {
        let alert = NSAlert(); alert.messageText = files ? "Delete this image from disk?" : "Remove this image from the library?"
        alert.informativeText = files ? "The image and its metadata will be permanently deleted." : "The image file will be kept."
        alert.addButton(withTitle: files ? "Delete" : "Remove"); alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn { run { try await store.mutate("/api/library/\(item.id)?delete_files=\(files)", method: "DELETE") } }
    }
    private func run(_ action: @escaping () async throws -> Void) { Task { await store.perform(action) } }
}

struct NativeProjects: View {
    @ObservedObject var store: NativeStore
    @State private var newName = ""
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack { TextField("New project name", text: $newName).textFieldStyle(.roundedBorder).frame(maxWidth: 300); Button("Create project") { run { try await store.createProject(name: newName.isEmpty ? "Untitled project" : newName); newName = "" } }.buttonStyle(.borderedProminent); Spacer() }
                if store.projects.isEmpty { NativeEmpty(symbol: "folder.badge.plus", title: "A home for every idea", detail: "Projects keep your prompts, reference copies, settings, and generation sessions together.") }
                ForEach(store.projects) { project in
                    VStack(alignment: .leading, spacing: 12) {
                        HStack(spacing: 14) {
                            NativeImage(path: project.strings("references").first ?? "", fit: false).frame(width: 64, height: 64).clipped().clipShape(RoundedRectangle(cornerRadius: 8))
                            VStack(alignment: .leading, spacing: 5) { Text(project.string("name")).font(.headline); Text("\(project.records("sessions").count) sessions · \(Int(project.number("generation_count"))) results · \(project.strings("references").count) references").font(.caption).foregroundStyle(.secondary); Text(project.string("prompt")).lineLimit(1).font(.caption).foregroundStyle(.secondary) }
                            Spacer()
                            Button("Open") { run { try await store.openProject(project.id) } }
                            Menu { Button("Rename…") { rename(project) }; Button("Delete project…", role: .destructive) { delete(project) } } label: { Image(systemName: "ellipsis") }.menuStyle(.borderlessButton).frame(width: 22)
                        }
                        if !project.strings("missing_references").isEmpty { Label("\(project.strings("missing_references").count) reference copies are missing", systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange) }
                        ForEach(project.records("sessions").sorted { $0.number("updated_at") > $1.number("updated_at") }) { session in
                            NativeSessionRow(store: store, project: project, session: session)
                        }
                        Button("New session") { run { try await store.openProject(project.id); try await store.newSession() } }.controlSize(.small)
                    }.padding(16).background(panelColor, in: RoundedRectangle(cornerRadius: 12))
                }
            }.padding(24).frame(maxWidth: 960, alignment: .leading).frame(maxWidth: .infinity)
        }
    }
    private func rename(_ project: NativeRecord) {
        let alert = NSAlert(); alert.messageText = "Rename project"; let field = NSTextField(string: project.string("name")); field.frame = NSRect(x: 0, y: 0, width: 300, height: 24); alert.accessoryView = field; alert.addButton(withTitle: "Rename"); alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn { run { try await store.mutate("/api/projects/\(project.id)", method: "PUT", body: ["name": field.stringValue]) } }
    }
    private func delete(_ project: NativeRecord) {
        let alert = NSAlert(); alert.messageText = "Delete \(project.string("name"))?"; alert.informativeText = "Project reference copies and sessions will be deleted. Generated images are kept."; alert.addButton(withTitle: "Delete"); alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn { run { try await store.mutate("/api/projects/\(project.id)", method: "DELETE"); if store.draft.project_id == project.id { store.draft.project_id = nil; store.draft.project_session_id = nil } } }
    }
    private func run(_ action: @escaping () async throws -> Void) { Task { await store.perform(action) } }
}

struct NativeAvatars: View {
    @ObservedObject var store: NativeStore
    @State private var editing = false
    @State private var selected: NativeRecord?
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack { Text("Tag a saved subject in any prompt using its @handle.").foregroundStyle(.secondary); Spacer(); Button("New avatar") { selected = nil; editing = true }.buttonStyle(.borderedProminent) }
                if store.avatars.isEmpty { NativeEmpty(symbol: "person.crop.square.badge.camera", title: "Keep a familiar face", detail: "Save face and body references once, then reuse the subject in your prompts.") }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 240, maximum: 360))], spacing: 18) {
                    ForEach(store.avatars) { avatar in VStack(alignment: .leading, spacing: 12) {
                        NativeImage(path: avatar.strings("references").first ?? "", fit: false).frame(height: 160).clipped().clipShape(RoundedRectangle(cornerRadius: 9))
                        HStack { VStack(alignment: .leading) { Text(avatar.string("name")).font(.headline); Text("@\(avatar.string("handle"))").font(.caption).foregroundStyle(.tint) }; Spacer(); Button("Edit") { selected = avatar; editing = true } }
                        Text(avatar.string("description")).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                        HStack { Text("\(avatar.strings("references").count) references").font(.caption).foregroundStyle(.secondary); Spacer(); Button("Use in prompt") { store.draft.prompt += " @" + avatar.string("handle"); store.page = .compose } }
                    }.padding(14).background(panelColor, in: RoundedRectangle(cornerRadius: 12)) }
                }
            }.padding(24)
        }.sheet(isPresented: $editing) { NativeAvatarEditor(store: store, avatar: selected, close: { editing = false }) }
    }
}

struct NativeAvatarEditor: View {
    @ObservedObject var store: NativeStore
    let avatar: NativeRecord?
    let close: () -> Void
    @State private var name = ""
    @State private var handle = ""
    @State private var description = ""
    @State private var references: [String] = []
    @State private var roles: [String] = []
    @State private var saving = false
    @State private var localError: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(avatar == nil ? "New avatar" : "Edit avatar").font(.title2.bold())
            Form { TextField("Name", text: $name); TextField("@handle", text: $handle); TextField("Description", text: $description) }.textFieldStyle(.roundedBorder)
            ScrollView { VStack(spacing: 12) { ForEach(references.indices, id: \.self) { index in HStack {
                NativeImage(path: references[index], fit: false).frame(width: 66, height: 66).clipped().clipShape(RoundedRectangle(cornerRadius: 6))
                Text(URL(fileURLWithPath: references[index]).lastPathComponent).lineLimit(1)
                Picker("Role", selection: $roles[index]) { Text("Face").tag("face"); Text("Body").tag("body"); Text("Reference").tag("reference") }.frame(width: 140)
                Button { references.remove(at: index); roles.remove(at: index) } label: { Image(systemName: "trash") }.accessibilityLabel("Remove avatar reference")
            } } } }.frame(minHeight: 150, maxHeight: 280)
            Button("Add reference images…") { NativeFiles.pickImages { paths in
                for path in paths where references.count < 10 { references.append(path); roles.append(references.count == 1 ? "face" : references.count == 2 ? "body" : "reference") }
            } }.disabled(references.count >= 10)
            if let localError { Text(localError).foregroundStyle(.red).font(.caption) }
            HStack {
                if let avatar { Button("Delete avatar…", role: .destructive) { delete(avatar) } }
                Spacer(); Button("Cancel") { close() }.keyboardShortcut(.cancelAction)
                Button(saving ? "Saving…" : "Save avatar") { save() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(saving || name.isEmpty || handle.isEmpty || references.isEmpty)
            }
        }.padding(24).frame(width: 600).onAppear {
            if let avatar { name = avatar.string("name"); handle = avatar.string("handle"); description = avatar.string("description"); references = avatar.strings("references"); roles = avatar.strings("reference_roles"); if roles.count != references.count { roles = references.map { _ in "reference" } } }
        }
    }
    private func save() {
        saving = true
        Task {
            do { try await store.mutate(avatar.map { "/api/avatars/\($0.id)" } ?? "/api/avatars", method: avatar == nil ? "POST" : "PUT", body: ["name": name, "handle": handle, "description": description, "references": references, "reference_roles": roles]); close() }
            catch { localError = error.localizedDescription }
            saving = false
        }
    }
    private func delete(_ avatar: NativeRecord) {
        let alert = NSAlert(); alert.messageText = "Delete @\(avatar.string("handle"))?"; alert.informativeText = "Its saved reference copies will be removed."; alert.addButton(withTitle: "Delete"); alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn { Task { do { try await store.mutate("/api/avatars/\(avatar.id)", method: "DELETE"); close() } catch { localError = error.localizedDescription } } }
    }
}

struct NativeModels: View {
    @ObservedObject var store: NativeStore
    @State private var basePath = ""
    @State private var packPath = "" { didSet { validation = nil } }
    @State private var installName = "custom-q4"
    @State private var validation: NativeRecord?
    var body: some View {
        ScrollView { VStack(alignment: .leading, spacing: 20) {
            ForEach(store.modelTasks) { job in VStack(alignment: .leading, spacing: 8) {
                HStack { Text(phaseName(job.string("phase"))).font(.headline); Spacer(); Button("Cancel") { run { try await store.cancel(job) } } }
                Text(job.string("message")).font(.caption).textSelection(.enabled)
                if job.number("total_bytes") > 0 { ProgressView(value: job.number("downloaded_bytes"), total: job.number("total_bytes")) } else { ProgressView().controlSize(.small) }
            }.padding(16).background(panelColor, in: RoundedRectangle(cornerRadius: 10)) }
            ForEach(store.sources) { source in sourceCard(source) }
            GroupBox("Text encoders · per model family") {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Encoders are specific to their model family and are baked into prepared model directories.").font(.caption).foregroundStyle(.secondary)
                    ForEach(store.encoders, id: \.valuesKey) { encoder in HStack { VStack(alignment: .leading, spacing: 3) { Text(encoder.string("label")); Text(encoder.string("family") + " · " + encoder.string("detail")).font(.caption).foregroundStyle(.secondary) }; Spacer(); if encoder.bool("installed") { Label("Installed", systemImage: "checkmark.circle.fill").foregroundStyle(.green).font(.caption) } } }
                }.padding(10).frame(maxWidth: .infinity, alignment: .leading)
            }
            GroupBox("Install custom weights") {
                VStack(alignment: .leading, spacing: 12) {
                    HStack { TextField("Base model directory", text: $basePath).onChange(of: basePath) { _ in validation = nil }; Button("Choose…") { NativeFiles.pickDirectory { basePath = $0 } } }
                    HStack { TextField("Transformer .safetensors pack", text: $packPath); Button("File…") { NativeFiles.pickPack { packPath = $0 } }; Button("Folder…") { NativeFiles.pickDirectory { packPath = $0 } } }
                    HStack { TextField("Install name", text: $installName); Button("Validate") { run { guard let api = store.api else { return }; validation = try await api.request("/api/validate", method: "POST", body: ["path": packPath, "component": "transformer", "base_model_path": basePath]) } }.disabled(packPath.isEmpty); Button("Install and use") { run { try await store.submit("install", payload: ["path": packPath, "base_model_path": basePath, "name": installName, "quantize": 4]) } }.disabled(basePath.isEmpty || validation?.bool("ok") != true || !store.modelTasks.isEmpty) }
                    if let validation { Text(validation.bool("ok") ? "Validation passed" : "Validation failed").foregroundStyle(validation.bool("ok") ? .green : .red); ForEach(validation.strings("errors") + validation.strings("warnings"), id: \.self) { Text($0).font(.caption).textSelection(.enabled) } }
                }.textFieldStyle(.roundedBorder).padding(10)
            }
            HStack { Text("Model storage · \(ByteCountFormatter.string(fromByteCount: Int64(store.system.number("models_size_bytes")), countStyle: .file))").foregroundStyle(.secondary); Spacer(); Button("Open model folder") { NSWorkspace.shared.open(URL(fileURLWithPath: store.system.string("models_dir"))) } }
        }.padding(24).frame(maxWidth: 1000).frame(maxWidth: .infinity) }
    }
    private func sourceCard(_ source: NativeRecord) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack { Image(systemName: "cube").foregroundStyle(.tint).font(.title2); Text(source.string("label")).font(.headline); Spacer(); Label(source.bool("available") ? "Ready" : "Not installed", systemImage: source.bool("available") ? "checkmark.circle" : "arrow.down.circle").font(.caption).foregroundStyle(source.bool("available") ? .green : .secondary) }
            Text(source.string("detail")).font(.callout).foregroundStyle(.secondary)
            ForEach(source.strings("notes"), id: \.self) { Text($0).font(.caption).foregroundStyle(.secondary) }
            HStack {
                Button(store.draft.model_source == source.id ? "Selected" : "Use this model") { run { try await store.selectModel(source) } }.disabled(store.draft.model_source == source.id).buttonStyle(.borderedProminent)
                if source.string("install_job") == "prepare-klein" {
                    Button("Download and stage") { run { try await store.submit("prepare-klein", payload: ["encoder": "ablated"]) } }.disabled(source.bool("available") || !store.modelTasks.isEmpty)
                } else if !source.string("repo_id").isEmpty {
                    Button("Download") { run { try await store.submit("download", payload: ["source_id": source.id]) } }.disabled(source.bool("available") || !store.modelTasks.isEmpty)
                    Button("Prepare local copy") { run { try await store.submit("prepare", payload: ["source_id": source.id]) } }.disabled(!store.modelTasks.isEmpty)
                }
                Spacer()
                if !source.string("location").isEmpty { Button("Reveal") { NativeFiles.reveal(source.string("location")) }; Button("Delete…", role: .destructive) { deleteModel(source.string("location")) } }
            }
            ForEach(source.records("installed"), id: \.pathKey) { model in HStack { Text(model.string("name")).font(.caption); Text(ByteCountFormatter.string(fromByteCount: Int64(model.number("size_bytes")), countStyle: .file)).font(.caption).foregroundStyle(.secondary); Spacer(); Button("Use") { run { try await store.selectModel(source, path: model.string("path")) } }; Button("Delete…", role: .destructive) { deleteModel(model.string("path")) } }.controlSize(.small) }
        }.padding(18).background(panelColor, in: RoundedRectangle(cornerRadius: 12))
    }
    private func deleteModel(_ path: String) {
        let alert = NSAlert(); alert.messageText = "Delete model files?"; alert.informativeText = "\(path)\nYou will need to download or prepare this model again."; alert.addButton(withTitle: "Delete"); alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn { run { try await store.mutate("/api/models/delete", body: ["path": path, "confirm": true]) } }
    }
    private func run(_ action: @escaping () async throws -> Void) { Task { await store.perform(action) } }
}

struct NativeSettings: View {
    @ObservedObject var store: NativeStore
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack { Text("Settings").font(.title2.bold()); Spacer(); Button("Done") { store.settingsOpen = false }.keyboardShortcut(.defaultAction) }
            Form {
                Picker("Appearance", selection: $store.appearance) { Text("System").tag("system"); Text("Light").tag("light"); Text("Dark").tag("dark") }
                Picker("Live previews", selection: $store.draft.preview_interval) { Text("Every step").tag(1); Text("Every 2 steps").tag(2); Text("Every 5 steps").tag(5); Text("Off").tag(0); if ![0, 1, 2, 5].contains(store.draft.preview_interval) { Text("Every \(store.draft.preview_interval) steps").tag(store.draft.preview_interval) } }
                Toggle("Save metadata alongside images", isOn: $store.draft.save_metadata)
                Toggle("Tile the VAE decode", isOn: $store.draft.vae_tiling)
                HStack { Text("Output folder"); Spacer(); Text(store.draft.output_dir ?? "Default").lineLimit(1).truncationMode(.middle); Button("Choose…") { NativeFiles.pickDirectory { store.draft.output_dir = $0 } }; Button("Default") { store.draft.output_dir = nil } }
            }
            Divider()
            Text("Local runtime & storage").font(.headline)
            Text("Runtime: \(HostPaths.python() ?? "Not installed")\nData: \(store.system.string("data_root"))\nMLX: \(store.health.string("mlx_version")) · mflux: \(store.health.string("mflux_version"))").font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            HStack { Button("Open data folder") { NSWorkspace.shared.open(HostPaths.dataRoot()) }; Button("Open logs") { NSWorkspace.shared.open(HostPaths.logsDirectory()) }; Spacer(); Button("Keyboard shortcuts") { store.settingsOpen = false; DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { store.shortcutsOpen = true } } }
            Text("Inference stays on your Mac. Models download from their upstream publishers; no cloud generation service is used.").font(.caption).foregroundStyle(.secondary)
        }.padding(26).frame(width: 620)
    }
}

struct NativeEmpty: View {
    let symbol: String
    let title: String
    let detail: String
    var body: some View {
        VStack(spacing: 14) { Image(systemName: symbol).font(.system(size: 38, weight: .light)).foregroundStyle(.tint).opacity(0.7); Text(title).font(.title3.weight(.semibold)); Text(detail).font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 330) }
            .padding(32).frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private func optionalString(_ binding: Binding<String?>) -> Binding<String> { Binding(get: { binding.wrappedValue ?? "" }, set: { binding.wrappedValue = $0.isEmpty ? nil : $0 }) }
extension Array {
    func safe(_ index: Int) -> Element? { indices.contains(index) ? self[index] : nil }
}
private extension NativeRecord {
    var valuesKey: String { string("key") }
    var pathKey: String { string("path") }
}
