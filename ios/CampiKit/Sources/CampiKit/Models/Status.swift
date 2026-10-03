import Foundation

/// `GET /api/status` (api-contract §5.1).
public struct Status: Codable, Hashable, Sendable {
    public var apiVersion: Int
    public var serverName: String
    public var serverTime: PCDate
    public var service: ServiceStatus
    public var capture: CaptureStatus
    public var clips: ClipsStatus
    public var daily: DailyStatus
    public var sightings: SightingsStatus
    /// Reserved: `nil` until the PC persists gaming state.
    public var gaming: GamingStatus?
    public var disk: Disk
    public var latestClipId: String?
    public var live: LiveStatus

    /// The PC's "today".
    public var today: PCDay { serverTime.day }
}

public struct ServiceStatus: Codable, Hashable, Sendable {
    public enum State: String, TolerantEnum { case running, stopped, unknown }
    public var state: State
    public var pid: Int?
    public var startedAt: PCDate?
    public var heartbeatAt: PCDate?
    public var disabled: Bool
}

public struct CaptureStatus: Codable, Hashable, Sendable {
    public var connected: Bool
    public var host: String?
    public var lastFrameAt: PCDate?
    public var saved: Int
    public var rejected: Int
    public var reconnects: Int
    public var restarts: Int?
    public var lastError: String?
}

public struct RenderResult: Codable, Hashable, Sendable {
    public enum Outcome: String, TolerantEnum { case ok, skipped, error, unknown }
    public var status: Outcome
    public var finishedAt: PCDate?
    public var clipId: String?
    public var day: PCDay?
    public var detail: String?
}

public struct RenderQueue: Codable, Hashable, Sendable {
    public enum Reason: String, TolerantEnum { case gaming, unknown }
    public var length: Int
    public var oldestWindowStart: PCDate?
    public var deferred: Bool
    public var deferredReason: Reason?
}

public struct ClipsStatus: Codable, Hashable, Sendable {
    public var last: RenderResult?
    public var nextAt: PCDate?
    public var running: Bool?
    /// Reserved (render queue from pc-sightings-spec).
    public var queue: RenderQueue?
}

public struct DailyStatus: Codable, Hashable, Sendable {
    public var last: RenderResult?
    public var running: Bool?
}

public struct SightingsStatus: Codable, Hashable, Sendable {
    public enum State: String, TolerantEnum {
        case disabled, notInstalled = "not_installed", serviceStopped = "service_stopped",
             notRunning = "not_running", starting, running, crashLooping = "crash_looping",
             pausedGaming = "paused_gaming", unknown
    }
    public enum Phase: String, TolerantEnum {
        case loading, disconnected, running, pausedDark = "paused_dark", error, unknown
    }
    public enum Backend: String, TolerantEnum { case openvino, cuda, unknown }

    public var enabled: Bool
    public var state: State
    public var phase: Phase?
    public var restarts: Int?
    public var lastError: String?
    public var nextRetryAt: PCDate?
    public var hasHistory: Bool
    public var today: Int
    public var total: Int
    public var lastSightingAt: PCDate?
    // Reserved until the sightings spec lands:
    public var backend: Backend?
    public var device: String?
    public var cpuFallback: Bool?
    public var classifyQueue: Int?
    public var cloud: CloudUsage?
}

public struct CloudUsage: Codable, Hashable, Sendable {
    public var enabled: Bool
    public var callsToday: Int
    public var cap: Int
}

public struct GamingStatus: Codable, Hashable, Sendable {
    public enum Mode: String, TolerantEnum { case auto, on, off, unknown }
    public enum Detection: String, TolerantEnum { case counters, pathOnly = "path_only", unknown }
    public var mode: Mode
    public var active: Bool
    public var exe: String?
    public var since: PCDate?
    public var detection: Detection
    public var rendersDeferred: Bool
}

public struct Disk: Codable, Hashable, Sendable {
    public var freeGB: Double
    public var drive: String

    enum CodingKeys: String, CodingKey {
        case freeGB = "freeGb"
        case drive
    }
}

public struct LiveStatus: Codable, Hashable, Sendable {
    public var available: Bool
    public var mjpeg: MediaPath?
    public var snapshot: MediaPath?
    public var rotation: Int
    public var maxViewers: Int
}

// MARK: - Health summary

public struct HealthReport: Hashable, Sendable {
    public enum Level: Int, Comparable, Sendable {
        case ok, warning, error
        public static func < (a: Level, b: Level) -> Bool { a.rawValue < b.rawValue }
    }

    public struct Issue: Hashable, Sendable {
        public var level: Level
        public var text: String
    }

    public var issues: [Issue]

    public var level: Level { issues.map(\.level).max() ?? .ok }
    public var headline: String { issues.sorted { $0.level > $1.level }.first?.text ?? "All systems normal" }
}

extension Status {
    /// Frames older than this while "connected" count as a stalled capture.
    public static let staleFrameSeconds: TimeInterval = 120
    public static let lowDiskGB: Double = 50

    /// What's wrong, most severe first. `now` is injectable for tests.
    public func health(now: Date = Date()) -> HealthReport {
        var issues: [HealthReport.Issue] = []
        func add(_ level: HealthReport.Level, _ text: String) { issues.append(.init(level: level, text: text)) }

        if service.state != .running {
            add(.error, service.disabled ? "Campi is stopped (campi stop)" : "Campi service is not running")
        } else if !capture.connected {
            add(.error, "Camera stream disconnected")
        } else if let last = capture.lastFrameAt, now.timeIntervalSince(last.date) > Self.staleFrameSeconds {
            add(.error, "No frame saved for \(Int(now.timeIntervalSince(last.date) / 60)) min")
        }
        switch sightings.state {
        case .crashLooping: add(.warning, "Sightings worker is crash-looping")
        case .notInstalled where sightings.enabled: add(.warning, "Sightings worker is not installed")
        default: break
        }
        if sightings.cpuFallback == true { add(.warning, "Sightings running on CPU fallback") }
        if clips.last?.status == .error { add(.warning, "Last clip render failed") }
        if daily.last?.status == .error { add(.warning, "Last daily render failed") }
        if disk.freeGB < Self.lowDiskGB { add(.warning, String(format: "Low disk: %.0f GB free", disk.freeGB)) }
        return HealthReport(issues: issues)
    }
}
