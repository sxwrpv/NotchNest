import AppKit
import SwiftUI
import Combine

/// Owns the notch panel: builds it, positions it under the notch, and animates
/// between the collapsed pill and the expanded panel as the view-model changes.
final class NotchController {
    private let env: AppEnvironment
    private let panel: NotchPanel
    private var geometry: NotchGeometry
    private var cancellables = Set<AnyCancellable>()

    /// Deferred window shrink after a collapse, so the panel stays large enough to
    /// contain the shape until the reveal spring has fully settled.
    private var shrinkWorkItem: DispatchWorkItem?
    private var collapseSettle: TimeInterval { env.settings.revealDuration + 0.1 }

    init(env: AppEnvironment) {
        self.env = env
        self.geometry = NotchGeometry(screen: NotchGeometry.preferredScreen())
        self.panel = NotchPanel(contentRect: geometry.collapsedFrame)

        let root = env.inject(NotchRootView())
        let hosting = NSHostingView(rootView: AnyView(root))
        hosting.autoresizingMask = [.width, .height]
        panel.contentView = hosting

        pushSizes()
        panel.setFrame(geometry.collapsedFrame, display: true)
        panel.orderFrontRegardless()

        observe()
    }

    /// Publishes the current pill/panel sizes to the view-model that drives layout.
    private func pushSizes() {
        env.notch.collapsedSize = geometry.collapsedSize
        env.notch.expandedSize = geometry.expandedSize
    }

    private func observe() {
        // The panel resizes as a container; the *reveal* itself is animated purely in
        // SwiftUI (one spring), so the window never animates its frame on a different curve.
        env.notch.$isExpanded
            .removeDuplicates()
            .receive(on: RunLoop.main)
            .sink { [weak self] expanded in
                self?.handleExpansion(expanded)
            }
            .store(in: &cancellables)

        // Recompute geometry when displays change (resolution, notch screen, docking).
        NotificationCenter.default.publisher(for: NSApplication.didChangeScreenParametersNotification)
            .debounce(for: .milliseconds(200), scheduler: RunLoop.main)
            .sink { [weak self] _ in
                self?.recomputeGeometry()
            }
            .store(in: &cancellables)

        // Menu / status-bar toggle.
        NotificationCenter.default.publisher(for: .notchToggle)
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in
                self?.env.notch.toggle()
            }
            .store(in: &cancellables)
    }

    private func handleExpansion(_ expanded: Bool) {
        shrinkWorkItem?.cancel()
        if expanded {
            // Grow the container instantly (no visible window animation) so the
            // SwiftUI shape has room to spring open into it.
            panel.setFrame(geometry.expandedFrame, display: true)
        } else {
            // Let the SwiftUI shape spring closed first, then shrink the container
            // back to the notch so clicks pass through the surrounding area again.
            let work = DispatchWorkItem { [weak self] in
                guard let self, !self.env.notch.isExpanded else { return }
                self.panel.setFrame(self.geometry.collapsedFrame, display: true)
            }
            shrinkWorkItem = work
            DispatchQueue.main.asyncAfter(deadline: .now() + collapseSettle, execute: work)
        }
    }

    private func recomputeGeometry() {
        geometry = NotchGeometry(screen: NotchGeometry.preferredScreen())
        pushSizes()
        shrinkWorkItem?.cancel()
        let frame = env.notch.isExpanded ? geometry.expandedFrame : geometry.collapsedFrame
        panel.setFrame(frame, display: true)
    }
}
