import AppKit

/// Computes where the notch (or fallback pill) sits on a given screen, in global
/// screen coordinates (origin at the bottom-left of the main display).
struct NotchGeometry {
    let screen: NSScreen
    let hasPhysicalNotch: Bool
    let collapsedSize: CGSize
    let expandedSize: CGSize

    /// Extra invisible hover margin below the collapsed pill, so the notch is easy to hit.
    static let collapsedHoverPadding: CGFloat = 6
    /// Extra invisible window width so the pill can grow sideways (recording
    /// indicator pops out left of the physical notch) without clipping.
    static let collapsedSideMargin: CGFloat = 44

    init(screen: NSScreen) {
        self.screen = screen

        let notchHeight = screen.safeAreaInsets.top
        let physical = notchHeight > 0

        // Width of the physical notch, derived from the two auxiliary areas beside it.
        var notchWidth: CGFloat = 200
        if physical,
           let left = screen.auxiliaryTopLeftArea,
           let right = screen.auxiliaryTopRightArea {
            notchWidth = screen.frame.width - left.width - right.width
        }

        self.hasPhysicalNotch = physical
        let collapsedH = physical ? max(notchHeight, 32) : 34
        let collapsedW = physical ? notchWidth : 190
        self.collapsedSize = CGSize(width: collapsedW, height: collapsedH)
        // Envelope for the invisible window; the visible panel sizes itself
        // per-module (ModuleID.panelSize) inside this and must always fit.
        self.expandedSize = CGSize(width: 640, height: 290)
    }

    /// Frame for the collapsed pill, flush with the top edge of the screen.
    var collapsedFrame: CGRect {
        let f = screen.frame
        let w = collapsedSize.width + Self.collapsedSideMargin * 2
        let h = collapsedSize.height + Self.collapsedHoverPadding
        let x = f.midX - w / 2
        let y = f.maxY - h
        return CGRect(x: x, y: y, width: w, height: h)
    }

    /// Frame for the expanded panel, centered under the notch and hanging down.
    var expandedFrame: CGRect {
        let f = screen.frame
        let w = expandedSize.width
        let h = expandedSize.height
        let x = f.midX - w / 2
        let y = f.maxY - h
        return CGRect(x: x, y: y, width: w, height: h)
    }

    /// The screen that currently has a notch, or the main screen as a fallback.
    static func preferredScreen() -> NSScreen {
        if let notched = NSScreen.screens.first(where: { $0.safeAreaInsets.top > 0 }) {
            return notched
        }
        return NSScreen.main ?? NSScreen.screens.first!
    }
}
