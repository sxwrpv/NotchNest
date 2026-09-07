import Foundation
import AppKit
import AVFoundation
import Combine

enum DictationState: String {
    case idle, listening, processing, command, offline

    var label: String {
        switch self {
        case .idle:       return "Ready"
        case .listening:  return "Listening…"
        case .processing: return "Transcribing…"
        case .command:    return "Command…"
        case .offline:    return "Murmur not running"
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

/// Owns the bundled dictation engine (DictationEngine/, the merged Murmur
/// code): spawns it headless as a child process, restarts it if it dies, and
/// talks to it through two files in ~/.murmur (state mirror + command file).
final class DictationManager: ObservableObject {
    @Published private(set) var state: DictationState = .offline
    @Published private(set) var latestText: String = ""
    @Published private(set) var history: [DictationEntry] = []
    @Published private(set) var murmurRunning = false
    /// nil until the engine has reported its config through the bridge.
    @Published private(set) var settings: DictationSettings?
    /// Last problem the engine reported (e.g. a dead microphone); "" when healthy.
    @Published private(set) var engineError: String = ""
    /// Microphone permission is denied or restricted for NotchNest.
    @Published private(set) var micDenied = false

    private let stateURL = URL(fileURLWithPath:
        NSString(string: "~/.murmur/notch.json").expandingTildeInPath)
    private let cmdURL = URL(fileURLWithPath:
        NSString(string: "~/.murmur/notch.cmd").expandingTildeInPath)

    /// DictationEngine lives next to NotchNest.app in the repo; fall back to
    /// the known absolute location when running from somewhere else.
    private static var engineDir: URL {
        let nextToApp = Bundle.main.bundleURL
            .deletingLastPathComponent()
            .appendingPathComponent("DictationEngine")
        if FileManager.default.fileExists(atPath: nextToApp.path) { return nextToApp }
        return URL(fileURLWithPath:
            NSString(string: "~/claude folder/NotchNest/DictationEngine").expandingTildeInPath)
    }

    private var timer: Timer?
    private var lastFinal: String = ""
    private var primed = false
    private var engine: Process?
    private var stoppingEngine = false
    private var pendingCommands: [String] = []

    func start() {
        ensureMicrophoneAccess()
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
        reclaimOrphanIfNeeded()

        let dir = Self.engineDir
        let python = dir.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.fileExists(atPath: python.path) else {
            NSLog("NotchNest: dictation engine not found at \(dir.path)")
            return
        }

        let proc = Process()
        proc.executableURL = python
        proc.arguments = ["-u", "main.py", "--headless"]
        proc.currentDirectoryURL = dir
        proc.standardOutput = FileHandle.nullDevice   // engine keeps its own log
        proc.standardError = FileHandle.nullDevice
        proc.terminationHandler = { [weak self] p in
            DispatchQueue.main.async {
                guard let self else { return }
                self.engine = nil
                guard !self.stoppingEngine else { return }
                NSLog("NotchNest: dictation engine exited (\(p.terminationStatus)) — restarting in 5s")
                DispatchQueue.main.asyncAfter(deadline: .now() + 5) { self.startEngine() }
            }
        }
        do {
            try proc.run()
            engine = proc
            NSLog("NotchNest: dictation engine started (pid \(proc.processIdentifier))")
        } catch {
            NSLog("NotchNest: failed to start dictation engine: \(error)")
        }
    }

    func stopEngine() {
        stoppingEngine = true
        engine?.terminate()
        engine = nil
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
        latestText = text
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

    private func flushPendingCommands() {
        guard !pendingCommands.isEmpty,
              !FileManager.default.fileExists(atPath: cmdURL.path) else { return }
        let next = pendingCommands.removeFirst()
        do {
            try next.write(to: cmdURL, atomically: true, encoding: .utf8)
        } catch {
            NSLog("NotchNest: failed to write dictation command: \(error)")
        }
    }

    private func sendCommand(_ command: String) {
        do {
            try command.write(to: cmdURL, atomically: true, encoding: .utf8)
        } catch {
            NSLog("NotchNest: failed to write dictation command: \(error)")
        }
    }

    func copyToClipboard(_ text: String) {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
    }
}
