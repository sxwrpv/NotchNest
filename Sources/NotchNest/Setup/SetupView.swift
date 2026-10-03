import SwiftUI
import AppKit
import AVFoundation
import EventKit

/// First-run setup assistant: shows the engine install as it happens (it
/// starts on its own at launch) and walks through the permissions NotchNest
/// needs, with live status. Closing it never stops the install.
struct SetupView: View {
    @EnvironmentObject var installer: EngineInstaller
    @EnvironmentObject var settings: SettingsStore
    @EnvironmentObject var dictation: DictationManager
    @EnvironmentObject var calendar: CalendarManager
    @StateObject private var permissions = PermissionsMonitor()

    var onDone: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            engineCard
            permissionsCard
            footer
        }
        .padding(.horizontal, 26)
        .padding(.top, 34)
        .padding(.bottom, 22)
        .frame(width: 500)
        .fixedSize(horizontal: false, vertical: true)
        .background(VisualEffectView(material: .underWindowBackground, blending: .behindWindow)
            .ignoresSafeArea())
        .onAppear {
            let engine = dictation
            permissions.onAccessibilityGranted = { [weak engine] in
                // The engine's hotkey monitor only works if it starts trusted.
                engine?.restartEngine()
            }
            permissions.start()
        }
        .onDisappear { permissions.stop() }
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 14) {
            Image(nsImage: NSApp.applicationIconImage)
                .resizable()
                .frame(width: 56, height: 56)
            VStack(alignment: .leading, spacing: 3) {
                Text("Welcome to NotchNest")
                    .font(.system(size: 20, weight: .semibold))
                Text("Your notch toolkit with private, on-device dictation. First-time setup downloads about 3 GB, then everything works offline.")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - Engine

    private var engineCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            cardTitle("Dictation engine", systemImage: "waveform")
            ForEach(EngineInstaller.Step.allCases) { step in
                stepRow(step)
            }
            engineStatus
                .padding(.top, 2)
        }
        .glassCard()
    }

    private enum StepState { case pending, active, done, failed }

    private func state(of step: EngineInstaller.Step) -> StepState {
        switch installer.phase {
        case .ready: return .done
        case .idle: return .pending
        case .running(let current):
            return step < current ? .done : (step == current ? .active : .pending)
        case .failed(let current, _):
            return step < current ? .done : (step == current ? .failed : .pending)
        }
    }

    private func stepRow(_ step: EngineInstaller.Step) -> some View {
        let s = state(of: step)
        return HStack(spacing: 10) {
            Group {
                switch s {
                case .done:
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                case .failed:
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                case .active:
                    if installer.fraction == nil {
                        ProgressView().controlSize(.small).scaleEffect(0.8)
                    } else {
                        Image(systemName: "arrow.down.circle.fill").foregroundStyle(Color.accentColor)
                    }
                case .pending:
                    Image(systemName: "circle.dashed").foregroundStyle(.tertiary)
                }
            }
            .font(.system(size: 14))
            .frame(width: 18, height: 18)

            Text(step.title)
                .font(.system(size: 13, weight: s == .active ? .semibold : .regular))
                .foregroundStyle(s == .pending ? .secondary : .primary)
            Spacer()
        }
    }

    @ViewBuilder
    private var engineStatus: some View {
        switch installer.phase {
        case .running:
            VStack(alignment: .leading, spacing: 6) {
                if let fraction = installer.fraction {
                    ProgressView(value: fraction)
                        .progressViewStyle(.linear)
                }
                HStack(alignment: .firstTextBaseline) {
                    Text(installer.detail)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    Button("Pause") { installer.cancel() }
                        .controlSize(.small)
                }
            }
        case .idle:
            HStack {
                Text(installer.detail.isEmpty ? "Dictation isn't set up yet." : installer.detail)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Set Up Dictation") { installer.install() }
                    .buttonStyle(.borderedProminent)
                    .buttonBorderShape(.capsule)
                    .controlSize(.small)
            }
        case .failed(_, let message):
            VStack(alignment: .leading, spacing: 8) {
                Text(message)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                HStack {
                    Button("Show Log") { NSWorkspace.shared.open(EngineRuntime.setupLog) }
                        .controlSize(.small)
                    Spacer()
                    Button("Try Again") { installer.install() }
                        .buttonStyle(.borderedProminent)
                        .buttonBorderShape(.capsule)
                        .controlSize(.small)
                }
            }
        case .ready:
            VStack(alignment: .leading, spacing: 4) {
                Label(readyHint, systemImage: "mic.fill")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(.primary)
                if !installer.summary.isEmpty {
                    Text(installer.summary)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var readyHint: String {
        let key = Self.keyLabel(dictation.settings?.dictationKey ?? "right_option")
        return "Ready — hold \(key) to talk, double-tap it to toggle."
    }

    static func keyLabel(_ key: String) -> String {
        let side = key.hasPrefix("right_") ? "right " : (key.hasPrefix("left_") ? "left " : "")
        switch key.split(separator: "_").last.map(String.init) ?? key {
        case "option":  return side + "⌥ Option"
        case "command": return side + "⌘ Command"
        case "control": return side + "⌃ Control"
        case "shift":   return side + "⇧ Shift"
        default:        return key
        }
    }

    // MARK: - Permissions

    private var permissionsCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            cardTitle("Permissions", systemImage: "hand.raised.fill")
            permissionRow(
                symbol: "mic.fill", title: "Microphone",
                note: "Hears you only while you dictate",
                status: permissions.microphone,
                action: permissions.requestMicrophone)
            permissionRow(
                symbol: "keyboard", title: "Accessibility",
                note: "The dictation hotkey and typing into other apps",
                status: permissions.accessibility,
                action: permissions.requestAccessibility)
            permissionRow(
                symbol: "calendar", title: "Calendar",
                note: "Today's events in the notch (optional)",
                status: permissions.calendar,
                action: { permissions.requestCalendar(using: calendar) })
            Text("Music and Spotify ask for Automation access the first time you use Now Playing.")
                .font(.system(size: 10))
                .foregroundStyle(.tertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .glassCard()
    }

    private func permissionRow(symbol: String, title: String, note: String,
                               status: PermissionsMonitor.Status,
                               action: @escaping () -> Void) -> some View {
        HStack(spacing: 10) {
            Image(systemName: symbol)
                .font(.system(size: 13))
                .foregroundStyle(Color.accentColor)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.system(size: 13))
                Text(note).font(.system(size: 11)).foregroundStyle(.secondary)
            }
            Spacer()
            switch status {
            case .granted:
                Label("Allowed", systemImage: "checkmark.circle.fill")
                    .labelStyle(.titleAndIcon)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.green)
            case .notAsked:
                Button("Allow", action: action)
                    .buttonBorderShape(.capsule)
                    .controlSize(.small)
            case .denied:
                Button("Open Settings", action: action)
                    .buttonBorderShape(.capsule)
                    .controlSize(.small)
            }
        }
    }

    // MARK: - Footer

    private var footer: some View {
        HStack {
            Toggle("Open NotchNest at login", isOn: $settings.launchAtLogin)
                .toggleStyle(.switch)
                .controlSize(.small)
                .font(.system(size: 12))
            Spacer()
            Button(installer.isReady ? "Start Using NotchNest" : "Continue in Background") {
                onDone()
            }
            .buttonStyle(.borderedProminent)
            .buttonBorderShape(.capsule)
            .controlSize(.large)
            .keyboardShortcut(.defaultAction)
        }
        .padding(.top, 2)
    }

    private func cardTitle(_ title: String, systemImage: String) -> some View {
        Label(title, systemImage: systemImage)
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(.secondary)
    }
}

/// Polls permission state once a second while the setup window is open, so
/// rows flip to "Allowed" the moment the user toggles something in Settings.
final class PermissionsMonitor: ObservableObject {
    enum Status { case notAsked, granted, denied }

    @Published private(set) var microphone: Status = .notAsked
    @Published private(set) var accessibility: Status = .notAsked
    @Published private(set) var calendar: Status = .notAsked

    var onAccessibilityGranted: (() -> Void)?

    private var timer: Timer?
    private var primed = false

    func start() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    func refresh() {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: microphone = .granted
        case .notDetermined: microphone = .notAsked
        default: microphone = .denied
        }

        let trusted = AXIsProcessTrusted()
        if primed, trusted, accessibility != .granted { onAccessibilityGranted?() }
        accessibility = trusted ? .granted : .notAsked

        switch EKEventStore.authorizationStatus(for: .event) {
        case .fullAccess: calendar = .granted
        case .notDetermined: calendar = .notAsked
        default: calendar = .denied
        }
        primed = true
    }

    func requestMicrophone() {
        if microphone == .denied {
            openPrivacyPane("Privacy_Microphone")
            return
        }
        AVCaptureDevice.requestAccess(for: .audio) { _ in
            DispatchQueue.main.async { self.refresh() }
        }
    }

    /// Shows the system prompt the first time; after that macOS stays quiet,
    /// so go straight to the Accessibility pane.
    func requestAccessibility() {
        let promptKey = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        if !AXIsProcessTrustedWithOptions([promptKey: true] as CFDictionary) {
            openPrivacyPane("Privacy_Accessibility")
        }
    }

    func requestCalendar(using manager: CalendarManager) {
        if calendar == .denied {
            openPrivacyPane("Privacy_Calendars")
        } else {
            manager.requestAccess()
        }
    }

    private func openPrivacyPane(_ anchor: String) {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(anchor)") {
            NSWorkspace.shared.open(url)
        }
    }
}

/// Liquid Glass card on macOS 26; frosted material with a hairline elsewhere.
private struct GlassCard: ViewModifier {
    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: 18, style: .continuous)
        let padded = content
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        if #available(macOS 26.0, *) {
            padded.glassEffect(.regular, in: shape)
        } else {
            padded
                .background(shape.fill(.regularMaterial))
                .overlay(shape.strokeBorder(Color.white.opacity(0.14), lineWidth: 0.5))
                .shadow(color: .black.opacity(0.08), radius: 16, y: 8)
        }
    }
}

private extension View {
    func glassCard() -> some View { modifier(GlassCard()) }
}
