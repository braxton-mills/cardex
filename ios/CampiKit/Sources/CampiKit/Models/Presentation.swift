import Foundation

/// Items of one PC-local day, in their original order.
public struct DayGroup<Item>: Identifiable {
    public let day: PCDay
    public var items: [Item]
    public var id: String { day.rawValue }
}

extension DayGroup: Sendable where Item: Sendable {}

/// Groups consecutive items by day, keeping list order (lists come newest first, so days come newest first).
public func groupedByDay<Item>(_ items: [Item], day: (Item) -> PCDay) -> [DayGroup<Item>] {
    var out: [DayGroup<Item>] = []
    for item in items {
        let d = day(item)
        if out.last?.day == d {
            out[out.count - 1].items.append(item)
        } else if let i = out.firstIndex(where: { $0.day == d }) {
            out[i].items.append(item)   // tolerate out-of-order input
        } else {
            out.append(DayGroup(day: d, items: [item]))
        }
    }
    return out
}

extension SightingQuery {
    /// Number of user-visible filters that differ from the default (paging excluded), for a filter badge.
    public var activeFilterCount: Int {
        var n = 0
        if from != nil || to != nil { n += 1 }
        if !classes.isEmpty { n += 1 }
        if !makes.isEmpty { n += 1 }
        if label != nil { n += 1 }
        if !decidedBy.isEmpty { n += 1 }
        if starredOnly { n += 1 }
        if hideUnsure { n += 1 }
        if hideStationary { n += 1 }
        if includeHidden { n += 1 }
        return n
    }

    /// The same filters without paging state.
    public var filtersOnly: SightingQuery {
        var q = self
        q.cursor = nil
        return q
    }

    /// Whether a (possibly just-edited) sighting still belongs in a list with these filters. Used to drop rows
    /// locally after an action, e.g. hiding one or un-starring it in a starred-only list.
    public func admits(_ s: Sighting) -> Bool {
        if !includeHidden && s.hidden { return false }
        if starredOnly && !s.starred { return false }
        if hideStationary && s.stationary { return false }
        if hideUnsure && s.unsure && s.decidedBy == .siglip { return false }
        if let label, s.label != label { return false }
        if !makes.isEmpty && !makes.contains(s.make ?? "") { return false }
        if !decidedBy.isEmpty && !decidedBy.contains(s.decidedBy) { return false }
        if !classes.isEmpty && !classes.contains(s.yoloClass ?? "") { return false }
        if let from, s.day < from { return false }
        if let to, s.day > to { return false }
        return true
    }
}

extension LabelCollection {
    /// Distinct makes, sorted, for the make filter.
    public var makes: [String] {
        Array(Set(items.compactMap(\.make))).sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
    }

    public struct Section: Identifiable, Sendable {
        public let title: String
        public let items: [CollectionItem]
        public var id: String { title }
    }

    /// Labels-file items grouped by make (generic types last), then discovered, then other, keeping API order.
    public func sections(matching search: String = "", caught: Bool? = nil) -> [Section] {
        let q = search.trimmingCharacters(in: .whitespaces)
        let filtered = items.filter { item in
            (caught == nil || item.isCaught == caught) &&
                (q.isEmpty || item.label.localizedCaseInsensitiveContains(q)
                    || (item.make?.localizedCaseInsensitiveContains(q) ?? false))
        }
        var out: [Section] = []
        var makeOrder: [String] = []
        var byMake: [String: [CollectionItem]] = [:]
        var generic: [CollectionItem] = []
        for item in filtered where item.origin == .labelsFile || item.origin == .unknown {
            if let make = item.make, !item.generic {
                if byMake[make] == nil { makeOrder.append(make) }
                byMake[make, default: []].append(item)
            } else {
                generic.append(item)
            }
        }
        out += makeOrder.map { Section(title: $0, items: byMake[$0]!) }
        if !generic.isEmpty { out.append(Section(title: "Other vehicles", items: generic)) }
        let discovered = filtered.filter { $0.origin == .discovered }
        if !discovered.isEmpty { out.append(Section(title: "Discovered by Gemini", items: discovered)) }
        let other = filtered.filter { $0.origin == .other }
        if !other.isEmpty { out.append(Section(title: "No longer in the labels file", items: other)) }
        return out
    }

    public func item(label: String) -> CollectionItem? { items.first { $0.label == label } }
}

extension Seek {
    /// Why there's nothing to play, in words; `nil` when there is a video.
    public var explanation: String? {
        guard target == .none || video == nil else { return nil }
        return switch reason {
        case .pendingRender: "That part of the timelapse hasn't been rendered yet (renders wait while you're gaming). Try again in a few minutes."
        case .noFrames: "There are no timelapse frames from then: it was too dark, or capture was down."
        case .expired: "The 10-minute clip has expired, and there's no daily video for that day."
        case .notRendered, .unknown, .none: "No timelapse was rendered for that time."
        }
    }
}

/// Clips of one PC-local hour.
public struct HourGroup: Identifiable, Sendable {
    public let day: PCDay
    public let hour: Int
    public var clips: [Clip]
    public var id: String { "\(day.rawValue)-\(hour)" }
}

extension ClipList {
    /// Clips grouped by the PC-local hour of their window start, keeping list order (newest first).
    public var byHour: [HourGroup] {
        var out: [HourGroup] = []
        for clip in items {
            var cal = Calendar(identifier: .gregorian)
            cal.timeZone = clip.windowStart.timeZone
            let hour = cal.component(.hour, from: clip.windowStart.date)
            if let last = out.last, last.day == clip.day, last.hour == hour {
                out[out.count - 1].clips.append(clip)
            } else {
                out.append(HourGroup(day: clip.day, hour: hour, clips: [clip]))
            }
        }
        return out
    }
}

extension Clip {
    /// "expires in about 5 h" / "expires within the hour" / "may be deleted any time now".
    public func expiryText(now: Date = Date()) -> String {
        let left = expiresAfter.date.timeIntervalSince(now)
        if left <= 0 { return "may be deleted any time now" }
        if left < 3600 { return "expires within the hour" }
        return "expires in about \(Int((left / 3600).rounded())) h"
    }

    /// Less than this left: suggest saving it.
    public func isExpiringSoon(now: Date = Date()) -> Bool { expiresAfter.date.timeIntervalSince(now) < 3 * 3600 }

    /// File name to use when saving or sharing.
    public var fileName: String { "campi_\(id).mp4" }
}

extension Daily {
    public var fileName: String { "campi_daily_\(day.rawValue).mp4" }
}

/// "2.6 MB", "1.2 GB".
public func byteText(_ bytes: Int) -> String {
    ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
}
