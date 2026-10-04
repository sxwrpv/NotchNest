import AppKit
import SwiftUI
import Combine

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var env: AppEnvironment!
    private var notchController: NotchController!
    private var statusItem: NSStatusItem!
    private var settingsWindow: NSWindow?
    private var setupWindow: NSWindow?
    private var cancellables = Set<AnyCancellable>()

    private static let setupCompletedKey = "setupCompleted"

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Relaunching from /Applications — this copy only has to get out of the way.
        if AppMover.offerMoveIfNeeded() { return }

        env = AppEnvironment()
        notchController = NotchController(env: env)
        let firstRun = !UserDefaults.standard.bool(forKey: Self.setupCompletedKey)
        if firstRun { applyFirstRunDefaults() }
        env.startServices()
        setupStatusItem()

        NotificationCenter.default.publisher(for: .openSettings)
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.openSettings() }
            .store(in: &cancellables)
        NotificationCenter.default.publisher(for: .openSetup)
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in self?.openSetup() }
            .store(in: &cancellables)

        let wantsDictation = env.settings.isEnabled(.dictation)
        if firstRun || (wantsDictation && !env.engineInstaller.isReady) {
            openSetup()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        // One app: quitting NotchNest also stops the dictation engine child.
        env?.dictation.stop()
    }

    /// New Mac, installed copy: open at login unless the user already chose.
    private func applyFirstRunDefaults() {
        if AppMover.isInApplications,
           UserDefaults.standard.object(forKey: "launchAtLogin") == nil {
            env.settings.launchAtLogin = true
        }
    }

    // MARK: - Status bar

    @MainActor
    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "rectangle.topthird.inset.filled",
                                   accessibilityDescription: "NotchNest")
        }

        // @Published sends the new value before the property changes, so build
        // from the value it hands over.
        env.updates.$phase
            .receive(on: RunLoop.main)
            .sink { [weak self] phase in self?.buildMenu(updatePhase: phase) }
            .store(in: &cancellables)
    }

    @MainActor
    private func buildMenu(updatePhase: UpdateChecker.Phase) {
        let menu = NSMenu()
        if case .available(let release) = updatePhase {
            menu.addItem(withTitle: "Update to NotchNest \(release.version)…",
                         action: #selector(updateApp), keyEquivalent: "")
            menu.addItem(.separator())
        }
        menu.addItem(withTitle: "Toggle Notch", action: #selector(toggleNotch), keyEquivalent: "n")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Settings…", action: #selector(openSettings), keyEquivalent: ",")
        menu.addItem(withTitle: "Setup Assistant…", action: #selector(openSetup), keyEquivalent: "")
        menu.addItem(withTitle: "Check for Updates…", action: #selector(checkForUpdates), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit NotchNest", action: #selector(quit), keyEquivalent: "q")
        for item in menu.items { item.target = self }
        statusItem.menu = menu
    }

    @objc private func toggleNotch() {
        env.notch.toggle()
    }

    @MainActor @objc private func updateApp() {
        if env.updates.canInstall {
            env.updates.install()
        } else {
            env.updates.openReleasePage()
        }
    }

    /// Shows the answer in Settings → General.
    @MainActor @objc private func checkForUpdates() {
        openSettings()
        Task { await env.updates.check(userInitiated: true) }
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    // MARK: - Settings window

    @objc private func openSettings() {
        if let window = settingsWindow {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let view = env.inject(SettingsView())
        let hosting = NSHostingController(rootView: AnyView(view))
        let window = NSWindow(contentViewController: hosting)
        window.title = "NotchNest Settings"
        window.styleMask = [.titled, .closable, .miniaturizable]
        window.isReleasedWhenClosed = false
        window.center()
        window.delegate = self
        settingsWindow = window
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

// MARK: - Setup assistant window

extension AppDelegate {
    @objc func openSetup() {
        if let window = setupWindow {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }
        let view = env.inject(SetupView(onDone: { [weak self] in
            UserDefaults.standard.set(true, forKey: Self.setupCompletedKey)
            self?.setupWindow?.close()
        }))
        let hosting = NSHostingController(rootView: AnyView(view))
        hosting.sizingOptions = [.preferredContentSize]
        let window = NSWindow(contentViewController: hosting)
        window.title = "Welcome to NotchNest"
        window.styleMask = [.titled, .closable, .fullSizeContentView]
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isMovableByWindowBackground = true
        window.isReleasedWhenClosed = false
        window.center()
        window.delegate = self
        setupWindow = window
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

extension AppDelegate: NSWindowDelegate {
    func windowWillClose(_ notification: Notification) {
        let window = notification.object as? NSWindow
        if window == settingsWindow {
            settingsWindow = nil
        }
        if window == setupWindow {
            setupWindow = nil
            // Closing it after a finished install counts as done; otherwise it
            // comes back next launch until dictation is ready.
            if env.engineInstaller.isReady {
                UserDefaults.standard.set(true, forKey: Self.setupCompletedKey)
            }
        }
    }
}
