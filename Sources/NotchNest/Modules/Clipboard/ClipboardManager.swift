import AppKit
import Combine

struct ClipEntry: Identifiable, Codable, Equatable {
    let id: UUID
    var text: String
    var date: Date
    var pinned: Bool

    init(id: UUID = UUID(), text: String, date: Date = Date(), pinned: Bool = false) {
        self.id = id
        self.text = text
        self.date = date
        self.pinned = pinned
    }
}

/// Watches the general pasteboard and keeps a searchable, pinnable text history.
final class ClipboardManager: ObservableObject {
    @Published private(set) var entries: [ClipEntry] = []
    @Published var search: String = ""

    private var timer: Timer?
    private var lastChangeCount: Int = NSPasteboard.general.changeCount
    private let defaultsKey = "clipboardHistory"
    private var limit: Int

    /// Set true while *we* write to the pasteboard, so we don't re-capture our own copy.
    private var suppressNextCapture = false

    init(limit: Int) {
        self.limit = limit
        load()
    }

    func updateLimit(_ newLimit: Int) {
        limit = max(5, newLimit)
        trim()
        save()
    }

    func start() {
        timer = Timer.scheduledTimer(withTimeInterval: 0.6, repeats: true) { [weak self] _ in
            self?.poll()
        }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    var visibleEntries: [ClipEntry] {
        let sorted = entries.sorted { a, b in
            if a.pinned != b.pinned { return a.pinned && !b.pinned }
            return a.date > b.date
        }
        guard !search.isEmpty else { return sorted }
        return sorted.filter { $0.text.localizedCaseInsensitiveContains(search) }
    }

    // MARK: - Polling

    private func poll() {
        let pb = NSPasteboard.general
        guard pb.changeCount != lastChangeCount else { return }
        lastChangeCount = pb.changeCount
        if suppressNextCapture { suppressNextCapture = false; return }
        guard let text = pb.string(forType: .string) else { return }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        capture(text)
    }

    private func capture(_ text: String) {
        // Move an existing identical entry to the top instead of duplicating.
        if let idx = entries.firstIndex(where: { $0.text == text }) {
            entries[idx].date = Date()
        } else {
            entries.insert(ClipEntry(text: text), at: 0)
        }
        trim()
        save()
    }

    // MARK: - Actions

    func copy(_ entry: ClipEntry) {
        suppressNextCapture = true
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(entry.text, forType: .string)
        lastChangeCount = pb.changeCount
        // Bump recency.
        if let idx = entries.firstIndex(where: { $0.id == entry.id }) {
            entries[idx].date = Date()
            save()
        }
    }

    func togglePin(_ entry: ClipEntry) {
        guard let idx = entries.firstIndex(where: { $0.id == entry.id }) else { return }
        entries[idx].pinned.toggle()
        save()
    }

    func delete(_ entry: ClipEntry) {
        entries.removeAll { $0.id == entry.id }
        save()
    }

    func clearUnpinned() {
        entries.removeAll { !$0.pinned }
        save()
    }

    // MARK: - Housekeeping

    private func trim() {
        // Never drop pinned entries; cap the unpinned tail.
        let pinned = entries.filter { $0.pinned }
        var unpinned = entries.filter { !$0.pinned }.sorted { $0.date > $1.date }
        if unpinned.count > limit {
            unpinned = Array(unpinned.prefix(limit))
        }
        entries = pinned + unpinned
    }

    private func save() {
        if let data = try? JSONEncoder().encode(entries) {
            UserDefaults.standard.set(data, forKey: defaultsKey)
        }
    }

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: defaultsKey),
              let decoded = try? JSONDecoder().decode([ClipEntry].self, from: data) else { return }
        entries = decoded
    }
}
