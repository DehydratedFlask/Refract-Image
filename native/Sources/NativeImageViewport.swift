import AppKit
import SwiftUI

struct NativeZoomControls: View {
    @Binding var zoom: Double
    @Binding var pan: CGPoint
    var body: some View {
        HStack(spacing: 6) {
            Button { zoom = max(0.1, zoom / 1.25) } label: { Image(systemName: "minus.magnifyingglass") }
                .help("Zoom out").accessibilityLabel("Zoom out").accessibilityIdentifier("image-zoom-out")
            Text("\(Int((zoom * 100).rounded()))%").font(.caption.monospacedDigit()).frame(width: 46)
                .help("Scale relative to Fit")
            Button { zoom = min(16, zoom * 1.25) } label: { Image(systemName: "plus.magnifyingglass") }
                .help("Zoom in").accessibilityLabel("Zoom in").accessibilityIdentifier("image-zoom-in")
            Button("Fit") { zoom = 1; pan = .zero }
                .help("Fit image and reset position").accessibilityIdentifier("image-fit")
        }.controlSize(.small)
    }
}

/// AppKit receives actual trackpad magnify/scroll events rather than fighting SwiftUI's
/// drag/export gestures. Both comparison images use the same transform.
struct NativeImageViewport: NSViewRepresentable {
    let path: String
    var referencePath: String? = nil
    var wipe: Double = 0.5
    @Binding var zoom: Double
    @Binding var pan: CGPoint

    func makeNSView(context: Context) -> NativeViewportView {
        let view = NativeViewportView()
        view.setAccessibilityIdentifier("image-viewport")
        view.setAccessibilityLabel("Image viewer. Pinch to zoom, scroll or drag to pan, double-click to fit.")
        return view
    }
    func updateNSView(_ view: NativeViewportView, context: Context) {
        view.onTransform = { scale, offset in zoom = scale; pan = offset }
        view.setImages(path: path, reference: referencePath)
        view.wipe = wipe
        view.zoom = zoom; view.pan = pan
        view.needsDisplay = true
    }
}

final class NativeViewportView: NSView {
    var image: NSImage?
    var referenceImage: NSImage?
    private var path = ""
    private var referencePath: String?
    private var loadID = UUID()
    var zoom = 1.0
    var pan = CGPoint.zero
    var wipe = 0.5
    var onTransform: ((Double, CGPoint) -> Void)?
    override var acceptsFirstResponder: Bool { true }

    func setImages(path: String, reference: String?) {
        guard self.path != path || referencePath != reference else { return }
        self.path = path; referencePath = reference
        let id = UUID(); loadID = id
        image = nil; referenceImage = nil; needsDisplay = true
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = try? Data(contentsOf: URL(fileURLWithPath: path))
            let refData = reference.flatMap { try? Data(contentsOf: URL(fileURLWithPath: $0)) }
            DispatchQueue.main.async {
                guard let self, self.loadID == id else { return }
                self.image = data.flatMap { NSImage(data: $0) }
                self.referenceImage = refData.flatMap { NSImage(data: $0) }
                self.needsDisplay = true
            }
        }
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.textBackgroundColor.setFill(); bounds.fill()
        guard let image, image.size.width > 0, image.size.height > 0 else { return }
        let fit = min(max(1, bounds.width - 24) / image.size.width, max(1, bounds.height - 24) / image.size.height)
        let size = NSSize(width: image.size.width * fit * zoom, height: image.size.height * fit * zoom)
        let rect = NSRect(x: bounds.midX - size.width / 2 + pan.x, y: bounds.midY - size.height / 2 + pan.y, width: size.width, height: size.height)
        NSGraphicsContext.current?.imageInterpolation = .high
        image.draw(in: rect, from: .zero, operation: .sourceOver, fraction: 1)
        if let referenceImage {
            NSGraphicsContext.saveGraphicsState()
            NSBezierPath(rect: NSRect(x: 0, y: 0, width: bounds.width * wipe, height: bounds.height)).addClip()
            referenceImage.draw(in: rect, from: .zero, operation: .sourceOver, fraction: 1)
            NSGraphicsContext.restoreGraphicsState()
            NSColor.white.setFill()
            NSRect(x: bounds.width * wipe - 1, y: 0, width: 2, height: bounds.height).fill()
        }
    }

    func changeZoom(_ scale: Double, anchor: CGPoint) {
        let next = min(16, max(0.1, scale))
        let ratio = next / zoom
        pan = CGPoint(x: anchor.x - bounds.midX - (anchor.x - bounds.midX - pan.x) * ratio,
                      y: anchor.y - bounds.midY - (anchor.y - bounds.midY - pan.y) * ratio)
        zoom = next
        publish()
    }
    func fitImage() { zoom = 1; pan = .zero; publish() }
    private func publish() { needsDisplay = true; onTransform?(zoom, pan) }
    override func magnify(with event: NSEvent) {
        changeZoom(zoom * max(0.01, 1 + event.magnification), anchor: convert(event.locationInWindow, from: nil))
    }
    override func scrollWheel(with event: NSEvent) {
        if event.modifierFlags.contains(.control) || event.modifierFlags.contains(.command) || !event.hasPreciseScrollingDeltas {
            changeZoom(zoom * exp(event.scrollingDeltaY * 0.025), anchor: convert(event.locationInWindow, from: nil))
        } else {
            pan.x += event.scrollingDeltaX; pan.y -= event.scrollingDeltaY
            publish()
        }
    }
    override func mouseDown(with event: NSEvent) {
        window?.makeFirstResponder(self)
        if event.clickCount == 2 { fitImage() }
    }
    override func mouseDragged(with event: NSEvent) { pan.x += event.deltaX; pan.y -= event.deltaY; publish() }
    override func resetCursorRects() { addCursorRect(bounds, cursor: .openHand) }
    override func keyDown(with event: NSEvent) {
        switch event.charactersIgnoringModifiers {
        case "+", "=": changeZoom(zoom * 1.25, anchor: CGPoint(x: bounds.midX, y: bounds.midY))
        case "-": changeZoom(zoom / 1.25, anchor: CGPoint(x: bounds.midX, y: bounds.midY))
        case "0": fitImage()
        default: super.keyDown(with: event)
        }
    }
}

struct NativeImageViewer: View {
    @ObservedObject var store: NativeStore
    let item: NativeRecord
    @State private var output = 0
    @State private var zoom = 1.0
    @State private var pan = CGPoint.zero
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Text("Image preview").font(.headline)
                if item.strings("outputs").count > 1 {
                    Picker("Image", selection: $output) {
                        ForEach(item.strings("outputs").indices, id: \.self) { Text("Image \($0 + 1)").tag($0) }
                    }.frame(width: 130)
                }
                Spacer()
                NativeZoomControls(zoom: $zoom, pan: $pan)
                Button { store.viewingImage = nil } label: { Image(systemName: "xmark") }
                    .accessibilityLabel("Close image preview").accessibilityIdentifier("close-image-viewer").keyboardShortcut(.cancelAction)
            }.padding(16)
            Divider()
            NativeImageViewport(path: path, zoom: $zoom, pan: $pan)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            Divider()
            VStack(alignment: .leading, spacing: 10) {
                Text(item.prompt).font(.callout).lineLimit(3).textSelection(.enabled)
                HStack {
                    Text("Pinch to zoom · Two-finger scroll or drag to pan · Double-click to fit").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("Copy") { NativeFiles.copy(path, store: store) }
                    Button("Save As…") { NativeFiles.save(path, store: store) }
                    Button("Reveal") { NativeFiles.reveal(path) }
                }
                HStack {
                    if !item.string("project_id").isEmpty {
                        Button("Open saved session") {
                            Task { await store.perform {
                                try await store.openProject(item.string("project_id"), session: item.object("params")["project_session_id"] as? String ?? "s1")
                                store.viewingImage = nil
                            } }
                        }
                    }
                    Spacer()
                    Button("Refine as new session") { Task { await store.perform { try await store.refine(item) } } }
                }.controlSize(.small)
            }.padding(16)
        }.background(Color(nsColor: .windowBackgroundColor))
        .preferredColorScheme(store.appearance == "system" ? nil : store.appearance == "dark" ? .dark : .light)
        .frame(minWidth: 700, idealWidth: 1000, maxWidth: .infinity, minHeight: 500, idealHeight: 720, maxHeight: .infinity)
        .onChange(of: output) { _ in zoom = 1; pan = .zero }
    }
    private var path: String { item.strings("outputs").safe(output) ?? item.strings("outputs").first ?? "" }
}
