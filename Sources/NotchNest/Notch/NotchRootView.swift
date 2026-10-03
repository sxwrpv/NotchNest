import SwiftUI

/// The whole notch surface. Draws the glass panel and swaps between the collapsed
/// pill and the expanded, tabbed content based on hover (and on file drags).
struct NotchRootView: View {
    @EnvironmentObject var notch: NotchViewModel
    @EnvironmentObject var settings: SettingsStore
    @EnvironmentObject var nowPlaying: NowPlayingManager
    @EnvironmentObject var pomodoro: PomodoroManager
    @EnvironmentObject var dictation: DictationManager

    @State private var collapseTask: DispatchWorkItem?

    // Animations built live from user settings so the reveal is tunable.
    private var revealSpring: Animation {
        .spring(duration: settings.revealDuration, bounce: settings.revealBounce)
    }
    private var fadeIn: Animation {
        .easeOut(duration: settings.fadeInDuration).delay(settings.fadeInDelay)
    }
    private var fadeOut: Animation {
        .easeIn(duration: settings.fadeOutDuration)
    }

    var body: some View {
        VStack(spacing: 0) {
            notchBox
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .environment(\.colorScheme, .dark)
        .contentShape(Rectangle())
        // Drops are caught by NotchHostingView, which opens the File Tray as a
        // drag arrives; once it leaves (or lands), fold back up when the
        // pointer is off the panel.
        .onChange(of: notch.dropTargeted) { _, targeted in
            if targeted {
                collapseTask?.cancel()
            } else {
                collapseWhenPointerLeaves()
            }
        }
    }

    /// Size of the expanded panel for whatever module is selected — compact
    /// modules get a compact popup. Dictation shrinks further with no history.
    private var currentExpandedSize: CGSize {
        var size = notch.selectedTab.panelSize
        if notch.selectedTab == .dictation {
            if dictation.history.isEmpty { size.height = 165 }
            // the microphone/engine warning needs a row of its own
            if dictation.micDenied || !dictation.engineError.isEmpty { size.height += 64 }
        }
        return size
    }

    /// The glass notch surface. Both content layers are laid out at their *final*
    /// sizes and simply cross-faded; the reveal is the clip shape growing under them,
    /// so nothing reflows during the animation and the whole thing rides one spring.
    /// Hover lives HERE (not on the window) so the panel collapses as soon as the
    /// pointer leaves the visible glass, and per-module size changes re-run the spring.
    private var notchBox: some View {
        let expanded = notch.isExpanded
        let panelSize = currentExpandedSize
        let collapsedSize = collapsedCurrentSize
        let size = expanded ? panelSize : collapsedSize
        let radius: CGFloat = expanded ? Theme.cornerRadius : 10
        let shape = NotchPanelShape(radius: radius)

        return ZStack(alignment: .top) {
            collapsedContent
                .frame(width: collapsedSize.width, height: collapsedSize.height)
                .opacity(expanded ? 0 : 1)
                .animation(expanded ? fadeOut : fadeIn, value: expanded)

            expandedContent
                .frame(width: panelSize.width, height: panelSize.height)
                .opacity(expanded ? 1 : 0)
                .animation(expanded ? fadeIn : fadeOut, value: expanded)
        }
        .frame(width: size.width, height: size.height, alignment: .top)
        .notchGlass(shape: shape, tint: settings.glassTint, expanded: expanded)
        .overlay {
            if notch.dropTargeted {
                shape.stroke(Theme.accent, style: StrokeStyle(lineWidth: 2, dash: [6]))
                    .transition(.opacity)
            }
        }
        .animation(revealSpring, value: expanded)
        .animation(revealSpring, value: size)
        .onHover { hovering in
            if hovering {
                collapseTask?.cancel()
                notch.expand()
            } else {
                scheduleCollapse()
            }
        }
    }

    /// Hover tracking is suspended during a drag, so a drag that passes over
    /// the notch (or ends in a drop) never reports the pointer leaving — the
    /// tray would stay open. Instead, check where the pointer is and collapse
    /// once it's off the panel; hovering back in cancels this as usual.
    private func collapseWhenPointerLeaves() {
        collapseTask?.cancel()
        let task = DispatchWorkItem {
            let panel = NSApp.windows.first { $0 is NotchPanel }
            if let panel, panel.frame.contains(NSEvent.mouseLocation) {
                collapseWhenPointerLeaves()
            } else {
                notch.collapse()
            }
        }
        collapseTask = task
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8, execute: task)
    }

    private func scheduleCollapse() {
        collapseTask?.cancel()
        let task = DispatchWorkItem { notch.collapse() }
        collapseTask = task
        DispatchQueue.main.asyncAfter(deadline: .now() + settings.collapseDelay, execute: task)
    }

    private var dictationActive: Bool {
        dictation.state == .listening || dictation.state == .processing
    }

    /// Actively capturing audio (not just transcribing afterwards).
    private var isRecording: Bool {
        dictation.state == .listening || dictation.state == .command
    }

    /// While recording, the pill grows sideways so a tiny mic indicator pops
    /// out left of the physical notch — visible without opening anything.
    private var collapsedCurrentSize: CGSize {
        var size = notch.collapsedSize
        if isRecording { size.width += 72 }
        return size
    }

    // MARK: - Collapsed

    private var collapsedContent: some View {
        ZStack {
            if isRecording {
                HStack {
                    Image(systemName: "mic.fill")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(Color(red: 1.0, green: 0.35, blue: 0.38))
                        .symbolEffect(.pulse, options: .repeating)
                        .frame(width: 36)
                    Spacer()
                }
                .transition(.opacity)
            }
            collapsedStrip
        }
    }

    private var collapsedStrip: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 0)
            HStack {
                if dictationActive {
                    Capsule().fill(Color(red: 1.0, green: 0.35, blue: 0.38))
                        .frame(width: 26, height: 3)
                        .transition(.opacity)
                } else if nowPlaying.info?.isPlaying == true {
                    Capsule().fill(Theme.accent).frame(width: 26, height: 3)
                        .transition(.opacity)
                }
                Spacer()
                if pomodoro.isRunning {
                    Capsule().fill(pomodoro.phase == .work ? Theme.warn : Theme.good)
                        .frame(width: 26, height: 3)
                        .transition(.opacity)
                }
            }
            .padding(.horizontal, 12)
            .padding(.bottom, 2)
        }
    }

    // MARK: - Expanded

    private var expandedContent: some View {
        VStack(spacing: 0) {
            NotchHeaderView()
                .padding(.horizontal, Theme.contentPadding)
                .padding(.top, 8)
            Divider().background(Theme.panelStroke).padding(.top, 6)
            ZStack {
                NotchModuleContainer(module: notch.selectedTab)
                    .id(notch.selectedTab)
                    .transition(.opacity)
            }
            .animation(Theme.contentAnimation, value: notch.selectedTab)
            .padding(.horizontal, Theme.contentPadding)
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .padding(.top, 2)
    }
}

/// Header: module tabs on the left, pin + settings on the right.
struct NotchHeaderView: View {
    @EnvironmentObject var notch: NotchViewModel
    @EnvironmentObject var settings: SettingsStore

    var body: some View {
        HStack(spacing: 6) {
            ForEach(settings.orderedEnabledModules) { module in
                TabButton(module: module,
                          isSelected: notch.selectedTab == module) {
                    notch.select(module)
                }
            }
            Spacer(minLength: 8)
            GlassIconButton(systemName: notch.isPinned ? "pin.fill" : "pin",
                            size: 26, symbolSize: 11,
                            tint: notch.isPinned ? Theme.accent : Theme.secondaryText) {
                notch.isPinned.toggle()
            }
            GlassIconButton(systemName: "gearshape.fill",
                            size: 26, symbolSize: 11,
                            tint: Theme.secondaryText) {
                NotificationCenter.default.post(name: .openSettings, object: nil)
            }
        }
    }
}

private struct TabButton: View {
    let module: ModuleID
    let isSelected: Bool
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Image(systemName: module.symbol)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(isSelected ? Theme.primaryText : Theme.secondaryText)
                .frame(width: 30, height: 26)
                .background(
                    RoundedRectangle(cornerRadius: 8)
                        .fill(isSelected ? Theme.controlBackgroundHover
                                         : (hovering ? Theme.controlBackground : Color.clear))
                )
        }
        .buttonStyle(.plain)
        .help(module.title)
        .onHover { hovering = $0 }
    }
}

/// Routes the selected module id to its view.
struct NotchModuleContainer: View {
    let module: ModuleID

    var body: some View {
        switch module {
        case .nowPlaying: NowPlayingView()
        case .dictation:  DictationView()
        case .fileTray:   FileTrayView()
        case .clipboard:  ClipboardView()
        case .pomodoro:   PomodoroView()
        case .notes:      NotesView()
        case .calendar:   CalendarView()
        }
    }
}
