import Foundation

/// A 10-minute timelapse clip (api-contract §5.4).
public struct Clip: Codable, Hashable, Sendable, Identifiable {
    public struct Media: Codable, Hashable, Sendable {
        public var video: MediaPath
        public var poster: MediaPath
    }

    public var id: String
    public var day: PCDay
    public var windowStart: PCDate
    public var windowEnd: PCDate
    public var exactWindow: Bool
    public var duration: Double?
    public var sizeBytes: Int
    public var modifiedAt: PCDate
    /// Approximate: housekeeping runs hourly.
    public var expiresAfter: PCDate
    public var sightingsCount: Int?
    public var starred: Bool
    public var media: Media

    enum CodingKeys: String, CodingKey {
        case id, day, windowStart, windowEnd, exactWindow, sizeBytes, modifiedAt, expiresAfter, sightingsCount,
             starred, media
        case duration = "durationS"
    }
}

public struct ClipList: Codable, Hashable, Sendable {
    public var latestId: String?
    public var retentionHours: Int
    public var windowMin: Int
    public var items: [Clip]
}

/// A daily video (api-contract §5.5).
public struct Daily: Codable, Hashable, Sendable, Identifiable {
    public var day: PCDay
    public var duration: Double?
    public var sizeBytes: Int
    public var modifiedAt: PCDate
    public var sightingsCount: Int?
    public var starred: Bool
    public var media: Clip.Media

    public var id: String { day.rawValue }

    enum CodingKeys: String, CodingKey {
        case day, sizeBytes, modifiedAt, sightingsCount, starred, media
        case duration = "durationS"
    }
}

public struct ArchivePart: Codable, Hashable, Sendable, Identifiable {
    public struct Media: Codable, Hashable, Sendable {
        public var video: MediaPath?
    }

    public var part: Int
    public var sizeBytes: Int
    public var modifiedAt: PCDate
    public var duration: Double?
    /// The part being appended to; never playable.
    public var current: Bool
    public var media: Media

    public var id: Int { part }

    enum CodingKeys: String, CodingKey {
        case part, sizeBytes, modifiedAt, current, media
        case duration = "durationS"
    }
}

public struct ArchiveList: Codable, Hashable, Sendable {
    public var enabled: Bool
    public var items: [ArchivePart]
}

/// Where an instant is in the timelapse (api-contract §5.8, §6.4).
public struct Seek: Codable, Hashable, Sendable {
    public enum Target: String, TolerantEnum { case clip, daily, none, unknown }
    public enum Reason: String, TolerantEnum {
        case pendingRender = "pending_render", notRendered = "not_rendered", noFrames = "no_frames", expired, unknown
    }

    public var ts: PCDate
    public var target: Target
    public var clipId: String?
    public var day: PCDay?
    public var video: MediaPath?
    public var offset: Double?
    public var approximate: Bool
    public var reason: Reason?

    enum CodingKeys: String, CodingKey {
        case ts, target, clipId, day, video, approximate, reason
        case offset = "offsetS"
    }
}
