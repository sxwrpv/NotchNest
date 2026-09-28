import Foundation
import CryptoKit

/// Where the dictation engine's pieces live. Everything NotchNest ships — the
/// engine source, its hash-locked dependency list and the `uv` installer — is
/// read from inside the signed app bundle. Only the Python environment that
/// `uv` provisions lives outside it, in the user's Application Support.
enum EngineRuntime {
    static let bundledEngine = Bundle.main.bundleURL
        .appendingPathComponent("Contents/Resources/DictationEngine")
    static let uv = Bundle.main.bundleURL.appendingPathComponent("Contents/Helpers/uv")
    static var lockfile: URL { bundledEngine.appendingPathComponent("requirements.lock") }

    static let support = FileManager.default
        .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("NotchNest")
    static let home = support.appendingPathComponent("DictationEngine")
    static let venv = home.appendingPathComponent(".venv")
    static let python = venv.appendingPathComponent("bin/python")
    static let pythonInstalls = support.appendingPathComponent("python")
    static let marker = home.appendingPathComponent("install.json")

    static let caches = FileManager.default
        .urls(for: .cachesDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("NotchNest")
    static let setupLog = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/NotchNest-setup.log")

    struct Marker: Codable {
        var lockSHA256: String
        var appVersion: String
        var installedAt: Date
    }

    static var bundleIsComplete: Bool {
        let fm = FileManager.default
        return fm.isExecutableFile(atPath: uv.path)
            && fm.fileExists(atPath: bundledEngine.appendingPathComponent("main.py").path)
            && fm.fileExists(atPath: bundledEngine.appendingPathComponent("setup_engine.py").path)
            && fm.fileExists(atPath: lockfile.path)
    }

    static var lockDigest: String? {
        guard let data = try? Data(contentsOf: lockfile) else { return nil }
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// Installed means: provisioned from exactly the lockfile this build ships,
    /// and the interpreter is still there. A new lockfile triggers a re-sync.
    static var isInstalled: Bool {
        guard let digest = lockDigest,
              let data = try? Data(contentsOf: marker),
              let marker = try? JSONDecoder.iso.decode(Marker.self, from: data)
        else { return false }
        return marker.lockSHA256 == digest
            && FileManager.default.isExecutableFile(atPath: python.path)
    }

    /// Environment for every engine Python process. Bytecode caches go to
    /// ~/Library/Caches — never into the signed bundle, which would break its
    /// seal — and nothing from a user site-packages or an active venv leaks in.
    static var pythonEnvironment: [String: String] {
        var env = ProcessInfo.processInfo.environment
        for key in ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV"] {
            env.removeValue(forKey: key)
        }
        env["PYTHONPYCACHEPREFIX"] = caches.appendingPathComponent("pycache").path
        env["PYTHONNOUSERSITE"] = "1"
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
        env["HF_HUB_DISABLE_XET"] = "1"
        return env
    }

    /// The engine runs with NotchNest's Microphone and Accessibility grants,
    /// so refuse an interpreter someone else could have swapped in: every
    /// directory on the way must belong to this user and not be group- or
    /// world-writable, and the venv must resolve into our own Python install.
    static func validate() throws {
        let installRoot = pythonInstalls.resolvingSymlinksInPath().path + "/"
        let interpreter = python.resolvingSymlinksInPath()
        guard interpreter.path.hasPrefix(installRoot) else {
            throw InstallError("The engine's Python points outside NotchNest's own install (\(interpreter.path)).")
        }
        for url in [support, home, venv, venv.appendingPathComponent("bin"), pythonInstalls, interpreter] {
            var info = stat()
            guard lstat(url.path, &info) == 0 else {
                throw InstallError("\(url.path) is missing.")
            }
            guard info.st_uid == getuid(), info.st_mode & 0o022 == 0 else {
                throw InstallError("\(url.path) is writable by other users — reinstall the engine.")
            }
        }
    }
}

struct InstallError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

private struct Cancelled: Error {}

/// Provisions the dictation engine on a Mac that has never seen Python: the
/// bundled `uv` installs a private Python 3.12 and the hash-locked packages,
/// then the engine's own setup script tunes ~/.murmur/config.yaml to this
/// Mac's memory and downloads the Whisper + cleanup-LLM weights with progress.
/// Runs off the main thread; every published change is posted back to main.
final class EngineInstaller: ObservableObject {
    enum Step: Int, CaseIterable, Identifiable, Comparable {
        case python, packages, configure, models, verify

        var id: Int { rawValue }

        var title: String {
            switch self {
            case .python:    return "Python runtime"
            case .packages:  return "Speech & AI packages"
            case .configure: return "Settings for this Mac"
            case .models:    return "Speech & AI models"
            case .verify:    return "Final check"
            }
        }

        static func < (a: Step, b: Step) -> Bool { a.rawValue < b.rawValue }
    }

    enum Phase: Equatable {
        case idle
        case running(Step)
        case ready
        case failed(Step, String)
    }

    @Published private(set) var phase: Phase
    /// Latest human-readable status line for the running step.
    @Published private(set) var detail = ""
    /// Determinate progress of the running step, when it can be measured.
    @Published private(set) var fraction: Double?
    /// What auto-configuration chose, e.g. "Tuned for 16 GB: Qwen2.5 3B cleanup".
    @Published private(set) var summary = ""

    /// Called on the main thread once the engine is installed and verified.
    var onReady: (() -> Void)?

    private let queue = DispatchQueue(label: "com.notchnest.engine-installer")
    private let lock = NSLock()
    private var currentProcess: Process?
    private var cancelRequested = false
    private var log: FileHandle?

    init() {
        phase = EngineRuntime.isInstalled ? .ready : .idle
    }

    var isRunning: Bool {
        if case .running = phase { return true }
        return false
    }

    var isReady: Bool { phase == .ready }

    // MARK: - Control

    func install() {
        guard !isRunning else { return }
        lock.withLock { cancelRequested = false }
        phase = .running(.python)
        detail = "Starting…"
        fraction = nil
        queue.async { [weak self] in self?.pipeline() }
    }

    /// Re-runs every step. uv only re-fetches what changed and cached models
    /// are kept, so this is quick unless something was actually broken.
    func repair() {
        guard !isRunning else { return }
        try? FileManager.default.removeItem(at: EngineRuntime.marker)
        install()
    }

    func cancel() {
        lock.withLock {
            cancelRequested = true
            currentProcess?.terminate()
        }
    }

    // MARK: - Pipeline (background queue)

    private func pipeline() {
        openLog()
        defer { try? log?.close(); log = nil }
        var step = Step.python
        do {
            try preflight()

            try prepareDirectories()
            enter(step, "Downloading Python 3.12…")
            // --no-bin: never drop a python3.12 onto the user's PATH (~/.local/bin).
            try runUV(["python", "install", "3.12", "--no-bin"])
            if !FileManager.default.isExecutableFile(atPath: EngineRuntime.python.path) {
                // A dangling venv (its Python was removed) can't be repaired in place.
                try? FileManager.default.removeItem(at: EngineRuntime.venv)
                try runUV(["venv", "--python", "3.12", "--managed-python", EngineRuntime.venv.path])
            }

            step = .packages
            enter(step, "Downloading packages…")
            try runUV(["pip", "sync", "--python", EngineRuntime.python.path,
                       "--require-hashes", EngineRuntime.lockfile.path])

            step = .configure
            enter(step, "Checking this Mac…")
            try runSetup("configure")

            step = .models
            enter(step, "Checking models…")
            try runSetup("models")

            step = .verify
            enter(step, "Loading the engine — the first time takes up to a minute…")
            try runSetup("verify")

            try EngineRuntime.validate()
            try writeMarker()
            write("setup complete")
            DispatchQueue.main.async {
                self.phase = .ready
                self.detail = ""
                self.fraction = nil
                self.onReady?()
            }
        } catch is Cancelled {
            write("setup paused by user")
            DispatchQueue.main.async {
                self.phase = .idle
                self.detail = "Paused — downloads resume where they left off."
                self.fraction = nil
            }
        } catch {
            let message = error.localizedDescription
            write("setup failed at \(step.title): \(message)")
            DispatchQueue.main.async {
                self.phase = .failed(step, message)
                self.fraction = nil
            }
        }
    }

    private func preflight() throws {
        guard EngineRuntime.bundleIsComplete else {
            throw InstallError("This copy of NotchNest is missing its dictation engine files. Rebuild it with ./build.sh.")
        }
        let home = FileManager.default.homeDirectoryForCurrentUser
        if let free = try? home.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
            .volumeAvailableCapacityForImportantUsage,
           free < 4_000_000_000 {
            let gb = String(format: "%.1f", Double(free) / 1e9)
            throw InstallError("Dictation needs about 4 GB of free disk space; this Mac has \(gb) GB available.")
        }
    }

    private func prepareDirectories() throws {
        let fm = FileManager.default
        for dir in [EngineRuntime.support, EngineRuntime.home] {
            try fm.createDirectory(at: dir, withIntermediateDirectories: true)
            try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: dir.path)
        }
        try fm.createDirectory(at: EngineRuntime.caches, withIntermediateDirectories: true)
    }

    private func writeMarker() throws {
        guard let digest = EngineRuntime.lockDigest else {
            throw InstallError("Couldn't read the bundled lockfile.")
        }
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
        let marker = EngineRuntime.Marker(lockSHA256: digest, appVersion: version, installedAt: Date())
        try JSONEncoder.iso.encode(marker).write(to: EngineRuntime.marker, options: .atomic)
    }

    // MARK: - Steps

    private func runUV(_ args: [String]) throws {
        var env = ProcessInfo.processInfo.environment
        env["UV_PYTHON_INSTALL_DIR"] = EngineRuntime.pythonInstalls.path
        env["UV_CACHE_DIR"] = EngineRuntime.caches.appendingPathComponent("uv").path
        env.removeValue(forKey: "VIRTUAL_ENV")

        var tail: [String] = []
        let status = try run(EngineRuntime.uv, args + ["--no-config", "--no-progress"], env: env) { line, _ in
            tail = Array((tail + [line]).suffix(4))
            self.post(detail: Self.uvStatus(line))
        }
        guard status == 0 else {
            let reason = tail.last(where: { $0.lowercased().contains("error") }) ?? tail.last ?? "exit \(status)"
            throw InstallError("\(reason.trimmingCharacters(in: .whitespaces)) — check the internet connection and try again.")
        }
    }

    /// Runs `setup_engine.py <command>` and turns its JSON lines into progress.
    private func runSetup(_ command: String) throws {
        var failure: String?
        let status = try run(EngineRuntime.python, ["-u", "setup_engine.py", command],
                             cwd: EngineRuntime.bundledEngine,
                             env: EngineRuntime.pythonEnvironment) { line, isStdout in
            guard isStdout,
                  let data = line.data(using: .utf8),
                  let event = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let name = event["event"] as? String
            else { return }
            switch name {
            case "progress":
                let done = (event["done"] as? Double) ?? 0
                let total = max((event["total"] as? Double) ?? 1, 1)
                let item = event["item"] as? String ?? "Model"
                let index = event["index"] as? Int ?? 1
                let count = event["count"] as? Int ?? 1
                let of = count > 1 ? " (\(index) of \(count))" : ""
                self.post(detail: "\(item)\(of) — \(Self.bytes(done)) of \(Self.bytes(total))",
                          fraction: done / total)
            case "model_ready":
                self.post(detail: "\(event["item"] as? String ?? "Model") ready", fraction: nil)
            case "configured":
                self.post(summary: Self.describe(configured: event))
            case "error":
                failure = event["message"] as? String
            default:
                break
            }
        }
        guard status == 0 else {
            throw InstallError(failure ?? "The engine's \(command) step exited with status \(status).")
        }
    }

    // MARK: - Process plumbing

    /// Runs a tool to completion, streaming stdout/stderr lines to `onLine`
    /// (and the setup log). Throws `Cancelled` if `cancel()` interrupted it.
    private func run(_ executable: URL, _ args: [String], cwd: URL? = nil,
                     env: [String: String],
                     onLine: @escaping (String, Bool) -> Void) throws -> Int32 {
        let proc = Process()
        proc.executableURL = executable
        proc.arguments = args
        proc.environment = env
        if let cwd { proc.currentDirectoryURL = cwd }
        let out = Pipe(), err = Pipe()
        proc.standardOutput = out
        proc.standardError = err

        // One lock for both streams, so `onLine` never runs on two threads at once.
        let emit = NSLock()
        let stdoutLines = LineBuffer { line in
            emit.withLock { self.write("  \(line)"); onLine(line, true) }
        }
        let stderrLines = LineBuffer { line in
            emit.withLock { self.write("  ! \(line)"); onLine(line, false) }
        }
        out.fileHandleForReading.readabilityHandler = { stdoutLines.feed($0.availableData) }
        err.fileHandleForReading.readabilityHandler = { stderrLines.feed($0.availableData) }

        write("$ \(executable.lastPathComponent) \(args.joined(separator: " "))")
        try lock.withLock {
            if cancelRequested { throw Cancelled() }
            try proc.run()
            currentProcess = proc
        }
        proc.waitUntilExit()

        out.fileHandleForReading.readabilityHandler = nil
        err.fileHandleForReading.readabilityHandler = nil
        stdoutLines.feed(out.fileHandleForReading.readDataToEndOfFile())
        stderrLines.feed(err.fileHandleForReading.readDataToEndOfFile())
        stdoutLines.flush()
        stderrLines.flush()

        let cancelled = lock.withLock { () -> Bool in
            currentProcess = nil
            return cancelRequested
        }
        if cancelled { throw Cancelled() }
        return proc.terminationStatus
    }

    private func enter(_ step: Step, _ detail: String) {
        write("== \(step.title)")
        DispatchQueue.main.async {
            self.phase = .running(step)
            self.detail = detail
            self.fraction = nil
        }
    }

    private func post(detail: String? = nil, summary: String? = nil) {
        DispatchQueue.main.async {
            if let detail { self.detail = detail }
            if let summary { self.summary = summary }
        }
    }

    private func post(detail: String, fraction: Double?) {
        DispatchQueue.main.async {
            self.detail = detail
            self.fraction = fraction
        }
    }

    // MARK: - Setup log (~/Library/Logs/NotchNest-setup.log)

    private func openLog() {
        let url = EngineRuntime.setupLog
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        log = try? FileHandle(forWritingTo: url)
        _ = try? log?.seekToEnd()
        write("---- NotchNest engine setup")
    }

    private func write(_ line: String) {
        let stamp = ISO8601DateFormatter().string(from: Date())
        try? log?.write(contentsOf: Data("\(stamp) \(line)\n".utf8))
    }

    // MARK: - Formatting

    private static func bytes(_ value: Double) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(value), countStyle: .file)
    }

    /// uv's plain-text status lines, trimmed for the one-line detail label.
    private static func uvStatus(_ line: String) -> String {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        return trimmed.count > 80 ? String(trimmed.prefix(79)) + "…" : trimmed
    }

    private static func describe(configured event: [String: Any]) -> String {
        guard event["fresh"] as? Bool == true, let values = event["values"] as? [String: Any] else {
            return "Keeping your existing dictation settings"
        }
        let ram = Int(((event["ram_gb"] as? Double) ?? 0).rounded())
        let llm = (values["llm.mlx.model"] as? String ?? "")
            .replacingOccurrences(of: "mlx-community/", with: "")
            .replacingOccurrences(of: "-Instruct-4bit", with: "")
            .replacingOccurrences(of: "-", with: " ")
        return "Tuned for this \(ram) GB Mac: Whisper large-v3-turbo + \(llm) cleanup"
    }
}

/// Splits a byte stream into lines; thread-safe because pipe handlers and the
/// final drain can briefly overlap.
private final class LineBuffer {
    private var pending = Data()
    private let lock = NSLock()
    private let onLine: (String) -> Void

    init(_ onLine: @escaping (String) -> Void) { self.onLine = onLine }

    func feed(_ data: Data) {
        guard !data.isEmpty else { return }
        let lines: [String] = lock.withLock {
            pending.append(data)
            var found: [String] = []
            while let newline = pending.firstIndex(where: { $0 == 0x0A || $0 == 0x0D }) {
                let chunk = pending[pending.startIndex..<newline]
                pending.removeSubrange(pending.startIndex...newline)
                if let line = String(data: chunk, encoding: .utf8), !line.isEmpty { found.append(line) }
            }
            return found
        }
        lines.forEach(onLine)
    }

    func flush() {
        let rest: String? = lock.withLock {
            defer { pending.removeAll() }
            return pending.isEmpty ? nil : String(data: pending, encoding: .utf8)
        }
        if let rest, !rest.isEmpty { onLine(rest) }
    }
}

extension JSONDecoder {
    static let iso: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }()
}

extension JSONEncoder {
    static let iso: JSONEncoder = {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        return e
    }()
}
