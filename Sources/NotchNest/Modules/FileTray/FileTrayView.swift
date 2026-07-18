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
    let item: TrayItem
    @State private var hovering = false

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
        .onHover { hovering = $0 }
        .onDrag { NSItemProvider(contentsOf: item.url) ?? NSItemProvider(object: item.url as NSURL) }
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
        guard let view = NSApp.windows.first(where: { $0 is NotchPanel })?.contentView else { return }
        manager.share(item, from: view)
    }
}
