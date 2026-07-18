import SwiftUI
import Combine

extension ModuleID {
    /// Preferred expanded-panel size. Simple modules get a compact popup instead
    /// of one full-size window; list-heavy modules get more room and scroll.
    var panelSize: CGSize {
        switch self {
        case .nowPlaying: return CGSize(width: 560, height: 175)
        case .dictation:  return CGSize(width: 470, height: 215)
        case .fileTray:   return CGSize(width: 560, height: 240)
        case .clipboard:  return CGSize(width: 560, height: 250)
        case .pomodoro:   return CGSize(width: 450, height: 200)
        case .notes:      return CGSize(width: 520, height: 230)
        case .calendar:   return CGSize(width: 500, height: 240)
        }
    }
}

/// Shared UI state for the notch: whether it is expanded, which module tab is
/// selected, and whether the user has pinned it open.
final class NotchViewModel: ObservableObject {
    @Published var isExpanded = false
    @Published var isPinned = false
    @Published var selectedTab: ModuleID

    /// Current pill / panel dimensions, kept in sync by `NotchController` so the
    /// SwiftUI layer can size and clip the reveal itself.
    @Published var collapsedSize = CGSize(width: 190, height: 34)
    @Published var expandedSize = CGSize(width: 640, height: 250)

    private let settings: SettingsStore

    init(settings: SettingsStore) {
        self.settings = settings
        self.selectedTab = settings.orderedEnabledModules.first ?? .nowPlaying
    }

    func expand() {
        guard !isExpanded else { return }
        // Make sure the selected tab is still enabled.
        if !settings.isEnabled(selectedTab) {
            selectedTab = settings.orderedEnabledModules.first ?? .nowPlaying
        }
        isExpanded = true
    }

    func collapse() {
        guard isExpanded, !isPinned else { return }
        isExpanded = false
    }

    func toggle() {
        isExpanded ? forceCollapse() : expand()
    }

    func forceCollapse() {
        isPinned = false
        isExpanded = false
    }

    func select(_ module: ModuleID) {
        selectedTab = module
    }

    /// Open straight to the File Tray — used when a file is dragged onto the notch,
    /// since you can't hover-to-expand while a drag is in progress.
    func presentFileTray() {
        selectedTab = .fileTray
        isExpanded = true
    }
}
