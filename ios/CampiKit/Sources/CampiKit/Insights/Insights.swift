import Foundation

/// One day of counted sightings (api-contract §6.1), boiled down to what the Insights charts need. Small enough to
/// cache per day, so a range is fetched once and only today (and days still waiting on the cloud) is refetched.
public struct DaySummary: Codable, Hashable, Sendable {
    public static let version = 1

    public var day: PCDay
    public var total: Int
    /// Counted sightings by the hour they started on the PC's clock, `[0]` = midnight–1 AM.
    public var hours: [Int]
    public var leftToRight: [Int]
    public var rightToLeft: [Int]
    /// Gemini's color names, lowercased.
    public var colors: [String: Int]
    public var makes: [String: Int]
    /// True when a sighting still waits on the cloud, whose verdict can change its label, make and color.
    public var pending: Bool

    public init(day: PCDay, sightings: [Sighting]) {
        self.day = day
        hours = Array(repeating: 0, count: 24)
        leftToRight = hours
        rightToLeft = hours
        colors = [:]
        makes = [:]
        pending = false
        total = 0
        var cal = Calendar(identifier: .gregorian)
        for s in sightings where s.counts && s.day == day {
            total += 1
            cal.timeZone = s.startedAt.timeZone
            let h = cal.component(.hour, from: s.startedAt.date)
            hours[h] += 1
            switch s.direction {
            case .leftToRight: leftToRight[h] += 1
            case .rightToLeft: rightToLeft[h] += 1
            default: break
            }
            if let c = s.color?.trimmingCharacters(in: .whitespaces).lowercased(), !c.isEmpty { colors[c, default: 0] += 1 }
            if let m = s.make, !m.isEmpty { makes[m, default: 0] += 1 }
        }
        pending = sightings.contains { $0.isAwaitingCloud }
    }

    /// 1 = Sunday … 7 = Saturday, like `Calendar.component(.weekday)`.
    public var weekday: Int { day.weekday }
}

extension Sighting {
    /// Whether this sighting counts (api-contract §6.1): not hidden, labeled, and sure unless the cloud or the user
    /// decided it.
    public var counts: Bool {
        !hidden && label != nil && (!unsure || decidedBy == .cloud || decidedBy == .user)
    }
}

extension PCDay {
    private static var gmtCalendar: Calendar {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = .gmt
        return cal
    }

    public func adding(days: Int) -> PCDay {
        let d = Self.gmtCalendar.date(byAdding: .day, value: days, to: noon(in: .gmt)) ?? noon(in: .gmt)
        return PCDay(date: d, timeZone: .gmt)
    }

    /// Days from `self` to `other` (positive when `other` is later).
    public func days(to other: PCDay) -> Int {
        Self.gmtCalendar.dateComponents([.day], from: noon(in: .gmt), to: other.noon(in: .gmt)).day ?? 0
    }

    /// 1 = Sunday … 7 = Saturday.
    public var weekday: Int { Self.gmtCalendar.component(.weekday, from: noon(in: .gmt)) }

    /// Every day from `from` through `to`, oldest first.
    public static func range(_ from: PCDay, through to: PCDay) -> [PCDay] {
        guard from <= to else { return [] }
        return (0...from.days(to: to)).map { from.adding(days: $0) }
    }
}

public struct DayCount: Hashable, Sendable {
    public var day: PCDay
    public var count: Int

    public init(day: PCDay, count: Int) {
        self.day = day
        self.count = count
    }
}

/// The Insights charts, computed from a range of day summaries.
public struct Insights: Hashable, Sendable {
    public struct Cell: Hashable, Sendable {
        public var weekday: Int
        public var hour: Int
        /// Average counted sightings in this hour on this weekday.
        public var average: Double
    }

    public struct HourDirection: Hashable, Sendable {
        public var hour: Int
        /// Average per day in the range.
        public var leftToRight: Double
        public var rightToLeft: Double
    }

    public struct Share: Hashable, Sendable, Identifiable {
        public var name: String
        public var count: Int
        public var id: String { name }
    }

    public var days: Int
    public var total: Int
    /// Weekday × hour, every combination present (zero when nothing was seen).
    public var heatmap: [Cell]
    public var directions: [HourDirection]
    public var leftToRightTotal: Int
    public var rightToLeftTotal: Int
    /// Most common first; past `limit` folded into "Other".
    public var colors: [Share]
    public var colorsKnown: Int
    public var makes: [Share]
    public var makesKnown: Int
    /// The busiest day in the range.
    public var busiest: DayCount?

    public init(_ summaries: [DaySummary], limit: Int = 8) {
        days = summaries.count
        total = summaries.reduce(0) { $0 + $1.total }

        var sums = [[Int]](repeating: Array(repeating: 0, count: 24), count: 8)
        var weekdayDays = [Int](repeating: 0, count: 8)
        var lr = [Int](repeating: 0, count: 24), rl = lr
        var colorCounts: [String: Int] = [:], makeCounts: [String: Int] = [:]
        for s in summaries {
            weekdayDays[s.weekday] += 1
            for h in 0..<24 {
                sums[s.weekday][h] += s.hours[h]
                lr[h] += s.leftToRight[h]
                rl[h] += s.rightToLeft[h]
            }
            colorCounts.merge(s.colors, uniquingKeysWith: +)
            makeCounts.merge(s.makes, uniquingKeysWith: +)
        }
        heatmap = (1...7).flatMap { wd in
            (0..<24).map { h in
                Cell(weekday: wd, hour: h, average: weekdayDays[wd] == 0 ? 0 : Double(sums[wd][h]) / Double(weekdayDays[wd]))
            }
        }
        let n = Double(max(days, 1))
        directions = (0..<24).map { HourDirection(hour: $0, leftToRight: Double(lr[$0]) / n, rightToLeft: Double(rl[$0]) / n) }
        leftToRightTotal = lr.reduce(0, +)
        rightToLeftTotal = rl.reduce(0, +)
        colors = Self.top(colorCounts, limit: limit)
        colorsKnown = colorCounts.values.reduce(0, +)
        makes = Self.top(makeCounts, limit: limit)
        makesKnown = makeCounts.values.reduce(0, +)
        busiest = summaries.filter { $0.total > 0 }
            .max { ($0.total, $1.day) < ($1.total, $0.day) }   // ties: the earlier day
            .map { DayCount(day: $0.day, count: $0.total) }
    }

    /// The `limit` largest, then the rest as one "Other" share.
    static func top(_ counts: [String: Int], limit: Int) -> [Share] {
        let sorted = counts.map { Share(name: $0.key, count: $0.value) }
            .sorted { ($0.count, $1.name) > ($1.count, $0.name) }
        guard sorted.count > limit else { return sorted }
        let rest = sorted[(limit - 1)...].reduce(0) { $0 + $1.count }
        return Array(sorted[..<(limit - 1)]) + [Share(name: "Other", count: rest)]
    }
}

/// The busiest day in all of history: the daily videos' counts (api-contract §5.5, counted per §6.1), plus days
/// with no daily video yet (today) from summaries.
public func busiestDay(dailies: [Daily], summaries: [DaySummary]) -> DayCount? {
    var counts: [PCDay: Int] = [:]
    for d in dailies { if let n = d.sightingsCount { counts[d.day] = n } }
    for s in summaries { counts[s.day] = max(counts[s.day] ?? 0, s.total) }
    return counts.filter { $0.value > 0 }
        .max { ($0.value, $1.key) < ($1.value, $0.key) }
        .map { DayCount(day: $0.key, count: $0.value) }
}

/// Day summaries on disk, one file per paired PC. Only finished days are stored: before today and with nothing
/// still waiting on the cloud.
public struct InsightsCache: Sendable {
    let url: URL

    struct File: Codable {
        var version: Int
        var days: [String: DaySummary]
    }

    public init(serverURL: URL, directory: URL? = nil) {
        let dir = directory ?? FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        let key = (serverURL.host() ?? "server").replacingOccurrences(of: "/", with: "_")
        url = dir.appending(path: "insights-\(key).json")
    }

    public func load() -> [PCDay: DaySummary] {
        guard let data = try? Data(contentsOf: url),
              let file = try? JSONDecoder().decode(File.self, from: data), file.version == DaySummary.version
        else { return [:] }
        return Dictionary(file.days.values.map { ($0.day, $0) }, uniquingKeysWith: { a, _ in a })
    }

    public func save(_ days: [PCDay: DaySummary], today: PCDay) {
        let keep = days.values.filter { $0.day < today && !$0.pending }
        let file = File(version: DaySummary.version, days: Dictionary(keep.map { ($0.day.rawValue, $0) }) { a, _ in a })
        guard let data = try? JSONEncoder().encode(file) else { return }
        try? data.write(to: url, options: .atomic)
    }

    public func clear() { try? FileManager.default.removeItem(at: url) }
}
