import Foundation
import Combine
import UserNotifications
import AppKit

enum PomodoroPhase: String {
    case work
    case shortBreak
    case longBreak

    var title: String {
        switch self {
        case .work: return "Focus"
        case .shortBreak: return "Short Break"
        case .longBreak: return "Long Break"
        }
    }
}

/// A classic Pomodoro cycle: focus → short break, and a long break every 4th focus.
final class PomodoroManager: ObservableObject {
    @Published private(set) var phase: PomodoroPhase = .work
    @Published private(set) var remaining: TimeInterval
    @Published private(set) var isRunning = false
    @Published private(set) var completedFocusSessions = 0

    private var timer: Timer?
    private var endDate: Date?
    private weak var settings: SettingsStore?

    init(settings: SettingsStore) {
        self.settings = settings
        self.remaining = TimeInterval(settings.workMinutes * 60)
        requestNotificationPermission()
    }

    var phaseDuration: TimeInterval {
        guard let s = settings else { return 25 * 60 }
        switch phase {
        case .work: return TimeInterval(s.workMinutes * 60)
        case .shortBreak: return TimeInterval(s.shortBreakMinutes * 60)
        case .longBreak: return TimeInterval(s.longBreakMinutes * 60)
        }
    }

    var progress: Double {
        let total = phaseDuration
        guard total > 0 else { return 0 }
        return max(0, min(1, 1 - remaining / total))
    }

    // MARK: - Controls

    func toggle() { isRunning ? pause() : start() }

    func start() {
        guard !isRunning else { return }
        isRunning = true
        endDate = Date().addingTimeInterval(remaining)
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            self?.tick()
        }
    }

    func pause() {
        isRunning = false
        timer?.invalidate()
        timer = nil
        if let endDate { remaining = max(0, endDate.timeIntervalSinceNow) }
    }

    func reset() {
        pause()
        remaining = phaseDuration
    }

    /// Jump to the next phase without waiting for the timer.
    func skip() {
        advancePhase(userInitiated: true)
    }

    // MARK: - Tick

    private func tick() {
        guard let endDate else { return }
        remaining = max(0, endDate.timeIntervalSinceNow)
        if remaining <= 0 {
            completePhase()
        }
    }

    private func completePhase() {
        pause()
        notify(for: phase)
        advancePhase(userInitiated: false)
    }

    private func advancePhase(userInitiated: Bool) {
        let wasRunning = isRunning
        pause()
        switch phase {
        case .work:
            completedFocusSessions += 1
            phase = (completedFocusSessions % 4 == 0) ? .longBreak : .shortBreak
        case .shortBreak, .longBreak:
            phase = .work
        }
        remaining = phaseDuration
        // Auto-continue the cycle only when a phase finished naturally.
        if wasRunning && !userInitiated { start() }
    }

    // MARK: - Notifications

    private func requestNotificationPermission() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    private func notify(for phase: PomodoroPhase) {
        let content = UNMutableNotificationContent()
        switch phase {
        case .work:
            content.title = "Focus session complete"
            content.body = "Nice work. Time for a break."
        case .shortBreak, .longBreak:
            content.title = "Break over"
            content.body = "Back to it — starting your next focus session."
        }
        content.sound = .default
        let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
        NSSound(named: "Glass")?.play()
    }
}
