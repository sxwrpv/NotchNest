// Renders Resources/AppIcon.icns: a dark screen squircle whose notch has
// opened into a glass panel with a live waveform — the app in one picture.
//
//   swift Resources/make-icon.swift && iconutil -c icns /tmp/AppIcon.iconset -o Resources/AppIcon.icns
import SwiftUI
import AppKit

struct NotchShape: Shape {
    var radius: CGFloat
    func path(in r: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: r.minX, y: r.minY))
        p.addLine(to: CGPoint(x: r.maxX, y: r.minY))
        p.addLine(to: CGPoint(x: r.maxX, y: r.maxY - radius))
        p.addQuadCurve(to: CGPoint(x: r.maxX - radius, y: r.maxY), control: CGPoint(x: r.maxX, y: r.maxY))
        p.addLine(to: CGPoint(x: r.minX + radius, y: r.maxY))
        p.addQuadCurve(to: CGPoint(x: r.minX, y: r.maxY - radius), control: CGPoint(x: r.minX, y: r.maxY))
        p.closeSubpath()
        return p
    }
}

struct Icon: View {
    let accent = Color(red: 0.40, green: 0.78, blue: 1.0)
    let deep = Color(red: 0.16, green: 0.45, blue: 1.0)

    var body: some View {
        let body = RoundedRectangle(cornerRadius: 186, style: .continuous)
        ZStack {
            body
                .fill(LinearGradient(colors: [Color(red: 0.13, green: 0.17, blue: 0.29),
                                              Color(red: 0.03, green: 0.04, blue: 0.09)],
                                     startPoint: .top, endPoint: .bottom))
                .overlay(
                    RadialGradient(colors: [accent.opacity(0.28), .clear],
                                   center: UnitPoint(x: 0.5, y: 0.32), startRadius: 10, endRadius: 520)
                        .clipShape(body))
                .overlay(body.strokeBorder(
                    LinearGradient(colors: [.white.opacity(0.35), .white.opacity(0.04)],
                                   startPoint: .top, endPoint: .bottom), lineWidth: 5))
                .frame(width: 824, height: 824)
                .shadow(color: .black.opacity(0.4), radius: 28, y: 14)

            // The notch, opened into a glass panel.
            VStack(spacing: 0) {
                ZStack {
                    NotchShape(radius: 96)
                        .fill(LinearGradient(colors: [Color.black, Color(red: 0.05, green: 0.07, blue: 0.13)],
                                             startPoint: .top, endPoint: .bottom))
                    NotchShape(radius: 96)
                        .stroke(LinearGradient(colors: [.white.opacity(0.05), .white.opacity(0.42)],
                                               startPoint: .top, endPoint: .bottom), lineWidth: 5)
                    HStack(spacing: 22) {
                        ForEach(Array([0.34, 0.62, 1.0, 0.72, 0.44].enumerated()), id: \.offset) { _, h in
                            Capsule()
                                .fill(LinearGradient(colors: [accent, deep], startPoint: .top, endPoint: .bottom))
                                .frame(width: 40, height: 250 * h)
                                .shadow(color: accent.opacity(0.6), radius: 18)
                        }
                    }
                    .offset(y: 14)
                }
                .frame(width: 560, height: 360)
                Spacer()
            }
            .frame(width: 824, height: 824)
            .clipShape(body)
        }
        .frame(width: 1024, height: 1024)
    }
}

let iconset = URL(fileURLWithPath: "/tmp/AppIcon.iconset")
try? FileManager.default.removeItem(at: iconset)
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)

MainActor.assumeIsolated {
    for (points, scale) in [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2),
                            (256, 1), (256, 2), (512, 1), (512, 2)] {
        let pixels = points * scale
        let renderer = ImageRenderer(content: Icon())
        renderer.scale = CGFloat(pixels) / 1024
        guard let image = renderer.cgImage else { fatalError("render failed at \(pixels)px") }
        let rep = NSBitmapImageRep(cgImage: image)
        let name = scale == 1 ? "icon_\(points)x\(points).png" : "icon_\(points)x\(points)@2x.png"
        try! rep.representation(using: .png, properties: [:])!.write(to: iconset.appendingPathComponent(name))
    }
}
print("wrote \(iconset.path)")
