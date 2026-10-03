import AppKit

/// Borderless, non-activating panel that floats above the menu bar and hugs the notch.
final class NotchPanel: NSPanel {
    init(contentRect: CGRect) {
        super.init(
            contentRect: contentRect,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )

        isFloatingPanel = true
        // Just above the menu bar (24) and status items (25). Not higher: macOS
        // stops delivering drag-and-drop to windows at the shielding tier, which
        // is what kept files from ever landing in the File Tray.
        level = NSWindow.Level(rawValue: NSWindow.Level.mainMenu.rawValue + 3)
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        isMovableByWindowBackground = false
        isMovable = false
        hidesOnDeactivate = false
        titleVisibility = .hidden
        titlebarAppearsTransparent = true
        // Allow the panel to receive mouse events (hover, drag-in) without stealing key focus.
        becomesKeyOnlyIfNeeded = true
    }

    // A borderless panel normally refuses key/main; permit it so text fields work when needed.
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}
