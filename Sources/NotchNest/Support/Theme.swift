import SwiftUI

/// Central place for colors, sizes and animations so the whole notch reads as one system.
enum Theme {
    // Surfaces
    static let notchFill = Color.black
    static let panelStroke = Color.white.opacity(0.08)

    // Text
    static let primaryText = Color.white
    static let secondaryText = Color.white.opacity(0.6)
    static let tertiaryText = Color.white.opacity(0.35)

    // Accents
    static let accent = Color(red: 0.40, green: 0.78, blue: 1.0)
    static let good = Color(red: 0.30, green: 0.85, blue: 0.55)
    static let warn = Color(red: 1.0, green: 0.72, blue: 0.30)

    // Control chrome
    static let controlBackground = Color.white.opacity(0.08)
    static let controlBackgroundHover = Color.white.opacity(0.16)

    // Geometry
    static let cornerRadius: CGFloat = 22
    static let contentPadding: CGFloat = 14

    // Motion
    //
    // The reveal itself is driven by ONE spring built live from user settings (see
    // NotchRootView) so the notch shape and its contents move in lockstep. Springs
    // are Apple's standard motion curve (WWDC23, "Animate with springs").
    // This curve is only for small in-panel control affordances.
    static let contentAnimation = Animation.easeInOut(duration: 0.18)
}

/// A rectangle that is flush (square) on the top edge and rounded on the bottom —
/// the classic "notch panel" silhouette.
struct NotchPanelShape: Shape {
    var radius: CGFloat

    /// Lets the corner radius interpolate smoothly alongside the size animation
    /// instead of snapping between collapsed/expanded values.
    var animatableData: CGFloat {
        get { radius }
        set { radius = newValue }
    }

    func path(in rect: CGRect) -> Path {
        let r = min(radius, min(rect.width, rect.height) / 2)
        var p = Path()
        p.move(to: CGPoint(x: rect.minX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - r))
        p.addArc(center: CGPoint(x: rect.maxX - r, y: rect.maxY - r),
                 radius: r, startAngle: .degrees(0), endAngle: .degrees(90), clockwise: false)
        p.addLine(to: CGPoint(x: rect.minX + r, y: rect.maxY))
        p.addArc(center: CGPoint(x: rect.minX + r, y: rect.maxY - r),
                 radius: r, startAngle: .degrees(90), endAngle: .degrees(180), clockwise: false)
        p.closeSubpath()
        return p
    }
}
