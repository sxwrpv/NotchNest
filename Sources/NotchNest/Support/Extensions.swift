import SwiftUI
import AppKit

extension Notification.Name {
    static let notchToggle = Notification.Name("com.notchnest.notchToggle")
    static let openSettings = Notification.Name("com.notchnest.openSettings")
}

extension Date {
    var shortTime: String {
        let f = DateFormatter()
        f.timeStyle = .short
        f.dateStyle = .none
        return f.string(from: self)
    }
}

extension TimeInterval {
    /// Formats a duration as m:ss (or h:mm:ss if >= 1h).
    var clockString: String {
        let total = Int(self.rounded())
        let h = total / 3600
        let m = (total % 3600) / 60
        let s = total % 60
        if h > 0 {
            return String(format: "%d:%02d:%02d", h, m, s)
        }
        return String(format: "%d:%02d", m, s)
    }
}

/// A reusable soft, hover-highlighting icon button used across modules.
struct GlassIconButton: View {
    let systemName: String
    var size: CGFloat = 30
    var symbolSize: CGFloat = 13
    var tint: Color = Theme.primaryText
    let action: () -> Void

    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: symbolSize, weight: .semibold))
                .foregroundStyle(tint)
                .frame(width: size, height: size)
                .glassCircle(hovering: hovering)
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .animation(Theme.contentAnimation, value: hovering)
    }
}

/// Text field styled for the dark notch surface (search boxes etc.).
struct NotchTextField: NSViewRepresentable {
    @Binding var text: String
    var placeholder: String
    var onSubmit: (() -> Void)?

    func makeNSView(context: Context) -> NSTextField {
        let tf = NSTextField()
        tf.placeholderString = placeholder
        tf.isBordered = false
        tf.drawsBackground = false
        tf.focusRingType = .none
        tf.textColor = .white
        tf.font = .systemFont(ofSize: 12)
        tf.delegate = context.coordinator
        tf.lineBreakMode = .byTruncatingTail
        tf.cell?.usesSingleLineMode = true
        return tf
    }

    func updateNSView(_ nsView: NSTextField, context: Context) {
        if nsView.stringValue != text {
            nsView.stringValue = text
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, NSTextFieldDelegate {
        let parent: NotchTextField
        init(_ parent: NotchTextField) { self.parent = parent }
        func controlTextDidChange(_ obj: Notification) {
            guard let tf = obj.object as? NSTextField else { return }
            parent.text = tf.stringValue
        }
        func control(_ control: NSControl, textView: NSTextView, doCommandBy selector: Selector) -> Bool {
            if selector == #selector(NSResponder.insertNewline(_:)) {
                parent.onSubmit?()
                return true
            }
            return false
        }
    }
}
