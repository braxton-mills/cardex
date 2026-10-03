import AVKit
import CampiKit
import OSLog
import SwiftUI

let playerLog = Logger(subsystem: "com.braxtonmills.campi", category: "player")

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
        // Loading the asset surfaces errors the item's status doesn't: on iOS a bad range answer leaves the item
        // .unknown forever while AVKit shows a crossed-out play button.
        do {
            guard try await item.asset.load(.isPlayable) else {
                show(await Self.diagnose(target.url))
                return
            }
        } catch {
            playerLog.error("asset failed: \(String(describing: error), privacy: .public)")
            show(Self.describe(error))
            return
        }
        for await status in item.publisher(for: \.status).values {
            playerLog.notice("waiting: item status \(status.rawValue)")
            if status == .readyToPlay { break }
            if status == .failed {
                show(Self.describe(item.error))
                return
            }
        }
        let t = CMTime(seconds: max(0, target.start - (target.start > 0 ? Self.leadIn : 0)), preferredTimescale: 600)
        await p.seek(to: t, toleranceBefore: .zero, toleranceAfter: .zero)
        p.play()
        // An item can also fail after it was ready (a later range request goes wrong); without this, AVKit only shows
        // a crossed-out play button. Ends when the player closes (the view's task is cancelled).
        if let message = await Self.firstFailure(of: item) { show(message) }
    }

    private func show(_ message: String) {
        player?.pause()
        failed = message
        player = nil
    }

    /// Waits until `item` fails (status or failed-to-play-to-end), returning why; `nil` if cancelled first.
    static func firstFailure(of item: AVPlayerItem) async -> String? {
        let (failures, sink) = AsyncStream.makeStream(of: String.self)
        let status = item.observe(\.status) { item, _ in
            playerLog.notice("item status \(item.status.rawValue) error \(String(describing: item.error), privacy: .public)")
            if item.status == .failed { sink.yield(describe(item.error)) }
        }
        let errors = NotificationCenter.default.addObserver(forName: AVPlayerItem.newErrorLogEntryNotification,
                                                            object: item, queue: .main) { note in
            let e = (note.object as? AVPlayerItem)?.errorLog()?.events.last
            playerLog.notice("error log \(e?.errorStatusCode ?? 0) \(e?.errorDomain ?? "", privacy: .public) \(e?.errorComment ?? "", privacy: .public)")
        }
        let ended = NotificationCenter.default.addObserver(forName: AVPlayerItem.failedToPlayToEndTimeNotification,
                                                           object: item, queue: .main) { note in
            sink.yield(describe(note.userInfo?[AVPlayerItemFailedToPlayToEndTimeErrorKey] as? any Error))
        }
        defer {
            status.invalidate()
            NotificationCenter.default.removeObserver(ended)
            NotificationCenter.default.removeObserver(errors)
        }
        for await message in failures { return message }   // the stream ends when the task is cancelled
        return nil
    }

    /// Why an unplayable video failed. AVPlayer needs exact byte ranges (api-contract §7.1), so ask for the whole file
    /// as a range and compare the answer's Content-Range (headers only; the body isn't read).
    static func diagnose(_ url: URL) async -> String {
        var req = URLRequest(url: url, timeoutInterval: 15)
        req.setValue("bytes=0-", forHTTPHeaderField: "Range")
        if let (_, response) = try? await URLSession.shared.bytes(for: req),
           let http = response as? HTTPURLResponse, http.statusCode == 206,
           let range = http.value(forHTTPHeaderField: "Content-Range"),
           let (end, total) = Self.rangeEnd(range), end + 1 < total {
            return describe(NSError(domain: "CoreMediaErrorDomain", code: -12939))
        }
        return "The video didn't load. Try again, or check that the PC is reachable."
    }

    /// `bytes a-b/total` → (b, total).
    nonisolated static func rangeEnd(_ contentRange: String) -> (Int, Int)? {
        let parts = contentRange.replacingOccurrences(of: "bytes ", with: "").split(separator: "/")
        guard parts.count == 2, let total = Int(parts[1]),
              let end = parts[0].split(separator: "-").last.flatMap({ Int($0) }) else { return nil }
        return (end, total)
    }

    /// A readable reason. A wrong byte-range answer (CoreMedia -12939) is a server problem worth naming.
    nonisolated static func describe(_ error: (any Error)?) -> String {
        guard let error else { return "The video stopped loading." }
        var e: NSError? = error as NSError
        while let current = e {
            if current.domain == "CoreMediaErrorDomain" && current.code == -12939 {
                return "The PC sent the wrong part of the video file (an HTTP byte range shorter than asked for), so it can't be streamed. The PC needs a fix: api-contract §7.1."
            }
            e = current.userInfo[NSUnderlyingErrorKey] as? NSError
        }
        return error.localizedDescription
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
