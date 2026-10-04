import Foundation

public enum Tier: String, TolerantEnum, Comparable {
    case uncaught, rare, uncommon, common, unknown

    private var order: Int { [.uncaught: 0, .rare: 1, .uncommon: 2, .common: 3][self] ?? 4 }
    public static func < (a: Tier, b: Tier) -> Bool { a.order < b.order }
}

/// `GET /api/collection` (api-contract §5.6).
public struct LabelCollection: Codable, Hashable, Sendable {
    public struct TierRange: Codable, Hashable, Sendable {
        public var tier: Tier
        public var min: Int
        public var max: Int?
    }

    public var total: Int
    public var caught: Int
    public var tiers: [TierRange]
    public var items: [CollectionItem]

    public var progress: Double { total == 0 ? 0 : Double(caught) / Double(total) }
}

public struct CollectionItem: Codable, Hashable, Sendable, Identifiable {
    public enum Origin: String, TolerantEnum {
        case labelsFile = "labels_file", discovered, other, unknown
    }

    public struct Cover: Codable, Hashable, Sendable {
        public var sightingId: String
        public var crop: MediaPath
    }

    public var label: String
    public var make: String?
    public var model: String?
    public var generic: Bool
    public var origin: Origin
    public var count: Int
    public var firstSeenAt: PCDate?
    public var lastSeenAt: PCDate?
    public var tier: Tier
    public var cover: Cover?

    public var id: String { label }
    public var isCaught: Bool { count > 0 }
}
