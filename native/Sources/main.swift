import AppKit

/// Command-line switches, so the same binary is usable from a terminal.
///
/// A double-clicked app gets these from its bundle; a terminal run needs `--mock` to exercise the
/// UI without loading 11 GB of weights, and `--smoke` to prove the window, the bridge and the
/// service all talk to each other without anyone clicking anything.
enum Launch {
    private(set) static var mock = false
    private(set) static var verbose = false
    private(set) static var smoke = false

    /// Load the page from a development server instead of the built bundle in `app/dist`.
    ///
    /// This is what makes the app the development shell: the window reloads as vite recompiles,
    /// and the service and the token handover are still the app's own, so the thing under test is
    /// the app rather than a browser tab.
    private(set) static var devURL: URL?

    static func parse(_ arguments: [String]) {
        var index = 0
        while index < arguments.count {
            defer { index += 1 }
            let argument = arguments[index]
            switch argument {
            case "--mock":
                mock = true
            case "--verbose", "-v":
                verbose = true
            case "--smoke":
                smoke = true
                verbose = true
            case "--dev-url", "--dev-url=":
                if argument.hasSuffix("=") {
                    devURL = URL(string: String(argument.dropLast()))
                } else if index + 1 < arguments.count {
                    devURL = URL(string: arguments[index + 1])
                    index += 1
                }
            default:
                break
            }
        }
    }

    static func usage() -> String {
        """
        Refract Image — local reference-guided image editing on Apple silicon

          --mock          run the placeholder runner instead of loading the model
          --verbose       forward the page's console to stderr, and allow the web inspector
          --smoke         start everything, check window + bridge + service, then exit
          --dev-url URL   show a development server's page instead of the built UI
        """
    }
}

/// One instance, always.
///
/// A second copy would start a second service, and two services each wanting 20 GB of unified
/// memory is how a machine gets restarted. Re-opening the app therefore wakes the copy that is
/// already running, which is also what people expect of a Dock icon.
func alreadyRunningCopy() -> NSRunningApplication? {
    guard let identifier = Bundle.main.bundleIdentifier, !Launch.smoke else { return nil }
    let mine = ProcessInfo.processInfo.processIdentifier
    return NSRunningApplication.runningApplications(withBundleIdentifier: identifier)
        .first { $0.processIdentifier != mine }
}

enum Smoke {
    static func report(_ problems: [String]) {
        if problems.isEmpty {
            FileHandle.standardError.write(Data("SMOKE_OK\n".utf8))
        }
        for problem in problems {
            FileHandle.standardError.write(Data("SMOKE_FAIL \(problem)\n".utf8))
        }
    }
}

// MARK: - application

let application = NSApplication.shared
Launch.parse(CommandLine.arguments)

if CommandLine.arguments.contains("--help") || CommandLine.arguments.contains("-h") {
    print(Launch.usage())
    exit(0)
}

let delegate = AppDelegate()
application.delegate = delegate
application.run()
