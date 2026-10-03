import CampiKit
import Foundation
import Observation
import UIKit

/// Drives Live (api-contract §8): MJPEG video or polled snapshots, and the states the PC can answer with.
@MainActor @Observable
final class LiveModel {
    enum Phase: Equatable {
        case connecting
        case showing
        /// 503 `live_busy`: `max_viewers` already watching.
        case busy(maxViewers: Int)
        /// 502 `pi_unreachable`, or `status.live.available == false`.
        case piUnreachable
        /// 404 from `/live.jpg`: no frame saved in the last 10 minutes.
        case noRecentFrame
        case offline(String)
    }

    /// Frames per second asked of `/live.mjpg`. Full 1920×1440 frames at 30 fps would be ~70 Mbit/s.
    static let wifiMaxFPS = 10
    static let snapshotInterval: Duration = .seconds(2)
    static let snapshotWidth = 1080

    private(set) var phase: Phase = .connecting
    private(set) var image: UIImage?
    /// Mode of the frame on screen.
    private(set) var imageMode: LiveMode?
    /// `X-Frame-Time` of the snapshot on screen.
    private(set) var frameTime: PCDate?
    /// Video frames shown in the last second.
    private(set) var fps = 0
    /// The user's pick in the mode control; `nil` = automatic (by network).
    var override: LiveMode?

    private var frameTimes: [Date] = []

    init() {
        #if DEBUG
        if let forced = UserDefaults.standard.string(forKey: "liveMode").flatMap(LiveMode.init(rawValue:)) {
            override = forced   // UI tests: -liveMode snapshots
        }
        #endif
    }

    /// Shows Live in `mode` until cancelled (the view's task ends when it disappears, the app leaves the
    /// foreground, or the mode changes). Cancelling closes the MJPEG connection and with it the PC's upstream.
    func run(mode: LiveMode, client: APIClient, app: AppModel, maxPixelSize: Int) async {
        if image == nil || imageMode != mode { phase = .connecting }
        let live: LiveStatus
        do {
            live = try await client.status().live   // fresh signed URLs and rotation
        } catch {
            fail(error, app: app)
            return
        }
        switch mode {
        case .video: await streamVideo(live, client: client, app: app, maxPixelSize: maxPixelSize)
        case .snapshots: await pollSnapshots(live, client: client, app: app, maxPixelSize: maxPixelSize)
        }
    }

    private func streamVideo(_ live: LiveStatus, client: APIClient, app: AppModel, maxPixelSize: Int) async {
        guard live.available, let path = live.mjpeg else {
            phase = .piUnreachable
            return
        }
        let orientation = Self.orientation(rotation: live.rotation)
        frameTimes = []
        do {
            for try await jpeg in client.liveFrames(path, maxFPS: Self.wifiMaxFPS) {
                guard let img = await Self.decode(jpeg, maxPixelSize: maxPixelSize) else { continue }
                show(UIImage(cgImage: img, scale: 1, orientation: orientation), mode: .video, frameTime: nil)
                countFrame()
            }
            if !Task.isCancelled { phase = .offline("The PC ended the stream.") }
        } catch {
            if !Task.isCancelled { fail(error, app: app, maxViewers: live.maxViewers) }
        }
    }

    private func pollSnapshots(_ live: LiveStatus, client: APIClient, app: AppModel, maxPixelSize: Int) async {
        guard let path = live.snapshot else {
            phase = .piUnreachable
            return
        }
        fps = 0
        while !Task.isCancelled {
            do {
                let snap = try await client.liveSnapshot(path, width: Self.snapshotWidth)
                if let img = await Self.decode(snap.data, maxPixelSize: maxPixelSize) {
                    show(UIImage(cgImage: img), mode: .snapshots, frameTime: snap.frameTime)   // already rotated
                }
            } catch let e as APIError where e.code == .rateLimited {
                // another device is polling too: skip this tick
            } catch let e as APIError where e.code == .notFound {
                phase = .noRecentFrame
            } catch {
                if Task.isCancelled { return }
                fail(error, app: app)
                if (error as? APIError)?.needsPairing == true { return }
            }
            try? await Task.sleep(for: Self.snapshotInterval)
        }
    }

    private func show(_ img: UIImage, mode: LiveMode, frameTime: PCDate?) {
        image = img
        imageMode = mode
        self.frameTime = frameTime
        phase = .showing
    }

    private func countFrame() {
        let now = Date()
        frameTimes.append(now)
        frameTimes.removeAll { now.timeIntervalSince($0) > 1 }
        fps = frameTimes.count
    }

    private func fail(_ error: any Error, app: AppModel, maxViewers: Int = 3) {
        app.report(error)
        let e = error as? APIError ?? .unreachable(error.localizedDescription)
        phase = switch e.code {
        case .liveBusy: .busy(maxViewers: maxViewers)
        case .piUnreachable: .piUnreachable
        default: .offline(e.isUnreachable ? "Can't reach the PC. Is Tailscale connected?" : (e.errorDescription ?? "Live failed."))
        }
    }

    /// `[image] rotation` is clockwise degrees; `/live.mjpg` frames aren't rotated, so the display does it.
    static func orientation(rotation: Int) -> UIImage.Orientation {
        switch ((rotation % 360) + 360) % 360 {
        case 90: .right
        case 180: .down
        case 270: .left
        default: .up
        }
    }

    nonisolated static func decode(_ data: Data, maxPixelSize: Int) async -> CGImage? {
        await Task.detached(priority: .userInitiated) {
            ImageLoader.downsample(data, maxPixelSize: maxPixelSize)
        }.value
    }
}
