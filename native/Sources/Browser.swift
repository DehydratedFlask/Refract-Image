import AppKit
import WebKit

/// A web view that takes files dropped from Finder as *paths*.
///
/// That is the difference between this and the browser: a browser cannot learn where a dropped
/// file lives, so the only way to use one is to upload a copy into the service. Here the path
/// goes straight into the job, which is also what makes a 40 MB TIFF usable as a reference.
///
/// `registerForDraggedTypes` is overridden rather than called once because WebKit registers its
/// own destination types as pages load; without the override a dropped file can still be handled
/// by WebKit, which means the window navigates away to `file:///…` and the app is gone.
final class DropWebView: WKWebView {
    var onPathsDropped: (([String]) -> Void)?
    var onDragStateChanged: ((Bool) -> Void)?

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        registerForDraggedTypes([.fileURL])
    }

    override func registerForDraggedTypes(_ newTypes: [NSPasteboard.PasteboardType]) {
        super.registerForDraggedTypes(Array(Set(newTypes).union([.fileURL])))
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        noteDrag(Self.accepts(sender))
        return Self.accepts(sender) ? .copy : []
    }

    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        Self.accepts(sender) ? .copy : []
    }

    override func draggingExited(_ sender: NSDraggingInfo?) {
        noteDrag(false)
    }

    override func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool {
        Self.accepts(sender)
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        let paths = Self.filePaths(from: sender)
        noteDrag(false)
        guard !paths.isEmpty else { return false }
        onPathsDropped?(paths)
        return true
    }

    private func noteDrag(_ active: Bool) {
        onDragStateChanged?(active)
    }

    private static func accepts(_ sender: NSDraggingInfo) -> Bool {
        !filePaths(from: sender).isEmpty
    }

    /// Every real file path in the drop, in drop order, with directories left out — the
    /// reference strip wants images, and the service would reject a folder.
    static func filePaths(from sender: NSDraggingInfo) -> [String] {
        let options: [NSPasteboard.ReadingOptionKey: Any] = [.urlReadingFileURLsOnly: true]
        guard let urls = sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: options) as? [URL] else {
            return []
        }
        return urls.filter { !$0.hasDirectoryPath }.map(\.path)
    }
}

/// The window, and the rules for what it is allowed to show.
///
/// Two jobs beyond holding the web view. The first is chrome: the UI draws its own top bar with
/// room for the traffic lights, so the title bar is transparent and the content runs underneath
/// it, the way a real macOS app does. The second is a hard
/// boundary: anything that is not this app's own loopback origin is refused, and http(s) links
/// among them are handed to the browser. A wrapper that lets the window wander off is not an app.
final class Browser: NSObject, WKNavigationDelegate, NSWindowDelegate {
    let window: NSWindow
    let webView: DropWebView
    private let bridge: Bridge
    private weak var service: Service?
    private var origin: URLComponents?

    /// Called once the page has finished loading, and once if it could not load at all.
    var onLoaded: (() -> Void)?
    var onLoadFailed: ((String) -> Void)?

    init(bridge: Bridge, service: Service) {
        self.bridge = bridge
        self.service = service

        let configuration = WKWebViewConfiguration()
        bridge.configure(configuration)
        configuration.mediaTypesRequiringUserActionForPlayback = []

        webView = DropWebView(frame: NSRect(x: 0, y: 0, width: 1320, height: 860), configuration: configuration)

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1320, height: 860),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )

        super.init()

        window.title = "Refract Image"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.titlebarSeparatorStyle = .none
        window.minSize = NSSize(width: 1080, height: 680)
        window.isReleasedWhenClosed = false
        window.tabbingMode = .disallowed
        window.animationBehavior = .documentWindow
        window.setFrameAutosaveName("RefractImageMainWindow")
        window.delegate = self

        webView.autoresizingMask = [.width, .height]
        webView.allowsBackForwardNavigationGestures = false
        webView.navigationDelegate = self
        // The UI paints its own light or dark palette, so the colour behind it is the window's
        // rather than WebKit's default white — which otherwise flashes once on a dark theme.
        webView.underPageBackgroundColor = .windowBackgroundColor
        if #available(macOS 13.3, *) {
            webView.isInspectable = Launch.verbose
        }
        webView.onPathsDropped = { [weak bridge] paths in
            bridge?.noteDrop(paths: paths)
        }
        webView.onDragStateChanged = { [weak bridge] active in
            bridge?.noteDrag(active)
        }
        bridge.attach(to: webView)

        window.contentView = webView
        window.center()
    }

    // MARK: - presentation

    func show(url: URL) {
        origin = URLComponents(url: url, resolvingAgainstBaseURL: false)
        load(url)
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    func show() {
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    func reload() {
        webView.reload()
    }

    private func load(_ url: URL) {
        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        webView.load(request)
    }

    /// Start a window drag from inside the page.
    ///
    /// The UI marks its top bar `data-window-drag`; this is the other half of that marker.
    /// It has to use `NSApp.currentEvent`, because by the time the bridge call arrives the page's
    /// own mouse-down is over, and `performDrag` needs the event that is still being tracked.
    func beginDragFromPage() {
        guard let event = NSApp.currentEvent else { return }
        window.performDrag(with: event)
    }

    private func isAppOrigin(_ url: URL) -> Bool {
        guard let origin, url.scheme == origin.scheme, url.host == origin.host, url.port == origin.port else {
            return false
        }
        return true
    }

    // MARK: - WKNavigationDelegate

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        // `target="_blank"` and friends: the browser is the right place for a web page.
        if navigationAction.targetFrame?.isMainFrame != true {
            decisionHandler(.cancel)
            if url.scheme == "http" || url.scheme == "https" {
                NSWorkspace.shared.open(url)
            }
            return
        }
        if isAppOrigin(url) {
            decisionHandler(.allow)
            return
        }
        if url.scheme == "http" || url.scheme == "https" {
            NSWorkspace.shared.open(url)
        } else {
            NSLog("[refract] refused navigation to %@", url.absoluteString)
        }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        onLoaded?()
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError: Error) {
        onLoadFailed?(withError.localizedDescription)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation: WKNavigation!, withError: Error) {
        onLoadFailed?(withError.localizedDescription)
    }

    // MARK: - NSWindowDelegate

    /// Closing the window is quitting the app: an idle window holding a loaded 11 GB checkpoint
    /// would be the worst possible outcome of pressing the red button.
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        NSApp.terminate(nil)
        return false
    }

    func windowDidResignKey(_ notification: Notification) {
        // A drag highlight left behind by a cancelled drag would otherwise stay lit until the
        // next mouse event inside the page.
        bridge.noteDrag(false)
    }
}
