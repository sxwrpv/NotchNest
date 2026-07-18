import AppKit
import Combine

/// Which media app we are talking to.
enum MediaApp: String {
    case music = "Music"
    case spotify = "Spotify"

    var bundleID: String {
        switch self {
        case .music: return "com.apple.Music"
        case .spotify: return "com.spotify.client"
        }
    }
}

struct NowPlayingInfo: Equatable {
    var app: MediaApp
    var title: String
    var artist: String
    var isPlaying: Bool
    /// Spotify track URI ("spotify:track:…") — used by the Like button.
    /// For Apple Music this is a database id and is ignored.
    var trackID: String?
    /// Playback position and track length in seconds (0 when unknown).
    var position: Double = 0
    var duration: Double = 0
}

/// Reads and controls the current track from Apple Music / Spotify using AppleScript.
///
/// We never *launch* the players — we only script them when they are already running,
/// so opening the notch can't spring Music open on you.
final class NowPlayingManager: ObservableObject {
    @Published private(set) var info: NowPlayingInfo?
    @Published private(set) var artwork: NSImage?
    /// When `info` was last read — lets the view tick the position between polls.
    @Published private(set) var lastPollDate = Date()

    private var timer: Timer?
    private let queue = DispatchQueue(label: "com.notchnest.nowplaying", qos: .utility)
    private var lastArtworkKey: String?

    func start() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    // MARK: - Reading

    private func runningApps() -> [MediaApp] {
        let running = NSWorkspace.shared.runningApplications.compactMap { $0.bundleIdentifier }
        return [MediaApp.music, .spotify].filter { running.contains($0.bundleID) }
    }

    func refresh() {
        let candidates = runningApps()
        queue.async { [weak self] in
            guard let self else { return }
            var best: NowPlayingInfo?
            for app in candidates {
                if let info = self.query(app) {
                    // Prefer whichever is actively playing.
                    if info.isPlaying { best = info; break }
                    if best == nil { best = info }
                }
            }
            let art = best.flatMap { self.fetchArtwork(for: $0) }
            DispatchQueue.main.async {
                self.info = best
                self.lastPollDate = Date()
                if let best {
                    let key = "\(best.app.rawValue)|\(best.title)|\(best.artist)"
                    if key != self.lastArtworkKey {
                        self.artwork = art
                        self.lastArtworkKey = key
                    }
                } else {
                    self.artwork = nil
                    self.lastArtworkKey = nil
                }
            }
        }
    }

    private func query(_ app: MediaApp) -> NowPlayingInfo? {
        let script = """
        tell application "\(app.rawValue)"
            if it is running then
                try
                    set ps to player state as string
                    if ps is "playing" or ps is "paused" then
                        set t to name of current track
                        set a to artist of current track
                        set tid to ""
                        try
                            set tid to id of current track as string
                        end try
                        set pos to 0
                        set dur to 0
                        try
                            set pos to player position
                        end try
                        try
                            set dur to duration of current track
                        end try
                        return t & "\u{1F}" & a & "\u{1F}" & ps & "\u{1F}" & tid & "\u{1F}" & pos & "\u{1F}" & dur
                    end if
                end try
            end if
        end tell
        return ""
        """
        guard let out = runAppleScript(script), !out.isEmpty else { return nil }
        let parts = out.components(separatedBy: "\u{1F}")
        guard parts.count == 6 else { return nil }
        let title = parts[0].trimmingCharacters(in: .whitespacesAndNewlines)
        let artist = parts[1].trimmingCharacters(in: .whitespacesAndNewlines)
        let playing = parts[2].trimmingCharacters(in: .whitespacesAndNewlines) == "playing"
        let trackID = parts[3].trimmingCharacters(in: .whitespacesAndNewlines)
        let position = Self.parseNumber(parts[4])
        var duration = Self.parseNumber(parts[5])
        if app == .spotify { duration /= 1000 }  // Spotify reports milliseconds
        if title.isEmpty { return nil }
        return NowPlayingInfo(app: app, title: title, artist: artist,
                              isPlaying: playing,
                              trackID: trackID.isEmpty ? nil : trackID,
                              position: position, duration: max(0, duration))
    }

    /// AppleScript coerces numbers to text with the locale's decimal separator
    /// (e.g. "12,5" on a Russian system) — normalize before parsing.
    private static func parseNumber(_ raw: String) -> Double {
        Double(raw.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: ",", with: ".")) ?? 0
    }

    private func fetchArtwork(for info: NowPlayingInfo) -> NSImage? {
        // Spotify exposes an artwork URL; Music exposes raw image data.
        switch info.app {
        case .spotify:
            let script = """
            tell application "Spotify"
                if it is running then
                    try
                        return artwork url of current track
                    end try
                end if
            end tell
            return ""
            """
            guard let urlString = runAppleScript(script)?.trimmingCharacters(in: .whitespacesAndNewlines),
                  let url = URL(string: urlString),
                  let data = try? Data(contentsOf: url) else { return nil }
            return NSImage(data: data)
        case .music:
            let script = """
            tell application "Music"
                if it is running then
                    try
                        if (count of artworks of current track) > 0 then
                            return raw data of artwork 1 of current track
                        end if
                    end try
                end if
            end tell
            return missing value
            """
            var error: NSDictionary?
            guard let apple = NSAppleScript(source: script) else { return nil }
            let descriptor = apple.executeAndReturnError(&error)
            if error != nil { return nil }
            let data = descriptor.data
            guard !data.isEmpty else { return nil }
            return NSImage(data: data)
        }
    }

    // MARK: - Controls

    func playPause() { control("playpause") }
    func next()      { control("next track") }
    func previous()  { control("previous track") }

    /// Jumps to an absolute position (seconds). Optimistically updates the
    /// local mirror so the slider doesn't snap back before the next poll.
    func seek(to seconds: Double) {
        guard var current = info else { return }
        let clamped = max(0, min(seconds, current.duration))
        current.position = clamped
        info = current
        lastPollDate = Date()
        // AppleScript takes a dot-decimal literal regardless of system locale.
        control(String(format: "set player position to %.1f",
                       locale: Locale(identifier: "en_US_POSIX"), clamped))
    }

    private func control(_ command: String) {
        guard let app = info?.app else { return }
        queue.async { [weak self] in
            let script = """
            tell application "\(app.rawValue)"
                if it is running then
                    try
                        \(command)
                    end try
                end if
            end tell
            """
            _ = self?.runAppleScript(script)
            DispatchQueue.main.async { self?.refresh() }
        }
    }

    // MARK: - AppleScript helper

    @discardableResult
    private func runAppleScript(_ source: String) -> String? {
        var error: NSDictionary?
        guard let script = NSAppleScript(source: source) else { return nil }
        let result = script.executeAndReturnError(&error)
        if let error {
            // -1743 = not authorized (Automation permission not yet granted).
            NSLog("NotchNest AppleScript error: \(error)")
            return nil
        }
        return result.stringValue
    }
}
