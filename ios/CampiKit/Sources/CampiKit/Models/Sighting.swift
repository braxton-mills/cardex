import Foundation

/// One vehicle pass (api-contract §5.2). `label`/`make`/`model` are effective (the user's correction applied).
public struct Sighting: Codable, Hashable, Sendable, Identifiable {
    public enum DecidedBy: String, TolerantEnum { case siglip, cloud, user, unknown }
    public enum Source: String, TolerantEnum { case siglip, cloud, unknown }
    public enum Direction: String, TolerantEnum { case leftToRight = "LR", rightToLeft = "RL", unknown }
    public enum CloudStatus: String, TolerantEnum { case pending, done, failed, capped, skipped, unknown }

    public struct MachineVerdict: Codable, Hashable, Sendable {
        public var label: String?
        public var make: String?
        public var model: String?
        public var confidence: Double?
        public var source: Source
    }

    public struct SiglipVerdict: Codable, Hashable, Sendable {
        public var label: String?
        public var confidence: Double?
    }

    public struct RunnerUp: Codable, Hashable, Sendable, Identifiable {
        public var label: String
        public var p: Double
        public var id: String { label }
    }

    public struct Correction: Codable, Hashable, Sendable {
        public var label: String
        public var at: PCDate
    }

    public struct Media: Codable, Hashable, Sendable {
        public var crop: MediaPath?
        public var frame: MediaPath?
        public var clip: MediaPath?
    }

    public var id: String
    public var kind: String
    public var startedAt: PCDate
    public var endedAt: PCDate
    public var day: PCDay
    public var yoloClass: String?
    public var label: String?
    public var make: String?
    public var model: String?
    public var confidence: Double?
    public var decidedBy: DecidedBy
    public var machine: MachineVerdict
    public var siglip: SiglipVerdict
    public var runnerUps: [RunnerUp]
    public var yearRange: String?
    public var color: String?
    public var unsure: Bool
    public var stationary: Bool
    public var direction: Direction?
    /// Reserved until the PC persists it; `nil` = never sent / unknown.
    public var cloudStatus: CloudStatus?
    public var starred: Bool
    public var hidden: Bool
    public var correction: Correction?
    public var trackFrames: Int?
    public var maxBoxPx: Int?
    public var media: Media

    /// The instant clients pass to `/api/seek` (api-contract §4.11).
    public var midpoint: Date {
        startedAt.date.addingTimeInterval(endedAt.date.timeIntervalSince(startedAt.date) / 2)
    }

    /// Shown instead of a label when there is none.
    public var displayLabel: String { label ?? yoloClass ?? kind }

    /// Gemini was asked and hasn't answered yet.
    public var isAwaitingCloud: Bool { cloudStatus == .pending }
}
