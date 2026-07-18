import Foundation
import Combine

/// A single persistent scratchpad. Saves are debounced so typing stays smooth.
final class NotesManager: ObservableObject {
    @Published var text: String {
        didSet { scheduleSave() }
    }

    private let defaultsKey = "quickNoteText"
    private var saveWorkItem: DispatchWorkItem?

    init() {
        text = UserDefaults.standard.string(forKey: defaultsKey) ?? ""
    }

    private func scheduleSave() {
        saveWorkItem?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            UserDefaults.standard.set(self.text, forKey: self.defaultsKey)
        }
        saveWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4, execute: work)
    }

    func clear() { text = "" }
}
