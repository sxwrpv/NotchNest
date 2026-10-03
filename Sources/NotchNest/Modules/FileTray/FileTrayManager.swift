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

    /// Where promised files (Photos, Mail attachments, browser images) land.
    /// The tray owns these copies and deletes them when they leave the tray.
    static let inbox = FileManager.default
        .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("NotchNest/File Tray", isDirectory: true)
    private let promiseQueue: OperationQueue = {
        let queue = OperationQueue()
        queue.qualityOfService = .userInitiated
        return queue
    }()

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

    /// Writes promised files into a fresh folder under `inbox` (keeping their
    /// names without clashing) and adds each one as it arrives.
    func receive(_ promises: [NSFilePromiseReceiver]) {
        let folder = Self.inbox.appendingPathComponent(UUID().uuidString, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        } catch {
            fileLog("tray: can't create \(folder.path): \(error.localizedDescription)")
            return
        }
        for promise in promises {
            promise.receivePromisedFiles(atDestination: folder, options: [:],
                                         operationQueue: promiseQueue) { [weak self] url, error in
                DispatchQueue.main.async {
                    if let error {
                        fileLog("tray: promised file failed: \(error.localizedDescription)")
                    } else {
                        self?.add(urls: [url])
                    }
                }
            }
        }
    }

    func remove(_ item: TrayItem) {
        items.removeAll { $0.id == item.id }
        discardIfOwned(item.url)
        save()
    }

    func clear() {
        items.forEach { discardIfOwned($0.url) }
        items.removeAll()
        save()
    }

    /// Deletes a promised-file copy the tray made; files that live anywhere
    /// else are only ever referenced, never touched.
    private func discardIfOwned(_ url: URL) {
        let inbox = Self.inbox.standardizedFileURL.path + "/"
        guard url.standardizedFileURL.path.hasPrefix(inbox) else { return }
        let fm = FileManager.default
        try? fm.removeItem(at: url)
        let folder = url.deletingLastPathComponent()
        if (try? fm.contentsOfDirectory(atPath: folder.path))?.isEmpty == true {
            try? fm.removeItem(at: folder)
        }
    }

    func reveal(_ item: TrayItem) {
        NSWorkspace.shared.activateFileViewerSelecting([item.url])
    }

    func open(_ item: TrayItem) {
        NSWorkspace.shared.open(item.url)
    }

    // MARK: - Sharing

    /// Opens the standard macOS share picker (AirDrop, Mail, Messages…) under
    /// `rect` in `view`.
    func share(_ item: TrayItem, from view: NSView, at rect: CGRect) {
        let picker = NSSharingServicePicker(items: [item.url])
        picker.show(relativeTo: rect, of: view, preferredEdge: view.isFlipped ? .maxY : .minY)
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
