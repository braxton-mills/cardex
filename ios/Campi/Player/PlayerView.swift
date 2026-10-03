import AVKit
import CampiKit
import SwiftUI

/// Something to play: a URL and where to start.
struct VideoTarget: Identifiable, Hashable {
    let id = UUID()
    var url: URL
    var start: Double = 0
    var title: String
    /// The start position is an estimate (daily-video fallback).
    var approximate = false
    var note: String?
}

/// Full-screen player that seeks precisely to `target.start` once the item is ready.
struct PlayerView: View {
    let target: VideoTarget
    @Environment(\.dismiss) private var dismiss
    @State private var player: AVPlayer?
    @State private var failed: String?

    /// Start a little before the position, so the moment isn't missed.
    static let leadIn = 1.0

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ZStack {
                    Color.black
                    if let player {
                        PlayerController(player: player)
                    } else if let failed {
                        ContentUnavailableView("Can't play this video", systemImage: "exclamationmark.triangle",
                                               description: Text(failed))
                            .foregroundStyle(.white)
                    } else {
                        ProgressView().tint(.white)
                    }
                }
                if target.approximate || target.note != nil {
                    VStack(alignment: .leading, spacing: 4) {
                        if target.approximate {
                            Label("Approximate position: the 10-minute clip has expired, so this is the daily video.",
                                  systemImage: "scope")
                        }
                        if let note = target.note { Text(note) }
                    }
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding()
                }
            }
            .navigationTitle(target.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }.accessibilityIdentifier("player.done")
                }
            }
        }
        .task { await start() }
        .onDisappear { player?.pause() }
    }

    private func start() async {
        let item = AVPlayerItem(url: target.url)
        let p = AVPlayer(playerItem: item)
        p.isMuted = false
        player = p
        for await status in item.publisher(for: \.status).values {
            if status == .readyToPlay { break }
            if status == .failed {
                failed = item.error?.localizedDescription ?? "Unknown error"
                player = nil
                return
            }
        }
        let t = CMTime(seconds: max(0, target.start - (target.start > 0 ? Self.leadIn : 0)), preferredTimescale: 600)
        await p.seek(to: t, toleranceBefore: .zero, toleranceAfter: .zero)
        p.play()
    }
}

struct PlayerController: UIViewControllerRepresentable {
    let player: AVPlayer

    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let vc = AVPlayerViewController()
        vc.player = player
        vc.allowsPictureInPicturePlayback = false
        return vc
    }

    func updateUIViewController(_ vc: AVPlayerViewController, context: Context) {
        if vc.player !== player { vc.player = player }
    }
}

/// Inline player for short clips (a sighting's own clip); doesn't autoplay.
struct InlineClipPlayer: View {
    let url: URL
    @State private var player: AVPlayer?

    var body: some View {
        ZStack {
            Color.black
            if let player { VideoPlayer(player: player) }
        }
        .task(id: url) { player = AVPlayer(url: url) }
        .onDisappear { player?.pause() }
    }
}

/// Resolves "View in timelapse" for an instant via `/api/seek`.
@MainActor
enum Timelapse {
    enum Outcome {
        case play(VideoTarget)
        case message(String)
    }

    static func target(at date: Date, client: APIClient, timeZone: TimeZone) async throws -> Outcome {
        let seek = try await client.seek(to: date)
        guard let video = seek.video, let url = client.url(for: video), let offset = seek.offset else {
            return .message(seek.explanation ?? "No timelapse for that time.")
        }
        let title = switch seek.target {
        case .daily: "Daily video, \(seek.day?.text(timeZone: timeZone) ?? "")"
        default: "Timelapse around \(seek.ts.timeText)"
        }
        return .play(VideoTarget(url: url, start: offset, title: title, approximate: seek.approximate,
                                 note: "Captures are 2 s apart, so a fast car may fall between frames."))
    }
}

extension View {
    /// Presents the player full screen for a non-nil target.
    func videoPlayer(_ target: Binding<VideoTarget?>) -> some View {
        fullScreenCover(item: target) { PlayerView(target: $0) }
    }
}
