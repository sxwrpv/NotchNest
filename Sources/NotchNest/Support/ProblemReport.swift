import AppKit

/// "Report a Problem…" and "Show Logs" in Settings. The report is a new
/// GitHub issue pre-filled with the version, macOS, chip and engine state:
/// nothing about the user, and nothing is sent until they submit it.
enum ProblemReport {
    static func open(engineInstalled: Bool, engineRunning: Bool) {
        let info = Bundle.main.infoDictionary ?? [:]
        let version = info["CFBundleShortVersionString"] as? String ?? "?"
        let build = info["CFBundleVersion"] as? String ?? "?"
        let memory = ByteCountFormatter.string(
            fromByteCount: Int64(ProcessInfo.processInfo.physicalMemory), countStyle: .memory)
        let engine = !engineInstalled ? "not installed" : engineRunning ? "running" : "installed, not running"
        let body = """
            **What happened?**


            **What did you expect instead?**


            **Steps to make it happen again**


            ---
            NotchNest \(version) (\(build)) · macOS \(ProcessInfo.processInfo.operatingSystemVersionString) · \(chip) · \(memory)
            Dictation engine: \(engine)
            """
        var components = URLComponents(string: "https://github.com/\(UpdateFeed.repository)/issues/new")!
        components.queryItems = [URLQueryItem(name: "body", value: body)]
        if let url = components.url { NSWorkspace.shared.open(url) }
    }

    /// Selects the log files in Finder, so the user can look before attaching
    /// any. None of them contains dictated words at the default log level.
    static func revealLogs() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let logs = [
            home.appendingPathComponent("Library/Logs/NotchNest.log"),
            EngineRuntime.setupLog,
            home.appendingPathComponent(".murmur/murmur.log"),
        ].filter { FileManager.default.fileExists(atPath: $0.path) }
        if logs.isEmpty {
            NSWorkspace.shared.open(home.appendingPathComponent("Library/Logs"))
        } else {
            NSWorkspace.shared.activateFileViewerSelecting(logs)
        }
    }

    private static var chip: String {
        var size = 0
        sysctlbyname("machdep.cpu.brand_string", nil, &size, nil, 0)
        guard size > 0 else { return "unknown chip" }
        var buffer = [CChar](repeating: 0, count: size)
        sysctlbyname("machdep.cpu.brand_string", &buffer, &size, nil, 0)
        return buffer.withUnsafeBufferPointer { String(cString: $0.baseAddress!) }
    }
}
