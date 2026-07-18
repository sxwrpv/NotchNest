import SwiftUI

struct ClipboardView: View {
    @EnvironmentObject var manager: ClipboardManager
    @State private var copiedID: UUID?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.tertiaryText)
                NotchTextField(text: $manager.search, placeholder: "Search clipboard")
                    .frame(height: 18)
                Spacer(minLength: 4)
                Button("Clear") { manager.clearUnpinned() }
                    .buttonStyle(.plain)
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(Theme.secondaryText)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(RoundedRectangle(cornerRadius: 9).fill(Theme.controlBackground))

            if manager.visibleEntries.isEmpty {
                emptyState
            } else {
                ScrollView {
                    VStack(spacing: 5) {
                        ForEach(manager.visibleEntries) { entry in
                            ClipRow(entry: entry, justCopied: copiedID == entry.id) {
                                manager.copy(entry)
                                copiedID = entry.id
                                DispatchQueue.main.asyncAfter(deadline: .now() + 1.1) {
                                    if copiedID == entry.id { copiedID = nil }
                                }
                            } onPin: {
                                manager.togglePin(entry)
                            } onDelete: {
                                manager.delete(entry)
                            }
                        }
                    }
                }
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 6) {
            Image(systemName: "doc.on.clipboard")
                .font(.system(size: 24))
                .foregroundStyle(Theme.tertiaryText)
            Text(manager.search.isEmpty ? "Clipboard history is empty" : "No matches")
                .font(.system(size: 12))
                .foregroundStyle(Theme.secondaryText)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

private struct ClipRow: View {
    let entry: ClipEntry
    let justCopied: Bool
    let onCopy: () -> Void
    let onPin: () -> Void
    let onDelete: () -> Void
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: justCopied ? "checkmark" : (entry.pinned ? "pin.fill" : "doc.text"))
                .font(.system(size: 11))
                .foregroundStyle(justCopied ? Theme.good : (entry.pinned ? Theme.accent : Theme.tertiaryText))
                .frame(width: 16)
            Text(entry.text.replacingOccurrences(of: "\n", with: " "))
                .font(.system(size: 12))
                .foregroundStyle(Theme.primaryText)
                .lineLimit(1)
            Spacer(minLength: 4)
            if hovering {
                Button(action: onPin) {
                    Image(systemName: entry.pinned ? "pin.slash" : "pin")
                        .font(.system(size: 10))
                        .foregroundStyle(Theme.secondaryText)
                }.buttonStyle(.plain)
                Button(action: onDelete) {
                    Image(systemName: "trash")
                        .font(.system(size: 10))
                        .foregroundStyle(Theme.secondaryText)
                }.buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(RoundedRectangle(cornerRadius: 8).fill(hovering ? Theme.controlBackgroundHover : Theme.controlBackground.opacity(0.5)))
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
        .onTapGesture { onCopy() }
        .help(entry.text)
    }
}
