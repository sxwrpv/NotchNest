import SwiftUI

struct NotesView: View {
    @EnvironmentObject var manager: NotesManager

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Quick Note")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.secondaryText)
                Spacer()
                Text("\(manager.text.count)")
                    .font(.system(size: 10))
                    .foregroundStyle(Theme.tertiaryText)
                if !manager.text.isEmpty {
                    Button("Clear") { manager.clear() }
                        .buttonStyle(.plain)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Theme.secondaryText)
                }
            }
            NoteEditor(text: $manager.text)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 10).fill(Theme.controlBackground))
        }
    }
}

/// A dark, borderless multi-line editor. `TextEditor` can't be recolored well on
/// macOS, so we bridge `NSTextView` directly.
private struct NoteEditor: NSViewRepresentable {
    @Binding var text: String

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSTextView.scrollableTextView()
        guard let textView = scroll.documentView as? NSTextView else { return scroll }
        textView.delegate = context.coordinator
        textView.backgroundColor = .clear
        textView.drawsBackground = false
        textView.textColor = .white
        textView.font = .systemFont(ofSize: 13)
        textView.isRichText = false
        textView.insertionPointColor = NSColor(Theme.accent)
        textView.textContainerInset = NSSize(width: 2, height: 4)
        scroll.drawsBackground = false
        scroll.backgroundColor = .clear
        return scroll
    }

    func updateNSView(_ nsView: NSScrollView, context: Context) {
        guard let textView = nsView.documentView as? NSTextView else { return }
        if textView.string != text {
            textView.string = text
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, NSTextViewDelegate {
        let parent: NoteEditor
        init(_ parent: NoteEditor) { self.parent = parent }
        func textDidChange(_ notification: Notification) {
            guard let tv = notification.object as? NSTextView else { return }
            parent.text = tv.string
        }
    }
}
