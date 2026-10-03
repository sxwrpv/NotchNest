import SwiftUI

/// Owns every long-lived manager and the shared notch view-model. Injected into
/// both the notch panel and the settings window so they share one source of truth.
final class AppEnvironment: ObservableObject {
    let settings: SettingsStore
    let nowPlaying: NowPlayingManager
    let spotify: SpotifyService
    let dictation: DictationManager
    let engineInstaller: EngineInstaller
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
        self.engineInstaller = EngineInstaller()
        self.fileTray = FileTrayManager()
        self.clipboard = ClipboardManager(limit: settings.clipboardLimit)
        self.pomodoro = PomodoroManager(settings: settings)
        self.notes = NotesManager()
        self.calendar = CalendarManager()
        self.notch = NotchViewModel(settings: settings)
    }

    func startServices() {
        nowPlaying.start()
        // Restart rather than start, so a repaired environment is picked up too.
        engineInstaller.onReady = { [weak dictation] in dictation?.restartEngine() }
        dictation.start()
        // First launch on a new Mac: provision Python, packages and models
        // right away — the setup assistant shows the progress.
        if settings.isEnabled(.dictation), !engineInstaller.isReady { engineInstaller.install() }
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
            .environmentObject(engineInstaller)
            .environmentObject(fileTray)
            .environmentObject(clipboard)
            .environmentObject(pomodoro)
            .environmentObject(notes)
            .environmentObject(calendar)
            .environmentObject(notch)
    }
}
