import AppKit

/// The native menu bar.
///
/// The items and their shortcuts are the ones the Rust shell defined: the design rule is that
/// every primary action has a keyboard shortcut, and the menu is where those become discoverable
/// rather than folklore. The standard Edit items do more than look right — they travel the
/// responder chain into the web view, which is what makes ⌘C and ⌘V work inside the prompt field.
///
/// Page commands (`new`, `generate`, `compose`, …) are forwarded to the frontend by id, because
/// the page is what knows whether a job is running or which view is showing. Window commands
/// (`reload`) stay in Swift, because the page has no idea what a window is.
enum MainMenu {
    static func build(target: AppDelegate) -> NSMenu {
        let bar = NSMenu()
        bar.addItem(holder(for: appMenu(target)))
        bar.addItem(holder(for: fileMenu(target)))
        bar.addItem(holder(for: editMenu()))
        bar.addItem(holder(for: viewMenu(target)))
        bar.addItem(holder(for: windowMenu()))
        return bar
    }

    // MARK: - menus

    private static func appMenu(_ target: AppDelegate) -> NSMenu {
        NSMenu("Refract Image", [
            standard("About Refract Image", #selector(NSApplication.orderFrontStandardAboutPanel(_:))),
            .separator(),
            page("Settings…", "settings", ",", target),
            .separator(),
            servicesItem(),
            .separator(),
            standard("Hide Refract Image", #selector(NSApplication.hide(_:)), key: "h"),
            standard("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), key: "h", modifiers: [.command, .option]),
            standard("Show All", #selector(NSApplication.unhideAllApplications(_:))),
            .separator(),
            standard("Quit Refract Image", #selector(NSApplication.terminate(_:)), key: "q"),
        ])
    }

    private static func fileMenu(_ target: AppDelegate) -> NSMenu {
        NSMenu("File", [
            page("New Generation", "new", "n", target),
            page("New Project", "new-project", "p", target),
            page("Next Project", "next-project", "j", target),
            .separator(),
            page("Generate", "generate", "\r", target),
            page("Cancel Job", "cancel", ".", target),
            .separator(),
            page("Save Result As…", "save-as", "S", target, modifiers: [.command, .shift]),
            page("Reveal Result in Finder", "reveal", "R", target, modifiers: [.command, .shift]),
            .separator(),
            log("Open Service Log…", "backend", target),
            log("Open UI Server Log…", "ui", target),
            native("Open Log Folder…", #selector(AppDelegate.openLogsFolder(_:)), target),
        ])
    }

    private static func editMenu() -> NSMenu {
        // `undo:`/`redo:` have no AppKit-declared selector to name; the responder chain is what
        // resolves them, and WebKit implements both for editable content.
        NSMenu("Edit", [
            standard("Undo", Selector(("undo:")), key: "z"),
            standard("Redo", Selector(("redo:")), key: "z", modifiers: [.command, .shift]),
            .separator(),
            standard("Cut", #selector(NSText.cut(_:)), key: "x"),
            standard("Copy", #selector(NSText.copy(_:)), key: "c"),
            standard("Paste", #selector(NSText.paste(_:)), key: "v"),
            standard("Select All", #selector(NSResponder.selectAll(_:)), key: "a"),
        ])
    }

    private static func viewMenu(_ target: AppDelegate) -> NSMenu {
        NSMenu("View", [
            page("Compose", "compose", "1", target),
            page("Library", "library", "2", target),
            page("Projects", "projects", "3", target),
            page("Models", "models", "4", target),
            .separator(),
            page("Keyboard Shortcuts", "shortcuts", "/", target),
            .separator(),
            native("Reload Window", #selector(AppDelegate.reloadWindow(_:)), target, key: "r"),
        ])
    }

    private static func windowMenu() -> NSMenu {
        let window = NSMenu("Window", [
            standard("Minimize", #selector(NSWindow.performMiniaturize(_:)), key: "m"),
            standard("Zoom", #selector(NSWindow.performZoom(_:))),
            .separator(),
            standard("Close", #selector(NSWindow.performClose(_:)), key: "w"),
        ])
        NSApp.windowsMenu = window
        return window
    }

    // MARK: - items

    /// A page command. The target is explicit rather than nil so the message reaches the app
    /// delegate even while the web view is first responder and would otherwise swallow it.
    private static func page(
        _ title: String,
        _ id: String,
        _ key: String,
        _ target: AppDelegate,
        modifiers: NSEvent.ModifierFlags = .command
    ) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: #selector(AppDelegate.runMenuCommand(_:)), keyEquivalent: key)
        item.keyEquivalentModifierMask = modifiers
        item.target = target
        item.representedObject = id
        return item
    }

    /// A command the app answers itself.
    private static func native(
        _ title: String,
        _ action: Selector,
        _ target: AppDelegate,
        key: String = "",
        modifiers: NSEvent.ModifierFlags = .command
    ) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.keyEquivalentModifierMask = modifiers
        item.target = target
        return item
    }

    private static func log(_ title: String, _ which: String, _ target: AppDelegate) -> NSMenuItem {
        let item = native(title, #selector(AppDelegate.openLog(_:)), target)
        item.representedObject = which
        return item
    }

    /// A standard AppKit action, dispatched by the responder chain to the application, the window
    /// or the web view depending on which of them can answer it.
    private static func standard(
        _ title: String,
        _ action: Selector?,
        key: String = "",
        modifiers: NSEvent.ModifierFlags = .command
    ) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.keyEquivalentModifierMask = modifiers
        return item
    }

    private static func servicesItem() -> NSMenuItem {
        let item = NSMenuItem(title: "Services", action: nil, keyEquivalent: "")
        let services = NSMenu(title: "Services")
        item.submenu = services
        NSApp.servicesMenu = services
        return item
    }

    private static func holder(for submenu: NSMenu) -> NSMenuItem {
        let holder = NSMenuItem(title: submenu.title, action: nil, keyEquivalent: "")
        holder.submenu = submenu
        return holder
    }
}

private extension NSMenu {
    convenience init(_ title: String, _ items: [NSMenuItem]) {
        self.init(title: title)
        for item in items {
            addItem(item)
        }
    }
}
