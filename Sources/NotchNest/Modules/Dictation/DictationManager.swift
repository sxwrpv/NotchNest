import Foundation
import AppKit
import AVFoundation
import Combine
import CryptoKit
import Security

enum DictationState: String {
    case idle, listening, processing, command, offline

    var label: String {
        switch self {
        case .idle:       return "Ready"
        case .listening:  return "Listening…"
        case .processing: return "Transcribing…"
        case .command:    return "Command…"
        case .offline:    return "Engine offline"
        }
    }
}

struct DictationEntry: Identifiable, Equatable {
    let id = UUID()
    let text: String
    let date: Date
}

/// The dictation settings NotchNest exposes; mirrored from the engine's
/// config via the notch bridge (`settings` block in notch.json).
struct DictationSettings: Equatable {
    var dictationKey = "fn"
    var commandKey = "right_option"
    var model = "large-v3-turbo-q4"
    var language = "auto"
    var partials = true
    var cleanupEnabled = true
    var backend = "auto"
    var insertionMode = "type"
    var notifications = true
}

/// Owns the dictation engine (the merged Murmur code, shipped inside the app
/// bundle and provisioned by `EngineInstaller`): spawns it headless as a child
/// process, restarts it if it dies, and talks to it through two files in
/// ~/.murmur (state mirror + signed command file).
final class DictationManager: ObservableObject {
    @Published private(set) var state: DictationState = .offline
    /// This session's transcripts, newest first. Memory only: the engine
    /// clears the text from notch.json shortly after handing it over.
    @Published private(set) var history: [DictationEntry] = []
    @Published private(set) var murmurRunning = false
    /// nil until the engine has reported its config through the bridge.
    @Published private(set) var settings: DictationSettings?
    /// Last problem the engine reported (e.g. a dead microphone); "" when healthy.
    @Published private(set) var engineError: String = ""
    /// Microphone permission is denied or restricted for NotchNest.
    @Published private(set) var micDenied = false
    /// Why the engine couldn't be launched at all; "" when it could.
    @Published private(set) var launchError: String = ""

    private let stateURL = URL(fileURLWithPath:
        NSString(string: "~/.murmur/notch.json").expandingTildeInPath)
    private let cmdURL = URL(fileURLWithPath:
        NSString(string: "~/.murmur/notch.cmd").expandingTildeInPath)

    private var timer: Timer?
    private var lastFinal: String = ""
    private var primed = false
    private var engine: Process?
    private var stoppingEngine = false
    private var pendingCommands: [String] = []
    /// The running engine's per-launch key, handed over on its stdin; it
    /// rejects any notch.cmd command not signed with it. nil while no engine runs.
    private var commandSigningKey: SymmetricKey?
    /// Numbers this engine's commands; it ignores one that doesn't grow.
    private var commandCounter: UInt64 = 0

    func start() {
        startEngine()
        poll()
        timer = Timer.scheduledTimer(withTimeInterval: 0.4, repeats: true) { [weak self] _ in
            self?.poll()
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
        stopEngine()
    }

    // MARK: - Microphone permission

    /// The engine runs as our child process, so macOS attributes its
    /// microphone use to NotchNest. If the app never asks, the engine's input
    /// stream still opens but delivers digital silence — which is exactly how
    /// dictation "works" while transcribing nothing but hallucinations.
    func ensureMicrophoneAccess() {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            micDenied = false
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { [weak self] granted in
                DispatchQueue.main.async { self?.micDenied = !granted }
            }
        default:
            micDenied = true
        }
    }

    func openMicrophoneSettings() {
        guard let url = URL(string:
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")
        else { return }
        NSWorkspace.shared.open(url)
    }

    // MARK: - Engine process lifecycle

    /// Launches the headless engine as OUR child. If an engine from a previous
    /// app run is still alive, it gets terminated and respawned: macOS ties the
    /// microphone session to the responsible (parent) process, so an orphaned
    /// engine keeps heartbeating but captures only silence.
    func startEngine() {
        if engine?.isRunning == true { return }
        // Not provisioned yet — EngineInstaller calls back here when it is.
        guard EngineRuntime.isInstalled else { return }
        do {
            try EngineRuntime.validate()
        } catch {
            launchError = error.localizedDescription
            NSLog("NotchNest: refusing to start dictation engine: \(error.localizedDescription)")
            return
        }
        launchError = ""
        ensureMicrophoneAccess()
        reclaimOrphanIfNeeded()

        let proc = Process()
        proc.executableURL = EngineRuntime.python
        // Relative main.py (cwd = the bundled source) keeps the command line in
        // the shape the orphan reclaim and the engine's single-instance guard match.
        proc.arguments = ["-u", "main.py", "--headless"]
        proc.currentDirectoryURL = EngineRuntime.bundledEngine
        proc.environment = EngineRuntime.pythonEnvironment
        let stdin = Pipe()   // carries the command secret; see handOverCommandSecret(to:)
        proc.standardInput = stdin
        proc.standardOutput = FileHandle.nullDevice   // engine keeps its own log
        proc.standardError = FileHandle.nullDevice
        proc.terminationHandler = { [weak self] p in
            DispatchQueue.main.async {
                // A replaced engine's exit isn't news — only react to the current one.
                guard let self, self.engine === p else { return }
                self.engine = nil
                self.commandSigningKey = nil
                guard !self.stoppingEngine else { return }
                NSLog("NotchNest: dictation engine exited (\(p.terminationStatus)) — restarting in 5s")
                DispatchQueue.main.asyncAfter(deadline: .now() + 5) {
                    if !self.stoppingEngine { self.startEngine() }
                }
            }
        }
        do {
            stoppingEngine = false
            try proc.run()
            engine = proc
            NSLog("NotchNest: dictation engine started (pid \(proc.processIdentifier))")
        } catch {
            NSLog("NotchNest: failed to start dictation engine: \(error)")
            return
        }
        // Without a key the engine still dictates from its hotkeys; it just
        // ignores notch.cmd, so the mic button and settings stop working.
        commandSigningKey = Self.handOverCommandSecret(to: stdin)
        commandCounter = 0
    }

    func stopEngine() {
        stoppingEngine = true
        engine?.terminate()
        engine = nil
        commandSigningKey = nil
    }

    /// Makes a random 32-byte secret, writes it into the engine's stdin as one
    /// hex line and closes our end of the pipe. Only NotchNest holds that pipe,
    /// whereas any process of this user can read a file, another process's
    /// arguments or, with `ps eww`, its environment. Returns the key to sign
    /// commands with, or nil if the hand-over failed.
    private static func handOverCommandSecret(to pipe: Pipe) -> SymmetricKey? {
        let writer = pipe.fileHandleForWriting
        defer { try? writer.close() }
        var secret = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, secret.count, &secret) == errSecSuccess else {
            NSLog("NotchNest: no random bytes for the dictation command secret")
            return nil
        }
        // An engine that has already exited gets EPIPE, not a SIGPIPE that
        // would take NotchNest down with it.
        _ = fcntl(writer.fileDescriptor, F_SETNOSIGPIPE, 1)
        let line = secret.map { String(format: "%02x", $0) }.joined() + "\n"
        do {
            try writer.write(contentsOf: Data(line.utf8))
        } catch {
            NSLog("NotchNest: couldn't hand the dictation engine its command secret: \(error)")
            return nil
        }
        return SymmetricKey(data: secret)
    }

    /// Stops the engine and starts a fresh one once the old process is gone
    /// (e.g. after Accessibility is granted, so its hotkey monitor is trusted).
    func restartEngine() {
        let old = engine
        stopEngine()
        DispatchQueue.global(qos: .userInitiated).async {
            old?.waitUntilExit()
            DispatchQueue.main.async { self.startEngine() }
        }
    }

    /// Terminates any engine process we didn't spawn ourselves so the fresh
    /// child gets a live microphone attribution. The 1s pause lets the old
    /// process exit before the new one runs its single-instance guard.
    private func reclaimOrphanIfNeeded() {
        let find = Process()
        find.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        find.arguments = ["-f", "DictationEngine/.venv/bin/python -u main.py"]
        let pipe = Pipe()
        find.standardOutput = pipe
        guard (try? find.run()) != nil else { return }
        find.waitUntilExit()
        let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(),
                         encoding: .utf8) ?? ""
        let pids = out.split(separator: "\n").compactMap {
            Int32($0.trimmingCharacters(in: .whitespaces))
        }
        guard !pids.isEmpty else { return }
        for pid in pids {
            NSLog("NotchNest: reclaiming orphaned dictation engine (pid \(pid))")
            kill(pid, SIGTERM)
        }
        Thread.sleep(forTimeInterval: 1.0)
    }

    // MARK: - Reading Murmur's state

    private func poll() {
        flushPendingCommands()
        guard
            let data = try? Data(contentsOf: stateURL),
            let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            goOffline(); return
        }

        let ts = (obj["ts"] as? Double) ?? 0
        let fresh = Date().timeIntervalSince1970 - ts < 4.0
        let text = (obj["text"] as? String) ?? ""
        let rawState = (obj["state"] as? String) ?? "idle"

        murmurRunning = fresh
        state = fresh ? (DictationState(rawValue: rawState) ?? .idle) : .offline
        engineError = fresh ? ((obj["error"] as? String) ?? "") : ""

        if fresh, let raw = obj["settings"] as? [String: Any] {
            var s = DictationSettings()
            s.dictationKey = raw["hotkeys.dictation_key"] as? String ?? s.dictationKey
            s.commandKey = raw["hotkeys.command_key"] as? String ?? s.commandKey
            s.model = raw["asr.model"] as? String ?? s.model
            s.language = raw["asr.language"] as? String ?? s.language
            s.partials = raw["asr.partials"] as? Bool ?? s.partials
            s.cleanupEnabled = raw["llm.cleanup_enabled"] as? Bool ?? s.cleanupEnabled
            s.backend = raw["llm.backend"] as? String ?? s.backend
            s.insertionMode = raw["insertion.mode"] as? String ?? s.insertionMode
            s.notifications = raw["ui.show_notifications"] as? Bool ?? s.notifications
            if s != settings { settings = s }
        }

        // Adopt whatever's already there on first read without acting on it,
        // so a stale transcript from a previous session doesn't hijack the clipboard.
        if !primed {
            primed = true
            lastFinal = text
            return
        }

        if fresh, !text.isEmpty, text != lastFinal {
            lastFinal = text
            handleNewTranscript(text)
        }
    }

    private func handleNewTranscript(_ text: String) {
        history.insert(DictationEntry(text: text, date: Date()), at: 0)
        if history.count > 30 { history.removeLast(history.count - 30) }
        copyToClipboard(text)   // user preference: copy to clipboard + show in notch
    }

    private func goOffline() {
        murmurRunning = false
        state = .offline
    }

    // MARK: - Controlling the engine

    func toggle() { sendCommand("toggle") }
    func cancel() { sendCommand("cancel") }

    /// Writes one config value through the bridge: `set <key> <json>`.
    /// Queued because the cmd file holds a single command at a time. The local
    /// mirror updates optimistically so pickers don't snap back during the
    /// ~2s roundtrip (cmd → config write → hot reload → snapshot → poll).
    func setSetting(_ key: String, _ value: some Encodable) {
        guard let data = try? JSONEncoder().encode(value),
              let json = String(data: data, encoding: .utf8) else { return }
        pendingCommands.append("set \(key) \(json)")
        flushPendingCommands()

        if var s = settings {
            switch key {
            case "hotkeys.dictation_key":  s.dictationKey = value as? String ?? s.dictationKey
            case "hotkeys.command_key":    s.commandKey = value as? String ?? s.commandKey
            case "asr.model":              s.model = value as? String ?? s.model
            case "asr.language":           s.language = value as? String ?? s.language
            case "asr.partials":           s.partials = value as? Bool ?? s.partials
            case "llm.cleanup_enabled":    s.cleanupEnabled = value as? Bool ?? s.cleanupEnabled
            case "llm.backend":            s.backend = value as? String ?? s.backend
            case "insertion.mode":         s.insertionMode = value as? String ?? s.insertionMode
            case "ui.show_notifications":  s.notifications = value as? Bool ?? s.notifications
            default: break
            }
            settings = s
        }
    }

    /// Settings wait for a running engine; it has to sign them with its key.
    private func flushPendingCommands() {
        guard !pendingCommands.isEmpty, commandSigningKey != nil,
              !FileManager.default.fileExists(atPath: cmdURL.path) else { return }
        writeCommand(pendingCommands.removeFirst())
    }

    /// Toggle/cancel don't wait: a recording shouldn't start whenever an
    /// engine next comes up.
    private func sendCommand(_ command: String) {
        guard commandSigningKey != nil else {
            NSLog("NotchNest: dictation engine isn't running; dropped \"\(command)\"")
            return
        }
        writeCommand(command)
    }

    /// Writes `<counter> <mac> <command>` to notch.cmd, where mac is the hex
    /// HMAC-SHA256 of "<counter> <command>" under the running engine's key
    /// (format and rationale: the docstring of murmur/notch_bridge.py).
    private func writeCommand(_ command: String) {
        guard let key = commandSigningKey else { return }
        commandCounter += 1
        let mac = HMAC<SHA256>.authenticationCode(
            for: Data("\(commandCounter) \(command)".utf8), using: key)
        let hex = mac.map { String(format: "%02x", $0) }.joined()
        do {
            try Self.writePrivately(Data("\(commandCounter) \(hex) \(command)".utf8), to: cmdURL)
        } catch {
            NSLog("NotchNest: failed to write dictation command: \(error)")
        }
    }

    /// Writes through a temp file renamed into place, so the engine never reads
    /// half a command. mkstemp creates it 0600: readable by this user only.
    private static func writePrivately(_ data: Data, to url: URL) throws {
        var template = Array((url.deletingLastPathComponent().path + "/.notch.cmd.XXXXXX").utf8CString)
        let fd = mkstemp(&template)
        guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        let tmp = template.withUnsafeBufferPointer { String(cString: $0.baseAddress!) }
        let written = data.withUnsafeBytes { write(fd, $0.baseAddress, $0.count) }
        close(fd)
        guard written == data.count, rename(tmp, url.path) == 0 else {
            let code = POSIXErrorCode(rawValue: errno) ?? .EIO
            unlink(tmp)
            throw POSIXError(code)
        }
    }

    func copyToClipboard(_ text: String) {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
    }
}
