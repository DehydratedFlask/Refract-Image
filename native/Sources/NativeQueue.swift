import SwiftUI

struct NativeQueuePanel: View {
    @ObservedObject var store: NativeStore
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Task queue").font(.title2.weight(.semibold))
                    Text("Generations run one at a time to keep memory use safe. Waiting tasks start automatically.").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Done") { store.queueOpen = false }.keyboardShortcut(.cancelAction).accessibilityIdentifier("close-queue")
            }.padding(20)
            Divider()
            TimelineView(.periodic(from: .now, by: 1)) { context in
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        HStack {
                            Text("\(store.queueTasks.filter { $0.string("status") == "running" }.count) running · \(store.queueTasks.filter { $0.string("status") == "queued" }.count) waiting").font(.headline)
                            Spacer()
                        }
                        if store.queueTasks.isEmpty {
                            Label("All caught up — no tasks waiting", systemImage: "checkmark.circle").foregroundStyle(.secondary).padding(.vertical, 12)
                        }
                        ForEach(store.queueTasks) { job in
                            NativeQueueRow(store: store, job: job, now: context.date)
                                .padding(14).background(Color.secondary.opacity(0.07), in: RoundedRectangle(cornerRadius: 10))
                        }
                        if !store.recentTasks.isEmpty {
                            Divider().padding(.vertical, 6)
                            Text("Recent tasks").font(.headline)
                            ForEach(store.recentTasks) { job in
                                NativeQueueRow(store: store, job: job, now: context.date)
                                    .padding(14).background(Color.secondary.opacity(0.04), in: RoundedRectangle(cornerRadius: 10))
                            }
                        }
                    }.padding(20)
                }
            }
        }.frame(width: 720, height: 600)
    }
}

struct NativeQueueRow: View {
    @ObservedObject var store: NativeStore
    let job: NativeRecord
    let now: Date
    var compact = false
    private var waitingPosition: Int {
        (store.queueTasks.filter { $0.string("status") == "queued" }.firstIndex { $0.id == job.id } ?? 0) + 1
    }
    private var title: String {
        if !job.prompt.isEmpty { return job.prompt }
        return job.string("kind").replacingOccurrences(of: "_", with: " ").capitalized + " · " + (job.object("payload")["model_source"] as? String ?? "Model task")
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                if !compact, let path = job.strings("outputs").first ?? (job.string("preview_path").isEmpty ? nil : job.string("preview_path")) {
                    NativeImage(path: path, fit: false).frame(width: 56, height: 56).clipped().clipShape(RoundedRectangle(cornerRadius: 6))
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(compact ? .caption : .callout.weight(.medium)).lineLimit(2)
                    Text(status).font(.caption).foregroundStyle(job.string("status") == "failed" ? .red : .secondary)
                        .accessibilityIdentifier("queue-status-\(job.id)")
                }
                Spacer(minLength: 6)
                Button(compact ? "View" : "Inspect") { Task { await store.perform { try await store.inspectJob(job) } } }
                    .controlSize(.small).accessibilityIdentifier("queue-inspect-\(job.id)")
                if job.pending {
                    Button(job.string("status") == "queued" ? "Remove" : "Cancel", role: .destructive) { Task { await store.perform { try await store.cancel(job) } } }
                        .controlSize(.small).accessibilityIdentifier("queue-cancel-\(job.id)")
                }
            }
            if job.string("status") == "running" {
                if job.number("total_steps") > 0 {
                    ProgressView(value: job.number("step"), total: max(1, job.number("total_steps")))
                        .accessibilityLabel("Task progress").accessibilityIdentifier("queue-progress-\(job.id)")
                    HStack {
                        Text("Step \(Int(job.number("step"))) / \(Int(job.number("total_steps")))")
                        Spacer()
                        Text("\(duration(store.elapsed(job, now: now))) elapsed · \(store.remaining(job, now: now))")
                    }.font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                } else {
                    ProgressView().controlSize(.small)
                    Text("\(duration(store.elapsed(job, now: now))) elapsed · \(job.string("message"))").font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            } else if job.string("status") == "queued" {
                Text("Waiting #\(waitingPosition) · \(duration(max(0, now.timeIntervalSince1970 - job.number("created_at")))) in queue")
                    .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            } else if !compact {
                Text(job.string("status") == "failed" ? job.string("error", job.string("message")) : "\(duration(store.elapsed(job, now: now))) · \(job.strings("outputs").count) saved images")
                    .font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
        }.accessibilityElement(children: .contain).accessibilityIdentifier("queue-task-\(job.id)")
    }
    private var status: String {
        job.string("status") == "running" ? "Running · " + phaseName(job.string("phase")) : phaseName(job.string("status"))
    }
}
