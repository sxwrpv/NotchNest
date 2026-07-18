import Foundation
import Combine
import ServiceManagement

/// Identifiers for each notch module. `rawValue` is used for persistence + ordering.
enum ModuleID: String, CaseIterable, Identifiable, Codable {
    case nowPlaying
    case dictation
    case fileTray
    case clipboard
    case pomodoro
    case notes
    case calendar

    var id: String { rawValue }

    var title: String {
        switch self {
        case .nowPlaying: return "Now Playing"
        case .dictation:  return "Dictation"
        case .fileTray:   return "File Tray"
        case .clipboard:  return "Clipboard"
        case .pomodoro:   return "Timer"
        case .notes:      return "Note"
        case .calendar:   return "Calendar"
        }
    }

    var symbol: String {
        switch self {
        case .nowPlaying: return "music.note"
        case .dictation:  return "mic.fill"
        case .fileTray:   return "tray.full"
        case .clipboard:  return "doc.on.clipboard"
        case .pomodoro:   return "timer"
        case .notes:      return "note.text"
        case .calendar:   return "calendar"
        }
    }
}

/// User-facing preferences, persisted to `UserDefaults`.
final class SettingsStore: ObservableObject {
    private let defaults = UserDefaults.standard

    @Published var enabledModules: Set<ModuleID> {
        didSet { persistModules() }
    }
    @Published var launchAtLogin: Bool {
        didSet { applyLaunchAtLogin() }
    }
    @Published var clipboardLimit: Int {
        didSet { defaults.set(clipboardLimit, forKey: Keys.clipboardLimit) }
    }
    @Published var workMinutes: Int {
        didSet { defaults.set(workMinutes, forKey: Keys.workMinutes) }
    }
    @Published var shortBreakMinutes: Int {
        didSet { defaults.set(shortBreakMinutes, forKey: Keys.shortBreakMinutes) }
    }
    @Published var longBreakMinutes: Int {
        didSet { defaults.set(longBreakMinutes, forKey: Keys.longBreakMinutes) }
    }
    @Published var collapseDelay: Double {
        didSet { defaults.set(collapseDelay, forKey: Keys.collapseDelay) }
    }

    // MARK: Reveal animation
    @Published var revealDuration: Double {
        didSet { defaults.set(revealDuration, forKey: Keys.revealDuration) }
    }
    @Published var revealBounce: Double {
        didSet { defaults.set(revealBounce, forKey: Keys.revealBounce) }
    }
    @Published var fadeInDelay: Double {
        didSet { defaults.set(fadeInDelay, forKey: Keys.fadeInDelay) }
    }
    @Published var fadeInDuration: Double {
        didSet { defaults.set(fadeInDuration, forKey: Keys.fadeInDuration) }
    }
    @Published var fadeOutDuration: Double {
        didSet { defaults.set(fadeOutDuration, forKey: Keys.fadeOutDuration) }
    }

    // MARK: Appearance
    /// 0 = clear glass, 1 = nearly solid. Darkens the frosted panel for legibility.
    @Published var glassTint: Double {
        didSet { defaults.set(glassTint, forKey: Keys.glassTint) }
    }

    // MARK: Spotify
    /// Client ID of the user's own Spotify developer app (needed for the Like button).
    @Published var spotifyClientID: String {
        didSet { defaults.set(spotifyClientID, forKey: Keys.spotifyClientID) }
    }

    private enum Keys {
        static let modules = "enabledModules"
        static let launchAtLogin = "launchAtLogin"
        static let clipboardLimit = "clipboardLimit"
        static let workMinutes = "workMinutes"
        static let shortBreakMinutes = "shortBreakMinutes"
        static let longBreakMinutes = "longBreakMinutes"
        static let collapseDelay = "collapseDelay"
        static let revealDuration = "revealDuration"
        static let revealBounce = "revealBounce"
        static let fadeInDelay = "fadeInDelay"
        static let fadeInDuration = "fadeInDuration"
        static let fadeOutDuration = "fadeOutDuration"
        static let glassTint = "glassTint"
        static let spotifyClientID = "spotifyClientID"
    }

    init() {
        // Modules — default to all enabled.
        if let raw = defaults.array(forKey: Keys.modules) as? [String] {
            let decoded = raw.compactMap { ModuleID(rawValue: $0) }
            enabledModules = Set(decoded)
        } else {
            enabledModules = Set(ModuleID.allCases)
        }

        launchAtLogin = defaults.object(forKey: Keys.launchAtLogin) as? Bool ?? false
        clipboardLimit = defaults.object(forKey: Keys.clipboardLimit) as? Int ?? 50
        workMinutes = defaults.object(forKey: Keys.workMinutes) as? Int ?? 25
        shortBreakMinutes = defaults.object(forKey: Keys.shortBreakMinutes) as? Int ?? 5
        longBreakMinutes = defaults.object(forKey: Keys.longBreakMinutes) as? Int ?? 15
        collapseDelay = defaults.object(forKey: Keys.collapseDelay) as? Double ?? 0.35

        revealDuration = defaults.object(forKey: Keys.revealDuration) as? Double ?? 0.42
        revealBounce = defaults.object(forKey: Keys.revealBounce) as? Double ?? 0.0
        fadeInDelay = defaults.object(forKey: Keys.fadeInDelay) as? Double ?? 0.12
        fadeInDuration = defaults.object(forKey: Keys.fadeInDuration) as? Double ?? 0.22
        fadeOutDuration = defaults.object(forKey: Keys.fadeOutDuration) as? Double ?? 0.12
        glassTint = defaults.object(forKey: Keys.glassTint) as? Double ?? 0.28
        spotifyClientID = defaults.string(forKey: Keys.spotifyClientID) ?? ""

        // Reconcile the persisted login-item state with the system on launch.
        syncLaunchAtLoginFromSystem()
    }

    /// Ordered list of modules currently enabled (used to drive the tab bar).
    var orderedEnabledModules: [ModuleID] {
        ModuleID.allCases.filter { enabledModules.contains($0) }
    }

    func isEnabled(_ module: ModuleID) -> Bool { enabledModules.contains(module) }

    func setEnabled(_ module: ModuleID, _ enabled: Bool) {
        if enabled { enabledModules.insert(module) }
        else { enabledModules.remove(module) }
    }

    private func persistModules() {
        defaults.set(enabledModules.map(\.rawValue), forKey: Keys.modules)
    }

    // MARK: - Launch at login

    private func applyLaunchAtLogin() {
        defaults.set(launchAtLogin, forKey: Keys.launchAtLogin)
        do {
            if launchAtLogin {
                if SMAppService.mainApp.status != .enabled {
                    try SMAppService.mainApp.register()
                }
            } else {
                if SMAppService.mainApp.status == .enabled {
                    try SMAppService.mainApp.unregister()
                }
            }
        } catch {
            NSLog("NotchNest: launch-at-login change failed: \(error)")
        }
    }

    private func syncLaunchAtLoginFromSystem() {
        let enabled = SMAppService.mainApp.status == .enabled
        if enabled != launchAtLogin {
            launchAtLogin = enabled  // triggers didSet -> persists, no-op register since already in sync
        }
    }
}
