import SwiftUI

struct CalendarView: View {
    @EnvironmentObject var manager: CalendarManager

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(todayLabel)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.secondaryText)
                Spacer()
                if manager.authorized {
                    Text("\(manager.events.count) events")
                        .font(.system(size: 10))
                        .foregroundStyle(Theme.tertiaryText)
                }
            }

            if !manager.authorized {
                permissionState
            } else if manager.events.isEmpty {
                emptyState
            } else {
                ScrollView {
                    VStack(spacing: 5) {
                        ForEach(manager.events) { event in
                            EventRow(event: event)
                        }
                    }
                }
            }
        }
    }

    private var todayLabel: String {
        let f = DateFormatter()
        f.dateFormat = "EEEE, MMM d"
        return f.string(from: Date())
    }

    private var permissionState: some View {
        VStack(spacing: 8) {
            Image(systemName: "calendar.badge.exclamationmark")
                .font(.system(size: 24))
                .foregroundStyle(Theme.tertiaryText)
            Text(manager.denied ? "Calendar access denied" : "Calendar access needed")
                .font(.system(size: 12))
                .foregroundStyle(Theme.secondaryText)
            if manager.denied {
                Button("Open System Settings") {
                    if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars") {
                        NSWorkspace.shared.open(url)
                    }
                }
                .buttonStyle(.plain)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(Theme.accent)
            } else {
                Button("Grant Access") { manager.requestAccess() }
                    .buttonStyle(.plain)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Theme.accent)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var emptyState: some View {
        VStack(spacing: 6) {
            Image(systemName: "checkmark.circle")
                .font(.system(size: 24))
                .foregroundStyle(Theme.good)
            Text("Nothing on the calendar today")
                .font(.system(size: 12))
                .foregroundStyle(Theme.secondaryText)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct EventRow: View {
    let event: CalendarEvent

    var body: some View {
        HStack(spacing: 10) {
            RoundedRectangle(cornerRadius: 2)
                .fill(color)
                .frame(width: 3, height: 30)
            VStack(alignment: .leading, spacing: 2) {
                Text(event.title)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(Theme.primaryText)
                    .lineLimit(1)
                Text(event.timeLabel)
                    .font(.system(size: 10))
                    .foregroundStyle(Theme.tertiaryText)
            }
            Spacer()
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 8).fill(Theme.controlBackground.opacity(0.5)))
    }

    private var color: Color {
        if let cg = event.calendarColor { return Color(cgColor: cg) }
        return Theme.accent
    }
}
