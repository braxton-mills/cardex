import Foundation

/// Filters for `GET /api/sightings` (api-contract §4.4).
public struct SightingQuery: Hashable, Sendable {
    public var from: PCDay?
    public var to: PCDay?
    public var classes: [String] = []
    public var makes: [String] = []
    public var label: String?
    public var decidedBy: [Sighting.DecidedBy] = []
    public var starredOnly = false
    public var hideUnsure = false
    public var hideStationary = false
    public var includeHidden = false
    public var limit: Int?
    public var cursor: String?

    public init(from: PCDay? = nil, to: PCDay? = nil, limit: Int? = nil) {
        self.from = from
        self.to = to
        self.limit = limit
    }

    var queryItems: [URLQueryItem] {
        var q: [URLQueryItem] = []
        if let from { q.append(.init(name: "from", value: from.rawValue)) }
        if let to { q.append(.init(name: "to", value: to.rawValue)) }
        q += classes.map { .init(name: "class", value: $0) }
        q += makes.map { .init(name: "make", value: $0) }
        if let label { q.append(.init(name: "label", value: label)) }
        q += decidedBy.map { .init(name: "decided_by", value: $0.rawValue) }
        if starredOnly { q.append(.init(name: "starred", value: "true")) }
        if hideUnsure { q.append(.init(name: "hide_unsure", value: "true")) }
        if hideStationary { q.append(.init(name: "hide_stationary", value: "true")) }
        if includeHidden { q.append(.init(name: "include_hidden", value: "true")) }
        if let limit { q.append(.init(name: "limit", value: String(limit))) }
        if let cursor { q.append(.init(name: "cursor", value: cursor)) }
        return q
    }
}

/// Filters for `GET /api/highlights` (api-contract §4.7).
public struct HighlightQuery: Hashable, Sendable {
    public var types: [Highlight.Kind] = []
    public var from: PCDay?
    public var to: PCDay?
    public var limit: Int?
    public var cursor: String?

    public init(types: [Highlight.Kind] = [], from: PCDay? = nil, to: PCDay? = nil, limit: Int? = nil) {
        self.types = types
        self.from = from
        self.to = to
        self.limit = limit
    }

    var queryItems: [URLQueryItem] {
        var q = types.map { URLQueryItem(name: "type", value: $0.rawValue) }
        if let from { q.append(.init(name: "from", value: from.rawValue)) }
        if let to { q.append(.init(name: "to", value: to.rawValue)) }
        if let limit { q.append(.init(name: "limit", value: String(limit))) }
        if let cursor { q.append(.init(name: "cursor", value: cursor)) }
        return q
    }
}
