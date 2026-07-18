import SwiftUI

/// Owns every long-lived manager and the shared notch view-model. Injected into
/// both the notch panel and the settings window so they share one source of truth.
final class AppEnvironment: ObservableObject {
    let settings: SettingsStore
    let nowPlaying: NowPlayingManager
    let spotify: SpotifyService
    let dictation: DictationManager
    let fileTray: FileTrayManager
    let clipboard: ClipboardManager
    let pomodoro: PomodoroManager
    let notes: NotesManager
    let calendar: CalendarManager
    let notch: NotchViewModel

    // Constructed from the app delegate at launch; main-actor so it can build
    // main-actor services like SpotifyService.
    @MainActor
    init() {
        let settings = SettingsStore()
        self.settings = settings
        self.nowPlaying = NowPlayingManager()
        self.spotify = SpotifyService(settings: settings)
        self.dictation = DictationManager()
        self.fileTray = FileTrayManager()
        self.clipboard = ClipboardManager(limit: settings.clipboardLimit)
        self.pomodoro = PomodoroManager(settings: settings)
        self.notes = NotesManager()
        self.calendar = CalendarManager()
        self.notch = NotchViewModel(settings: settings)
    }

    func startServices() {
        nowPlaying.start()
        dictation.start()
        clipboard.start()
        calendar.start()
    }

    /// Applies the SwiftUI environment objects a view hierarchy needs.
    func inject<Content: View>(_ content: Content) -> some View {
        content
            .environmentObject(self)
            .environmentObject(settings)
            .environmentObject(nowPlaying)
            .environmentObject(spotify)
            .environmentObject(dictation)
            .environmentObject(fileTray)
            .environmentObject(clipboard)
            .environmentObject(pomodoro)
            .environmentObject(notes)
            .environmentObject(calendar)
            .environmentObject(notch)
    }
}
