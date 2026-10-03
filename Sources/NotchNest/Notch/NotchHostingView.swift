import AppKit
import SwiftUI

/// The notch's root view, and its drop target. Files dragged onto the notch are
/// caught here in AppKit rather than with SwiftUI's `.onDrop(of: [.fileURL])`,
/// which also lets the tray take promised files (Photos, Mail attachments).
/// The collapsed notch only gets drops at all thanks to `NotchGlass`'s base.
final class NotchHostingView: NSHostingView<AnyView> {
    private let env: AppEnvironment

    init(env: AppEnvironment, rootView: AnyView) {
        self.env = env
        super.init(rootView: rootView)
        registerForDraggedTypes([.fileURL] + NSFilePromiseReceiver.readableDraggedTypes
            .map { NSPasteboard.PasteboardType($0) })
    }

    @MainActor required init(rootView: AnyView) {
        fatalError("use init(env:rootView:)")
    }

    @MainActor required dynamic init?(coder: NSCoder) {
        fatalError("use init(env:rootView:)")
    }

    // MARK: - NSDraggingDestination

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        guard accepts(sender) else { return [] }
        env.notch.dropTargeted = true
        // You can't hover-to-expand mid-drag, so open the tray to catch the drop.
        env.notch.presentFileTray()
        return .copy
    }

    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        accepts(sender) ? .copy : []
    }

    override func draggingExited(_ sender: NSDraggingInfo?) {
        env.notch.dropTargeted = false
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        env.notch.dropTargeted = false
        let pasteboard = sender.draggingPasteboard

        if let urls = pasteboard.readObjects(forClasses: [NSURL.self],
                                             options: [.urlReadingFileURLsOnly: true]) as? [URL],
           !urls.isEmpty {
            env.fileTray.add(urls: urls)
            fileLog("tray: dropped \(urls.count) file(s)")
            return true
        }

        // Photos, Mail attachments and browser images promise a file instead of
        // pointing at one; the tray keeps its own copy of those.
        if let promises = pasteboard.readObjects(forClasses: [NSFilePromiseReceiver.self])
            as? [NSFilePromiseReceiver], !promises.isEmpty {
            env.fileTray.receive(promises)
            fileLog("tray: receiving \(promises.count) promised file(s)")
            return true
        }
        fileLog("tray: drop had nothing usable (\(pasteboard.types?.map(\.rawValue) ?? []))")
        return false
    }

    private func accepts(_ sender: NSDraggingInfo) -> Bool {
        // A source in this app means a chip is being dragged out of the tray.
        guard env.settings.isEnabled(.fileTray), sender.draggingSource == nil else { return false }
        let pasteboard = sender.draggingPasteboard
        return pasteboard.canReadObject(forClasses: [NSURL.self],
                                        options: [.urlReadingFileURLsOnly: true])
            || pasteboard.canReadObject(forClasses: [NSFilePromiseReceiver.self])
    }
}
