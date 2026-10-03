import AppKit

/// On first launch from anywhere but Applications (Downloads, the disk image,
/// a quarantine-translocated copy), offers to move NotchNest there and
/// relaunch. Open-at-login and stable permissions both expect it to stay put.
enum AppMover {
    private static let declinedKey = "moveToApplicationsDeclined"

    /// True while a relaunch from Applications is under way — the caller
    /// should then skip the rest of its startup.
    static func offerMoveIfNeeded() -> Bool {
        let source = Bundle.main.bundleURL.resolvingSymlinksInPath()
        guard !isInApplications(source),
              !isDevelopmentBuild(source),
              !UserDefaults.standard.bool(forKey: declinedKey)
        else { return false }

        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Move NotchNest to Applications?"
        alert.informativeText = "NotchNest works best from the Applications folder — it can open at login and keeps its permissions there."
        alert.icon = NSApp.applicationIconImage
        alert.addButton(withTitle: "Move to Applications")
        alert.addButton(withTitle: "Not Now")
        guard alert.runModal() == .alertFirstButtonReturn else {
            UserDefaults.standard.set(true, forKey: declinedKey)
            return false
        }

        do {
            relaunch(from: try moveToApplications(source))
            return true
        } catch {
            NSAlert(error: error).runModal()
            return false
        }
    }

    static var isInApplications: Bool {
        isInApplications(Bundle.main.bundleURL.resolvingSymlinksInPath())
    }

    private static func isInApplications(_ url: URL) -> Bool {
        let userApps = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Applications").path
        return url.path.hasPrefix("/Applications/") || url.path.hasPrefix(userApps + "/")
    }

    /// Built from the repo with ./build.sh — leave it where it is.
    private static func isDevelopmentBuild(_ url: URL) -> Bool {
        FileManager.default.fileExists(atPath:
            url.deletingLastPathComponent().appendingPathComponent("Package.swift").path)
    }

    private static func moveToApplications(_ source: URL) throws -> URL {
        let fm = FileManager.default
        var folder = URL(fileURLWithPath: "/Applications")
        if !fm.isWritableFile(atPath: folder.path) {
            folder = fm.homeDirectoryForCurrentUser.appendingPathComponent("Applications")
            try fm.createDirectory(at: folder, withIntermediateDirectories: true)
        }
        let destination = folder.appendingPathComponent(source.lastPathComponent)
        if fm.fileExists(atPath: destination.path) {
            try fm.trashItem(at: destination, resultingItemURL: nil)   // older copy
        }
        try fm.copyItem(at: source, to: destination)

        // The user approved this copy when they opened it; the moved one
        // shouldn't make Gatekeeper ask all over again.
        let xattr = Process()
        xattr.executableURL = URL(fileURLWithPath: "/usr/bin/xattr")
        xattr.arguments = ["-dr", "com.apple.quarantine", destination.path]
        try? xattr.run()
        xattr.waitUntilExit()

        // Tidy the original unless it sits on a read-only disk image or mount.
        let readOnly = source.path.hasPrefix("/Volumes/") || source.path.contains("/AppTranslocation/")
        if !readOnly, fm.isDeletableFile(atPath: source.path) {
            try? fm.trashItem(at: source, resultingItemURL: nil)
        }
        return destination
    }

    private static func relaunch(from destination: URL) {
        let config = NSWorkspace.OpenConfiguration()
        config.createsNewApplicationInstance = true
        NSWorkspace.shared.openApplication(at: destination, configuration: config) { _, error in
            DispatchQueue.main.async {
                if let error {
                    NSLog("NotchNest: relaunch from Applications failed: \(error)")
                    NSWorkspace.shared.activateFileViewerSelecting([destination])
                }
                NSApp.terminate(nil)
            }
        }
    }
}
