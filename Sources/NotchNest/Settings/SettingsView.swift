import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var settings: SettingsStore
    @EnvironmentObject var clipboard: ClipboardManager
    @EnvironmentObject var spotify: SpotifyService
    @EnvironmentObject var dictation: DictationManager

    private static let hotkeyOptions = [
        "fn", "right_option", "left_option", "right_command", "left_command",
        "right_control", "left_control", "right_shift", "left_shift",
    ]
    private static let modelOptions = [
        "tiny.en", "base.en", "small.en", "small", "medium",
        "large-v3-turbo", "large-v3-turbo-q4",
    ]
    private static let languageOptions = ["auto", "en", "ru", "de", "fr", "es"]
    private static let backendOptions = ["auto", "ollama", "mlx"]
    private static let insertionOptions = ["type", "paste"]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                header

                section("Modules", "Choose what appears in the notch") {
                    ForEach(ModuleID.allCases) { module in
                        Toggle(isOn: Binding(
                            get: { settings.isEnabled(module) },
                            set: { settings.setEnabled(module, $0) }
                        )) {
                            Label(module.title, systemImage: module.symbol)
                        }
                    }
                }

                section("General", nil) {
                    Toggle("Launch at login", isOn: $settings.launchAtLogin)
                }

                section("Reveal Animation", "Tune how the notch opens and closes") {
                    sliderRow("Duration", value: $settings.revealDuration,
                              range: 0.15...0.8, step: 0.01, unit: "s")
                    sliderRow("Bounce", value: $settings.revealBounce,
                              range: 0.0...0.5, step: 0.01)
                    sliderRow("Collapse delay", value: $settings.collapseDelay,
                              range: 0.0...1.5, step: 0.05, unit: "s")
                    sliderRow("Content fade-in delay", value: $settings.fadeInDelay,
                              range: 0.0...0.4, step: 0.01, unit: "s")
                    sliderRow("Content fade-in", value: $settings.fadeInDuration,
                              range: 0.05...0.5, step: 0.01, unit: "s")
                    sliderRow("Content fade-out", value: $settings.fadeOutDuration,
                              range: 0.05...0.4, step: 0.01, unit: "s")
                }

                section("Appearance", "Frosted glass strength") {
                    sliderRow("Glass tint", value: $settings.glassTint,
                              range: 0.18...0.6, step: 0.02)
                }

                section("Clipboard", nil) {
                    Stepper("History limit: \(settings.clipboardLimit)",
                            value: $settings.clipboardLimit, in: 10...200, step: 10)
                        .onChange(of: settings.clipboardLimit) { _, newValue in
                            clipboard.updateLimit(newValue)
                        }
                }

                section("Dictation", "Engine settings — applied live") {
                    if let engine = dictation.settings {
                        dictationPickers(engine)
                    } else {
                        HStack(spacing: 8) {
                            Circle().fill(.gray).frame(width: 8, height: 8)
                            Text("Engine offline — settings appear when it's running")
                                .font(.system(size: 12))
                                .foregroundStyle(.secondary)
                            Spacer()
                            Button("Start engine") { dictation.startEngine() }
                        }
                    }
                }

                section("Spotify", "Enables the Like button in Now Playing") {
                    TextField("Client ID", text: $settings.spotifyClientID)
                        .textFieldStyle(.roundedBorder)
                    HStack(spacing: 8) {
                        Circle()
                            .fill(spotifyStatusColor)
                            .frame(width: 8, height: 8)
                        Text(spotifyStatusText)
                            .font(.system(size: 12))
                            .foregroundStyle(.secondary)
                        Spacer()
                        if spotify.connection == .connected {
                            Button("Disconnect") { spotify.disconnect() }
                        } else {
                            Button(spotify.connection == .connecting ? "Waiting…" : "Connect") {
                                spotify.connect()
                            }
                            .disabled(spotify.connection == .connecting)
                        }
                    }
                    if let error = spotify.lastError {
                        Text(error)
                            .font(.system(size: 11))
                            .foregroundStyle(.red)
                    }
                    Text("One-time setup: developer.spotify.com → Dashboard → Create app → add redirect URI \(SpotifyService.redirectURI) → paste the app's Client ID above → Connect.")
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }

                section("Pomodoro", nil) {
                    Stepper("Focus: \(settings.workMinutes) min",
                            value: $settings.workMinutes, in: 5...90, step: 5)
                    Stepper("Short break: \(settings.shortBreakMinutes) min",
                            value: $settings.shortBreakMinutes, in: 1...30, step: 1)
                    Stepper("Long break: \(settings.longBreakMinutes) min",
                            value: $settings.longBreakMinutes, in: 5...45, step: 5)
                }

                HStack {
                    Spacer()
                    Button("Quit NotchNest") { NSApp.terminate(nil) }
                        .foregroundStyle(.red)
                    Spacer()
                }
                .padding(.top, 4)
            }
            .padding(24)
        }
        .frame(width: 380, height: 620)
    }

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: "menubar.rectangle")
                .font(.system(size: 26))
                .foregroundStyle(Theme.accent)
            VStack(alignment: .leading, spacing: 1) {
                Text("NotchNest").font(.system(size: 18, weight: .bold))
                Text("Your notch, powered up").font(.system(size: 12)).foregroundStyle(.secondary)
            }
            Spacer()
        }
    }

    // MARK: - Dictation engine settings

    @ViewBuilder
    private func dictationPickers(_ engine: DictationSettings) -> some View {
        enginePicker("Dictation key", engine.dictationKey,
                     options: Self.hotkeyOptions, key: "hotkeys.dictation_key")
        enginePicker("Command key", engine.commandKey,
                     options: Self.hotkeyOptions, key: "hotkeys.command_key")
        enginePicker("Whisper model", engine.model,
                     options: Self.modelOptions, key: "asr.model")
        enginePicker("Language", engine.language,
                     options: Self.languageOptions, key: "asr.language")
        enginePicker("LLM backend", engine.backend,
                     options: Self.backendOptions, key: "llm.backend")
        enginePicker("Insert text by", engine.insertionMode,
                     options: Self.insertionOptions, key: "insertion.mode")
        engineToggle("Live partial transcript", engine.partials, key: "asr.partials")
        engineToggle("AI cleanup", engine.cleanupEnabled, key: "llm.cleanup_enabled")
        engineToggle("Notifications", engine.notifications, key: "ui.show_notifications")
        Text("Double-tap the dictation key to toggle, hold it for push-to-talk. Model changes re-download weights on first use.")
            .font(.system(size: 10))
            .foregroundStyle(.secondary)
    }

    private func enginePicker(_ title: String, _ current: String,
                              options: [String], key: String) -> some View {
        HStack {
            Text(title).font(.system(size: 12))
            Spacer()
            Picker("", selection: Binding(
                get: { current },
                set: { dictation.setSetting(key, $0) }
            )) {
                // Keep an unlisted current value (e.g. a custom HF model id) selectable.
                ForEach(options.contains(current) ? options : options + [current],
                        id: \.self) { option in
                    Text(option.replacingOccurrences(of: "_", with: " ")).tag(option)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .frame(maxWidth: 180)
        }
    }

    private func engineToggle(_ title: String, _ current: Bool, key: String) -> some View {
        Toggle(title, isOn: Binding(
            get: { current },
            set: { dictation.setSetting(key, $0) }
        ))
        .font(.system(size: 12))
        .toggleStyle(.switch)
        .controlSize(.mini)
    }

    private var spotifyStatusColor: Color {
        switch spotify.connection {
        case .connected: return .green
        case .connecting: return .orange
        case .disconnected: return .gray
        }
    }

    private var spotifyStatusText: String {
        switch spotify.connection {
        case .connected:
            return spotify.webAPIBlocked
                ? "Connected — API needs Premium, driving the Spotify app directly"
                : "Connected"
        case .connecting: return "Waiting for browser authorization…"
        case .disconnected: return "Not connected — Like drives the Spotify app directly"
        }
    }

    @ViewBuilder
    private func sliderRow(_ title: String, value: Binding<Double>,
                           range: ClosedRange<Double>, step: Double,
                           unit: String = "") -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(title).font(.system(size: 12))
                Spacer()
                Text(unit == "s"
                     ? String(format: "%.2fs", value.wrappedValue)
                     : String(format: "%.2f", value.wrappedValue))
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
            }
            Slider(value: value, in: range, step: step)
        }
    }

    @ViewBuilder
    private func section<Content: View>(_ title: String, _ subtitle: String?,
                                        @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 13, weight: .semibold))
                if let subtitle {
                    Text(subtitle).font(.system(size: 11)).foregroundStyle(.secondary)
                }
            }
            content()
        }
    }
}
