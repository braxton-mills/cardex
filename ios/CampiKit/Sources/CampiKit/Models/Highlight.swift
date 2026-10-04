import Foundation

/// One highlight (api-contract §5.7, rules §6.3).
public struct Highlight: Codable, Hashable, Sendable, Identifiable {
    public enum Kind: String, TolerantEnum {
        case newCatch = "new_catch", rare, busiest, daily, starred, unknown
    }

    public struct Window: Codable, Hashable, Sendable {
        public var start: PCDate
        public var end: PCDate
        public var sightingsCount: Int
    }

    /// What the highlight is about.
    public enum Content: Hashable, Sendable {
        case sighting(Sighting)
        case clip(Clip)
        case daily(Daily)
        /// A 10-minute window; its clip is `nil` once the file has expired (fall back to `/api/seek`).
        case window(Window, clip: Clip?)
        case unknown
    }

    public var id: String
    public var types: [Kind]
    public var at: PCDate
    public var day: PCDay
    public var sighting: Sighting?
    public var clip: Clip?
    public var daily: Daily?
    public var window: Window?

    public var content: Content {
        if let window { return .window(window, clip: clip) }
        if let sighting { return .sighting(sighting) }
        if let clip { return .clip(clip) }
        if let daily { return .daily(daily) }
        return .unknown
    }
}

/// A paged list (api-contract §2.5).
public struct Page<Item: Codable & Hashable & Sendable>: Codable, Hashable, Sendable {
    public var items: [Item]
    public var nextCursor: String?
    public var total: Int
}
