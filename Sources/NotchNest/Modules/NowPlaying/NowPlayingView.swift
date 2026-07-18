import SwiftUI

struct NowPlayingView: View {
    @EnvironmentObject var manager: NowPlayingManager
    @EnvironmentObject var spotify: SpotifyService
    /// Non-nil while the user drags the seek slider — freezes the thumb.
    @State private var dragPosition: Double?

    var body: some View {
        content
            .onChange(of: manager.info?.trackID) { _, newID in
                if let newID { Task { await spotify.refreshLikeStatus(for: newID) } }
            }
            .onChange(of: spotify.connection) { _, state in
                if state == .connected, let id = manager.info?.trackID {
                    Task { await spotify.refreshLikeStatus(for: id) }
                }
            }
            .onAppear {
                if let id = manager.info?.trackID {
                    Task { await spotify.refreshLikeStatus(for: id) }
                }
            }
    }

    @ViewBuilder
    private var content: some View {
        if let info = manager.info {
            HStack(spacing: 14) {
                artwork
                VStack(alignment: .leading, spacing: 4) {
                    Text(info.title)
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(Theme.primaryText)
                        .lineLimit(1)
                    Text(info.artist.isEmpty ? info.app.rawValue : info.artist)
                        .font(.system(size: 12))
                        .foregroundStyle(Theme.secondaryText)
                        .lineLimit(1)
                    Spacer(minLength: 0)
                    if info.duration > 1 {
                        seekBar(info)
                    }
                    HStack(spacing: 10) {
                        GlassIconButton(systemName: "backward.fill", size: 32, symbolSize: 13) {
                            manager.previous()
                        }
                        GlassIconButton(systemName: info.isPlaying ? "pause.fill" : "play.fill",
                                        size: 38, symbolSize: 15, tint: Theme.accent) {
                            manager.playPause()
                        }
                        GlassIconButton(systemName: "forward.fill", size: 32, symbolSize: 13) {
                            manager.next()
                        }
                        Spacer()
                        if info.app == .spotify {
                            likeButton(info)
                        }
                        Label(info.app.rawValue, systemImage: "hifispeaker.fill")
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(Theme.tertiaryText)
                            .labelStyle(.titleAndIcon)
                    }
                    // Never fail silently: surface like/API problems right here.
                    if let error = spotify.lastError {
                        Text(error)
                            .font(.system(size: 9))
                            .foregroundStyle(Theme.tertiaryText)
                            .lineLimit(2)
                    }
                }
            }
        } else {
            emptyState
        }
    }

    /// Scrub bar with live position. Polls arrive every 2s; TimelineView ticks
    /// the position forward locally between polls so motion looks continuous.
    private func seekBar(_ info: NowPlayingInfo) -> some View {
        TimelineView(.periodic(from: .now, by: 0.5)) { context in
            let position = dragPosition ?? livePosition(info, at: context.date)
            VStack(spacing: 2) {
                Slider(
                    value: Binding(
                        get: { position },
                        set: { dragPosition = $0 }
                    ),
                    in: 0...max(info.duration, 1),
                    onEditingChanged: { editing in
                        if !editing, let target = dragPosition {
                            manager.seek(to: target)
                            dragPosition = nil
                        }
                    }
                )
                .tint(Theme.accent)
                .controlSize(.mini)
                HStack {
                    Text(timeString(position))
                    Spacer()
                    Text(timeString(info.duration))
                }
                .font(.system(size: 9).monospacedDigit())
                .foregroundStyle(Theme.tertiaryText)
            }
        }
    }

    private func livePosition(_ info: NowPlayingInfo, at date: Date) -> Double {
        guard info.isPlaying else { return info.position }
        return min(info.duration,
                   info.position + date.timeIntervalSince(manager.lastPollDate))
    }

    private func timeString(_ seconds: Double) -> String {
        let total = Int(seconds.rounded())
        return String(format: "%d:%02d", total / 60, total % 60)
    }

    /// Heart = saved to Spotify Liked Songs. Works with or without the Web API:
    /// SpotifyService routes to the API when usable, else drives the app directly.
    private func likeButton(_ info: NowPlayingInfo) -> some View {
        let key = info.trackID ?? "local:\(info.title)|\(info.artist)"
        let liked = spotify.likedCache[key]
        return GlassIconButton(
            systemName: liked == true ? "heart.fill" : "heart",
            size: 32, symbolSize: 13,
            tint: liked == true ? Color(red: 0.98, green: 0.33, blue: 0.42)
                                : Theme.secondaryText
        ) {
            Task { await spotify.toggleLike(for: key) }
        }
        .help(liked == true ? "Remove from Liked Songs" : "Add to Liked Songs")
    }

    private var artwork: some View {
        let size = artworkSize
        return ZStack {
            RoundedRectangle(cornerRadius: 12)
                .fill(Theme.controlBackground)
            if let art = manager.artwork {
                Image(nsImage: art)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(width: size.width, height: size.height)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            } else {
                Image(systemName: "music.note")
                    .font(.system(size: 26))
                    .foregroundStyle(Theme.secondaryText)
            }
        }
        .frame(width: size.width, height: size.height)
        .animation(Theme.contentAnimation, value: size)
    }

    /// Fixed height, adaptive width: the frame follows the image's aspect
    /// ratio (clamped), so wide video thumbnails get a wide frame instead of
    /// being cropped square — and the HStack keeps it clear of the text.
    private var artworkSize: CGSize {
        let height: CGFloat = 100
        guard let art = manager.artwork, art.size.width > 0, art.size.height > 0 else {
            return CGSize(width: height, height: height)
        }
        let ratio = art.size.width / art.size.height
        return CGSize(width: min(170, max(64, height * ratio)), height: height)
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "music.note")
                .font(.system(size: 28))
                .foregroundStyle(Theme.tertiaryText)
            Text("Nothing playing")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(Theme.secondaryText)
            Text("Open Apple Music or Spotify")
                .font(.system(size: 11))
                .foregroundStyle(Theme.tertiaryText)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
