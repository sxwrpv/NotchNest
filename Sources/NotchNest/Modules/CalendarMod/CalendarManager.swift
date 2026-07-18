import Foundation
import Combine
import EventKit

struct CalendarEvent: Identifiable, Equatable {
    let id: String
    let title: String
    let start: Date
    let end: Date
    let isAllDay: Bool
    let calendarColor: CGColor?

    var timeLabel: String {
        if isAllDay { return "All day" }
        return start.shortTime
    }
}

/// Loads today's events from the system calendar via EventKit.
final class CalendarManager: ObservableObject {
    @Published private(set) var events: [CalendarEvent] = []
    @Published private(set) var authorized = false
    @Published private(set) var denied = false

    private let store = EKEventStore()
    private var timer: Timer?

    func start() {
        refreshAuthorization()
        timer = Timer.scheduledTimer(withTimeInterval: 300, repeats: true) { [weak self] _ in
            self?.reload()
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    func requestAccess() {
        store.requestFullAccessToEvents { [weak self] granted, _ in
            DispatchQueue.main.async {
                self?.authorized = granted
                self?.denied = !granted
                if granted { self?.reload() }
            }
        }
    }

    private func refreshAuthorization() {
        switch EKEventStore.authorizationStatus(for: .event) {
        case .fullAccess, .authorized:
            authorized = true
            denied = false
            reload()
        case .denied, .restricted, .writeOnly:
            // writeOnly can't read events, so for our read-only use it's effectively denied.
            authorized = false
            denied = true
        case .notDetermined:
            requestAccess()
        @unknown default:
            authorized = false
        }
    }

    func reload() {
        guard authorized else { return }
        let cal = Calendar.current
        let startOfDay = cal.startOfDay(for: Date())
        guard let endOfDay = cal.date(byAdding: .day, value: 1, to: startOfDay) else { return }
        let predicate = store.predicateForEvents(withStart: startOfDay, end: endOfDay, calendars: nil)
        let ekEvents = store.events(matching: predicate)
        let mapped = ekEvents
            .sorted { $0.startDate < $1.startDate }
            .map { ev in
                CalendarEvent(
                    id: ev.eventIdentifier ?? UUID().uuidString,
                    title: ev.title ?? "(No title)",
                    start: ev.startDate,
                    end: ev.endDate,
                    isAllDay: ev.isAllDay,
                    calendarColor: ev.calendar?.cgColor
                )
            }
        DispatchQueue.main.async { self.events = mapped }
    }
}
