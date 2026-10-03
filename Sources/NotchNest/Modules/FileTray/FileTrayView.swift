import SwiftUI

struct FileTrayView: View {
    @EnvironmentObject var manager: FileTrayManager

    private let columns = [GridItem(.adaptive(minimum: 84, maximum: 96), spacing: 10)]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("File Tray")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.secondaryText)
                Spacer()
                if !manager.items.isEmpty {
                    Text("\(manager.items.count)")
                        .font(.system(size: 11)).foregroundStyle(Theme.tertiaryText)
                    Button("Clear") { manager.clear() }
                        .buttonStyle(.plain)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Theme.secondaryText)
                }
            }

            ZStack {
                if manager.items.isEmpty {
                    emptyState
                } else {
                    ScrollView {
                        LazyVGrid(columns: columns, spacing: 10) {
                            ForEach(manager.items) { item in
                                FileChip(item: item)
                            }
                        }
                        .padding(.vertical, 2)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(
                RoundedRectangle(cornerRadius: 12)
                    .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [5]))
                    .foregroundStyle(Theme.panelStroke)
            )
        }
    }

    private var emptyState: some View {
        VStack(spacing: 6) {
            Image(systemName: "tray.and.arrow.down")
                .font(.system(size: 24))
                .foregroundStyle(Theme.tertiaryText)
            Text("Drop files here")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(Theme.secondaryText)
            Text("Drag them back out anytime")
                .font(.system(size: 10))
                .foregroundStyle(Theme.tertiaryText)
        }
    }

}

private struct FileChip: View {
    @EnvironmentObject var manager: FileTrayManager
    @EnvironmentObject var notch: NotchViewModel
    let item: TrayItem
    @State private var hovering = false
    /// Where the chip sits in the panel, so Share… can point at it.
    @State private var frameInPanel: CGRect = .zero

    var body: some View {
        VStack(spacing: 5) {
            Image(nsImage: item.icon)
                .resizable()
                .frame(width: 40, height: 40)
            Text(item.displayName)
                .font(.system(size: 10))
                .foregroundStyle(Theme.secondaryText)
                .lineLimit(1)
                .truncationMode(.middle)
        }
        .frame(width: 84, height: 70)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(hovering ? Theme.controlBackgroundHover : Theme.controlBackground)
        )
        .overlay(alignment: .topTrailing) {
            if hovering {
                Button { manager.remove(item) } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 13))
                        .foregroundStyle(Theme.primaryText, Color.black.opacity(0.5))
                }
                .buttonStyle(.plain)
                .padding(3)
            }
        }
        .onGeometryChange(for: CGRect.self) { $0.frame(in: .global) } action: { frameInPanel = $0 }
        .onHover { hovering = $0 }
        .gesture(DragGesture(minimumDistance: 3).onChanged { _ in
            TrayDrag.begin(item, onLeavePanel: notch.collapse)
        })
        .onTapGesture(count: 2) { manager.open(item) }
        .contextMenu {
            Button("Open") { manager.open(item) }
            Button("Reveal in Finder") { manager.reveal(item) }
            Button("Share…") { share() }
            Divider()
            Button("Remove") { manager.remove(item) }
        }
        .help(item.url.path)
    }

    private func share() {
        // The panel's hosting view is flipped, so SwiftUI's global frame is
        // already in its coordinates.
        guard let view = NSApp.windows.first(where: { $0 is NotchPanel })?.contentView else { return }
        manager.share(item, from: view, at: frameInPanel)
    }
}

/// Drags a tray file out as the file itself. SwiftUI's `.onDrag` copies it into
/// a cache first, so apps received a duplicate (Finder even named it
/// "JPEG image.jpeg") and big files were copied before the drop.
private enum TrayDrag {
    private static let source = Source()

    /// Starts the session from the drag event SwiftUI is currently handling.
    /// `onLeavePanel` runs once the drag is off the panel, to get it out of the way.
    static func begin(_ item: TrayItem, onLeavePanel: @escaping () -> Void) {
        guard !source.active,
              let event = NSApp.currentEvent, event.type == .leftMouseDragged,
              let view = event.window?.contentView else { return }
        let point = view.convert(event.locationInWindow, from: nil)
        let draggingItem = NSDraggingItem(pasteboardWriter: item.url as NSURL)
        draggingItem.setDraggingFrame(NSRect(x: point.x - 20, y: point.y - 20, width: 40, height: 40),
                                      contents: item.icon)
        source.active = true
        source.onLeavePanel = onLeavePanel
        view.beginDraggingSession(with: [draggingItem], event: event, source: source)
    }

    private final class Source: NSObject, NSDraggingSource {
        var active = false
        var onLeavePanel: (() -> Void)?

        // Copy only: the tray points at the user's originals, so a drop (or the
        // Trash) must never move them.
        func draggingSession(_ session: NSDraggingSession,
                             sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation {
            context == .outsideApplication ? .copy : []
        }

        func draggingSession(_ session: NSDraggingSession, movedTo screenPoint: NSPoint) {
            guard let panel = NSApp.windows.first(where: { $0 is NotchPanel }),
                  !panel.frame.contains(screenPoint) else { return }
            onLeavePanel?()
            onLeavePanel = nil
        }

        func draggingSession(_ session: NSDraggingSession, endedAt screenPoint: NSPoint,
                             operation: NSDragOperation) {
            active = false
            onLeavePanel = nil
        }
    }
}
