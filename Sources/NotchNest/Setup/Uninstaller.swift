import AppKit
import ServiceManagement

/// Removes NotchNest and everything it put on this Mac (Settings → Uninstall
/// NotchNest…): the dictation engine and its Python, caches, ~/.murmur, logs,
/// preferences, Keychain items, the login item and, if asked, the models.
///
/// Launch with `open --env NOTCHNEST_UNINSTALL_DRY_RUN=1 NotchNest.app` to
/// walk through it while only logging what would go.
enum Uninstaller {
    /// The models the setup assistant downloads (murmur/models.py pins the
    /// same repos). Ones the user picked by hand are left alone.
    private static let installedModels = [
        "mlx-community/whisper-large-v3-turbo-q4",
        "mlx-community/Qwen2.5-3B-Instruct-4bit",
        "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
    ]

    struct Plan {
        var files: [URL]
        var models: [URL]
        var filesBytes: Int64
        var modelsBytes: Int64
    }

    static var isDryRun: Bool {
        ProcessInfo.processInfo.environment["NOTCHNEST_UNINSTALL_DRY_RUN"] == "1"
    }

    /// What's there to remove, with sizes. Walks a few GB, so call it off the main thread.
    static func plan() -> Plan {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let library = home.appendingPathComponent("Library")
        let bundleID = Bundle.main.bundleIdentifier ?? Keychain.service
        let files = [
            EngineRuntime.support,          // engine, its Python, File Tray saves
            EngineRuntime.caches,           // uv download + bytecode caches
            home.appendingPathComponent(".murmur"),
            library.appendingPathComponent("Logs/NotchNest.log"),
            EngineRuntime.setupLog,
            library.appendingPathComponent("Caches/\(bundleID)"),
            library.appendingPathComponent("HTTPStorages/\(bundleID)"),
        ].filter(exists)
        let models = installedModels
            .map { huggingFaceHub.appendingPathComponent("models--" + $0.replacingOccurrences(of: "/", with: "--")) }
            .filter(exists)
        return Plan(files: files, models: models,
                    filesBytes: files.map(size).reduce(0, +),
                    modelsBytes: models.map(size).reduce(0, +))
    }

    /// Asks first, then removes everything and quits. The app itself goes to the Trash.
    @MainActor
    static func confirmAndRun(dictation: DictationManager) {
        Task { @MainActor in
            let found = await Task.detached(priority: .userInitiated) { Uninstaller.plan() }.value
            confirm(found, dictation: dictation)
        }
    }

    @MainActor
    private static func confirm(_ plan: Plan, dictation: DictationManager) {
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "Uninstall NotchNest?"
        alert.informativeText = """
            This removes NotchNest, its dictation engine (\(format(plan.filesBytes)) including \
            ~/.murmur: your dictation settings, personal dictionary and snippets), files saved \
            in the File Tray, your notes and clipboard history, its preferences, its Keychain \
            items and its login item, then moves the app to the Trash. This can't be undone.
            """
        let deleteModels = NSButton(
            checkboxWithTitle: "Also delete the speech and AI models (\(format(plan.modelsBytes)))",
            target: nil, action: nil)
        deleteModels.state = plan.models.isEmpty ? .off : .on
        deleteModels.isEnabled = !plan.models.isEmpty
        deleteModels.toolTip = "In \(huggingFaceHub.path). Other apps that use the Hugging Face cache may share them."
        alert.accessoryView = deleteModels
        alert.addButton(withTitle: isDryRun ? "Uninstall (dry run)" : "Uninstall")
        alert.addButton(withTitle: "Cancel")
        alert.buttons.first?.hasDestructiveAction = true
        NSApp.activate(ignoringOtherApps: true)
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        let withModels = deleteModels.state == .on
        // The engine writes into ~/.murmur; it has to be gone before that is.
        dictation.stop {
            DispatchQueue.global(qos: .userInitiated).async {
                let leftovers = remove(plan, withModels: withModels)
                DispatchQueue.main.async { finish(leftovers: leftovers) }
            }
        }
    }

    /// Returns the paths that couldn't be removed.
    private static func remove(_ plan: Plan, withModels: Bool) -> [String] {
        let targets = plan.files + (withModels ? plan.models : [])
        let bundleID = Bundle.main.bundleIdentifier ?? Keychain.service
        guard !isDryRun else {
            fileLog("uninstall (dry run): would remove \(targets.map(\.path)), the Keychain items, "
                    + "preferences \(bundleID), the login item, privacy grants, and trash \(Bundle.main.bundlePath)")
            return []
        }
        if SMAppService.mainApp.status == .enabled { try? SMAppService.mainApp.unregister() }
        Keychain.deleteAll()
        var leftovers: [String] = []
        for url in targets {
            do { try FileManager.default.removeItem(at: url) }
            catch { leftovers.append(url.path) }
        }
        // Microphone, Calendar and Apple Events grants; Accessibility may need
        // removing by hand (System Settings → Privacy & Security).
        let tcc = Process()
        tcc.executableURL = URL(fileURLWithPath: "/usr/bin/tccutil")
        tcc.arguments = ["reset", "All", bundleID]
        tcc.standardOutput = FileHandle.nullDevice
        tcc.standardError = FileHandle.nullDevice
        if (try? tcc.run()) != nil { tcc.waitUntilExit() }
        UserDefaults.standard.removePersistentDomain(forName: bundleID)
        UserDefaults.standard.synchronize()
        do { try FileManager.default.trashItem(at: Bundle.main.bundleURL, resultingItemURL: nil) }
        catch { leftovers.append(Bundle.main.bundlePath) }
        return leftovers
    }

    @MainActor
    private static func finish(leftovers: [String]) {
        let alert = NSAlert()
        alert.messageText = isDryRun ? "Dry run finished" : "NotchNest is uninstalled"
        var text = isDryRun
            ? "Nothing was removed. ~/Library/Logs/NotchNest.log lists what would have been."
            : "If NotchNest still appears under System Settings → Privacy & Security → Accessibility, remove it there."
        if !leftovers.isEmpty {
            text += "\n\nCouldn't remove:\n" + leftovers.joined(separator: "\n")
        }
        alert.informativeText = text
        alert.addButton(withTitle: "Quit")
        alert.runModal()
        // Not NSApp.terminate: nothing may write preferences or files back now.
        exit(0)
    }

    /// Where huggingface_hub keeps models, resolved the same way it does.
    private static var huggingFaceHub: URL {
        let env = ProcessInfo.processInfo.environment
        if let hub = env["HF_HUB_CACHE"] { return URL(fileURLWithPath: hub) }
        if let home = env["HF_HOME"] { return URL(fileURLWithPath: home).appendingPathComponent("hub") }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".cache/huggingface/hub")
    }

    private static func exists(_ url: URL) -> Bool {
        FileManager.default.fileExists(atPath: url.path)
    }

    private static func size(_ url: URL) -> Int64 {
        let keys: [URLResourceKey] = [.totalFileAllocatedSizeKey, .isRegularFileKey]
        guard let walker = FileManager.default.enumerator(at: url, includingPropertiesForKeys: keys) else {
            return Int64((try? url.resourceValues(forKeys: Set(keys)))?.totalFileAllocatedSize ?? 0)
        }
        var total: Int64 = Int64((try? url.resourceValues(forKeys: Set(keys)))?.totalFileAllocatedSize ?? 0)
        for case let file as URL in walker {
            total += Int64((try? file.resourceValues(forKeys: Set(keys)))?.totalFileAllocatedSize ?? 0)
        }
        return total
    }

    private static func format(_ bytes: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }
}
