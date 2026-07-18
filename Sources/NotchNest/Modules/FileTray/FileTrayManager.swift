import AppKit
import Combine
import UniformTypeIdentifiers

struct TrayItem: Identifiable, Equatable {
    let id: UUID
    let url: URL
    var bookmark: Data

    var displayName: String { url.lastPathComponent }
    var isDirectory: Bool {
        (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? false
    }
    var icon: NSImage { NSWorkspace.shared.icon(forFile: url.path) }
}

/// Holds files the user has dropped on the notch. Persists as bookmarks so the
/// tray survives relaunches even if files move.
final class FileTrayManager: ObservableObject {
    @Published private(set) var items: [TrayItem] = []

    private let defaultsKey = "fileTrayBookmarks"

    init() { load() }

    // MARK: - Mutation

    func add(urls: [URL]) {
        var changed = false
        for url in urls {
            guard !items.contains(where: { $0.url == url }) else { continue }
            guard let bookmark = try? url.bookmarkData(options: [],
                                                        includingResourceValuesForKeys: nil,
                                                        relativeTo: nil) else { continue }
            items.insert(TrayItem(id: UUID(), url: url, bookmark: bookmark), at: 0)
            changed = true
        }
        if changed { save() }
    }

    func remove(_ item: TrayItem) {
        items.removeAll { $0.id == item.id }
        save()
    }

    func clear() {
        items.removeAll()
        save()
    }

    func reveal(_ item: TrayItem) {
        NSWorkspace.shared.activateFileViewerSelecting([item.url])
    }

    func open(_ item: TrayItem) {
        NSWorkspace.shared.open(item.url)
    }

    // MARK: - AirDrop

    /// Opens the standard macOS share sheet anchored to a view, defaulting to AirDrop.
    func share(_ item: TrayItem, from view: NSView) {
        let picker = NSSharingServicePicker(items: [item.url])
        picker.show(relativeTo: .zero, of: view, preferredEdge: .minY)
    }

    // MARK: - Persistence

    private func save() {
        let data = items.map(\.bookmark)
        UserDefaults.standard.set(data, forKey: defaultsKey)
    }

    private func load() {
        guard let stored = UserDefaults.standard.array(forKey: defaultsKey) as? [Data] else { return }
        var resolved: [TrayItem] = []
        for bookmark in stored {
            var stale = false
            guard let url = try? URL(resolvingBookmarkData: bookmark,
                                     options: [],
                                     relativeTo: nil,
                                     bookmarkDataIsStale: &stale) else { continue }
            guard FileManager.default.fileExists(atPath: url.path) else { continue }
            let freshBookmark = stale ? ((try? url.bookmarkData()) ?? bookmark) : bookmark
            resolved.append(TrayItem(id: UUID(), url: url, bookmark: freshBookmark))
        }
        items = resolved
        if resolved.count != stored.count { save() }  // prune stale/missing entries
    }
}
