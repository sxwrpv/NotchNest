import SwiftUI
import AppKit

/// Behind-window frosted blur. This is the reliable glassmorphism base on every
/// macOS version; on macOS 26 we use real Liquid Glass instead.
struct VisualEffectView: NSViewRepresentable {
    var material: NSVisualEffectView.Material = .hudWindow
    var blending: NSVisualEffectView.BlendingMode = .behindWindow
    var state: NSVisualEffectView.State = .active

    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = material
        view.blendingMode = blending
        view.state = state
        view.isEmphasized = true
        return view
    }

    func updateNSView(_ nsView: NSVisualEffectView, context: Context) {
        nsView.material = material
        nsView.blendingMode = blending
        nsView.state = state
    }
}

/// Gives a view the notch's glass surface: content clipped to the panel silhouette,
/// a glass pane behind it (Liquid Glass on macOS 26, frosted `NSVisualEffectView`
/// elsewhere), and a bright top rim so it reads as a lit pane of glass.
struct NotchGlass: ViewModifier {
    var shape: NotchPanelShape
    var tint: Double
    var expanded: Bool

    func body(content: Content) -> some View {
        content
            .clipShape(shape)
            .background(glassBase)
            .overlay(rim.opacity(expanded ? 1 : 0))
    }

    /// Collapsed, the pill must read as the physical black notch, so it goes nearly
    /// opaque; expanded, it becomes the glassy panel governed by the user's tint —
    /// floored so the glass is never fully clear and text stays readable.
    private var effectiveTint: Double {
        expanded ? max(0.18, tint) : min(1.0, tint + 0.55)
    }

    @ViewBuilder
    private var glassBase: some View {
        if #available(macOS 26.0, *) {
            Color.clear
                .glassEffect(.regular.tint(Color.black.opacity(effectiveTint)), in: shape)
        } else {
            ZStack {
                VisualEffectView(material: .hudWindow, blending: .behindWindow)
                shape.fill(Color.black.opacity(effectiveTint))
            }
            .clipShape(shape)
        }
    }

    /// Bright top edge fading to nothing — the classic glass specular highlight.
    private var rim: some View {
        shape
            .stroke(
                LinearGradient(
                    colors: [
                        Color.white.opacity(expanded ? 0.5 : 0.32),
                        Color.white.opacity(0.08),
                        Color.white.opacity(0.02)
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                ),
                lineWidth: 1
            )
            .blendMode(.plusLighter)
    }
}

extension View {
    func notchGlass(shape: NotchPanelShape, tint: Double, expanded: Bool) -> some View {
        modifier(NotchGlass(shape: shape, tint: tint, expanded: expanded))
    }
}

/// A circular Liquid-Glass control surface for buttons. Interactive glass on
/// macOS 26, frosted material fallback otherwise.
struct GlassCircle: ViewModifier {
    var hovering: Bool

    func body(content: Content) -> some View {
        content
            .background(base)
            .clipShape(Circle())
            .overlay(Circle().strokeBorder(Color.white.opacity(0.10), lineWidth: 0.5))
    }

    @ViewBuilder
    private var base: some View {
        if #available(macOS 26.0, *) {
            Color.clear
                .glassEffect(
                    .regular.tint(Color.white.opacity(hovering ? 0.18 : 0.08)).interactive(),
                    in: Circle()
                )
        } else {
            ZStack {
                VisualEffectView(material: .hudWindow, blending: .withinWindow)
                Circle().fill(Color.white.opacity(hovering ? 0.18 : 0.08))
            }
        }
    }
}

extension View {
    func glassCircle(hovering: Bool) -> some View {
        modifier(GlassCircle(hovering: hovering))
    }
}
