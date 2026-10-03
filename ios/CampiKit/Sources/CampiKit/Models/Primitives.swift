import Foundation

/// An enum decoded from a contract string, which maps values it doesn't know to `.unknown`
/// (api-contract §2.6: new enum values are an additive change).
public protocol TolerantEnum: RawRepresentable, Codable, Hashable, Sendable where RawValue == String {
    static var unknown: Self { get }
}

extension TolerantEnum {
    public init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = Self(rawValue: raw) ?? .unknown
    }
}

/// An instant as the PC sends it: RFC 3339 with the PC's local UTC offset at that instant (api-contract §2.2).
/// Keeps the offset so times can be shown in the PC's wall-clock time, matching the video overlays.
public struct PCDate: Codable, Hashable, Comparable, Sendable, CustomStringConvertible {
    public let date: Date
    public let utcOffsetSeconds: Int

    public init(date: Date, utcOffsetSeconds: Int) {
        self.date = date
        self.utcOffsetSeconds = utcOffsetSeconds
    }

    /// Parses `2026-10-03T14:05:12.345-05:00` (fractional seconds optional, `Z` accepted).
    public init?(rfc3339 s: String) {
        let fractional = Date.ISO8601FormatStyle(includingFractionalSeconds: true)
        let plain = Date.ISO8601FormatStyle()
        guard let date = (try? fractional.parse(s)) ?? (try? plain.parse(s)) else { return nil }
        let offset: Int
        if s.hasSuffix("Z") || s.hasSuffix("z") {
            offset = 0
        } else {
            let tail = s.suffix(6)   // ±HH:MM
            guard tail.count == 6, let sign = tail.first, sign == "+" || sign == "-",
                  let h = Int(tail.dropFirst().prefix(2)), let m = Int(tail.suffix(2)) else { return nil }
            offset = (sign == "-" ? -1 : 1) * (h * 3600 + m * 60)
        }
        self.init(date: date, utcOffsetSeconds: offset)
    }

    /// The PC's time zone at this instant (a fixed offset).
    public var timeZone: TimeZone { TimeZone(secondsFromGMT: utcOffsetSeconds) ?? .gmt }

    public var rfc3339: String {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        let c = cal.dateComponents([.year, .month, .day, .hour, .minute, .second, .nanosecond], from: date)
        let ms = Int((Double(c.nanosecond ?? 0) / 1_000_000).rounded(.down))
        let off = abs(utcOffsetSeconds)
        return String(format: "%04d-%02d-%02dT%02d:%02d:%02d.%03d%@%02d:%02d",
                      c.year!, c.month!, c.day!, c.hour!, c.minute!, c.second!, ms,
                      utcOffsetSeconds < 0 ? "-" : "+", off / 3600, (off % 3600) / 60)
    }

    public var description: String { rfc3339 }

    /// The PC-local calendar day of this instant.
    public var day: PCDay { PCDay(date: date, timeZone: timeZone) }

    public static func < (a: PCDate, b: PCDate) -> Bool { a.date < b.date }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.singleValueContainer()
        let s = try c.decode(String.self)
        guard let v = PCDate(rfc3339: s) else {
            throw DecodingError.dataCorruptedError(in: c, debugDescription: "not RFC 3339: \(s)")
        }
        self = v
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(rfc3339)
    }
}

/// A PC-local calendar day, `YYYY-MM-DD` (api-contract §2.2).
public struct PCDay: Codable, Hashable, Comparable, Sendable, CustomStringConvertible {
    public let rawValue: String

    public init?(_ s: String) {
        let parts = s.split(separator: "-")
        guard s.count == 10, parts.count == 3, parts.allSatisfy({ $0.allSatisfy(\.isNumber) }) else { return nil }
        rawValue = s
    }

    public init(date: Date, timeZone: TimeZone) {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        let c = cal.dateComponents([.year, .month, .day], from: date)
        rawValue = String(format: "%04d-%02d-%02d", c.year!, c.month!, c.day!)
    }

    public var description: String { rawValue }

    /// Noon of this day in `timeZone`, for formatting the day itself ("Fri, Oct 3").
    public func noon(in timeZone: TimeZone) -> Date {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = timeZone
        let p = rawValue.split(separator: "-").compactMap { Int($0) }
        return cal.date(from: DateComponents(year: p[0], month: p[1], day: p[2], hour: 12)) ?? .distantPast
    }

    public static func < (a: PCDay, b: PCDay) -> Bool { a.rawValue < b.rawValue }

    public init(from decoder: any Decoder) throws {
        let c = try decoder.singleValueContainer()
        let s = try c.decode(String.self)
        guard let v = PCDay(s) else {
            throw DecodingError.dataCorruptedError(in: c, debugDescription: "not YYYY-MM-DD: \(s)")
        }
        self = v
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(rawValue)
    }
}

/// A path-absolute URL from the API (`/media/...`, `/live...`), usually signed (api-contract §3.3).
/// Resolve against the base URL; never build one by hand.
public struct MediaPath: Codable, Hashable, Sendable, CustomStringConvertible {
    public let rawValue: String

    public init(_ rawValue: String) { self.rawValue = rawValue }

    public init(from decoder: any Decoder) throws {
        rawValue = try decoder.singleValueContainer().decode(String.self)
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(rawValue)
    }

    public var description: String { rawValue }

    public func url(relativeTo base: URL) -> URL? {
        URL(string: rawValue, relativeTo: base)?.absoluteURL
    }

    /// Adds a documented query parameter (`max_fps`, `w`); the signature covers the path only.
    public func adding(_ name: String, _ value: String) -> MediaPath {
        MediaPath(rawValue + (rawValue.contains("?") ? "&" : "?") + "\(name)=\(value)")
    }

    /// Cache key: the path plus any query items other than the signature (`d`, `exp`, `sig`).
    public var cacheKey: String {
        guard var c = URLComponents(string: rawValue) else { return rawValue }
        let kept = (c.queryItems ?? []).filter { !["d", "exp", "sig"].contains($0.name) }
        c.queryItems = kept.isEmpty ? nil : kept
        return c.string ?? rawValue
    }
}

enum JSON {
    static func decoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }

    static func encoder() -> JSONEncoder {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        e.outputFormatting = [.sortedKeys]
        return e
    }
}

extension JSONDecoder {
    /// The decoder configuration the API's JSON needs (snake_case keys).
    public static var campi: JSONDecoder { JSON.decoder() }
}
