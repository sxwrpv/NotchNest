import SwiftUI

struct PomodoroView: View {
    @EnvironmentObject var manager: PomodoroManager

    var body: some View {
        HStack(spacing: 20) {
            ZStack {
                Circle()
                    .stroke(Theme.controlBackground, lineWidth: 8)
                Circle()
                    .trim(from: 0, to: manager.progress)
                    .stroke(phaseColor, style: StrokeStyle(lineWidth: 8, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                    .animation(.linear(duration: 0.25), value: manager.progress)
                VStack(spacing: 2) {
                    Text(manager.remaining.clockString)
                        .font(.system(size: 22, weight: .bold, design: .rounded))
                        .foregroundStyle(Theme.primaryText)
                        .monospacedDigit()
                    Text(manager.phase.title)
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(Theme.secondaryText)
                }
            }
            .frame(width: 118, height: 118)

            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 6) {
                    ForEach(0..<4, id: \.self) { i in
                        Circle()
                            .fill(i < (manager.completedFocusSessions % 4) ? Theme.warn : Theme.controlBackground)
                            .frame(width: 8, height: 8)
                    }
                    Text("\(manager.completedFocusSessions) done")
                        .font(.system(size: 10))
                        .foregroundStyle(Theme.tertiaryText)
                        .padding(.leading, 4)
                }

                HStack(spacing: 10) {
                    GlassIconButton(systemName: "arrow.counterclockwise", size: 34, symbolSize: 13,
                                    tint: Theme.secondaryText) {
                        manager.reset()
                    }
                    GlassIconButton(systemName: manager.isRunning ? "pause.fill" : "play.fill",
                                    size: 44, symbolSize: 17, tint: phaseColor) {
                        manager.toggle()
                    }
                    GlassIconButton(systemName: "forward.end.fill", size: 34, symbolSize: 13,
                                    tint: Theme.secondaryText) {
                        manager.skip()
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var phaseColor: Color {
        manager.phase == .work ? Theme.warn : Theme.good
    }
}
