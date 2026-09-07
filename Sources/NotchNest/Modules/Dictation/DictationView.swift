import SwiftUI

struct DictationView: View {
    @EnvironmentObject var manager: DictationManager

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            if !manager.murmurRunning {
                offlineState
            } else {
                if manager.micDenied || !manager.engineError.isEmpty {
                    problemBanner
                }
                controls
                if !manager.history.isEmpty {
                    Divider().background(Theme.panelStroke)
                    historyList
                }
            }
        }
    }

    private var header: some View {
        HStack {
            Text("Dictation")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Theme.secondaryText)
            Spacer()
            HStack(spacing: 5) {
                Circle().fill(stateColor).frame(width: 7, height: 7)
                Text(manager.state.label)
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.tertiaryText)
            }
        }
    }

    private var controls: some View {
        HStack(spacing: 14) {
            Button {
                manager.toggle()
            } label: {
                ZStack {
                    Circle().fill(stateColor.opacity(isActive ? 0.9 : 0.16))
                    Image(systemName: isActive ? "stop.fill" : "mic.fill")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(isActive ? Color.white : stateColor)
                }
                .frame(width: 54, height: 54)
            }
            .buttonStyle(.plain)
            .help(isActive ? "Stop dictation" : "Start dictation")

            VStack(alignment: .leading, spacing: 3) {
                Text(isActive ? manager.state.label : "Tap to dictate")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Theme.primaryText)
                Text(manager.latestText.isEmpty ? "Transcriptions appear here and copy to your clipboard."
                                                 : manager.latestText)
                    .font(.system(size: 11))
                    .foregroundStyle(manager.latestText.isEmpty ? Theme.tertiaryText : Theme.secondaryText)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
        }
    }

    private var historyList: some View {
        ScrollView {
            VStack(spacing: 4) {
                ForEach(manager.history) { entry in
                    Button {
                        manager.copyToClipboard(entry.text)
                    } label: {
                        HStack(spacing: 8) {
                            Image(systemName: "text.quote")
                                .font(.system(size: 10))
                                .foregroundStyle(Theme.tertiaryText)
                            Text(entry.text.replacingOccurrences(of: "\n", with: " "))
                                .font(.system(size: 11))
                                .foregroundStyle(Theme.secondaryText)
                                .lineLimit(1)
                            Spacer(minLength: 4)
                            Image(systemName: "doc.on.doc")
                                .font(.system(size: 9))
                                .foregroundStyle(Theme.tertiaryText)
                        }
                        .padding(.horizontal, 9)
                        .padding(.vertical, 6)
                        .background(RoundedRectangle(cornerRadius: 7).fill(Theme.controlBackground.opacity(0.5)))
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .help("Click to copy")
                }
            }
        }
    }

    /// A dead microphone used to fail silently — the engine transcribed digital
    /// silence into hallucinated words. Now it says so, right where you dictate.
    private var problemBanner: some View {
        HStack(alignment: .top, spacing: 7) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 11))
                .foregroundStyle(Theme.warn)
            VStack(alignment: .leading, spacing: 3) {
                Text(manager.micDenied
                     ? "Microphone access is off for NotchNest"
                     : manager.engineError)
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.secondaryText)
                    .fixedSize(horizontal: false, vertical: true)
                Button("Open Microphone settings") {
                    manager.openMicrophoneSettings()
                }
                .buttonStyle(.plain)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Theme.accent)
            }
            Spacer(minLength: 0)
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8).fill(Theme.controlBackground))
    }

    private var offlineState: some View {
        VStack(spacing: 8) {
            Image(systemName: "mic.slash")
                .font(.system(size: 26))
                .foregroundStyle(Theme.tertiaryText)
            Text("Dictation engine isn't running")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Theme.secondaryText)
            Text("Start it to dictate from the notch.")
                .font(.system(size: 11))
                .foregroundStyle(Theme.tertiaryText)
            Button("Start engine") { manager.startEngine() }
                .buttonStyle(.plain)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Theme.accent)
                .padding(.top, 2)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var isActive: Bool {
        manager.state == .listening || manager.state == .processing || manager.state == .command
    }

    private var stateColor: Color {
        switch manager.state {
        case .listening:  return Color(red: 1.0, green: 0.35, blue: 0.38)
        case .processing: return Theme.warn
        case .command:    return Theme.accent
        case .idle:       return Theme.good
        case .offline:    return Theme.tertiaryText
        }
    }
}
