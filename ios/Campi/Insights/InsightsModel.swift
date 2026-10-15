import CampiKit
import Foundation
import Observation

/// Traffic patterns over a range of days, from counted sightings (`GET /api/sightings`, one day at a time) and the
/// daily videos' counts. Finished days are cached on disk, so a range is downloaded once.
@MainActor @Observable
final class InsightsModel {
    enum Range: Int, CaseIterable, Identifiable {
        case week = 7, month = 30, quarter = 90, all = 0
        var id: Int { rawValue }
        var title: String {
            switch self {
            case .week: "7 Days"
            case .month: "30 Days"
            case .quarter: "90 Days"
            case .all: "All"
            }
        }
        /// "the 30-day average"
        var averageTitle: String { self == .all ? "all-time" : "\(rawValue)-day" }
    }

    var range: Range = .month
    private(set) var insights: Insights?
    /// The busiest day in all of history, whatever the range.
    private(set) var busiestEver: DayCount?
    private(set) var dailies: [PCDay: Daily] = [:]
    private(set) var status: Status?
    /// Days counted so far of the days in range, while loading.
    private(set) var progress: (done: Int, total: Int)?
    private(set) var error: APIError?

    private var summaries: [PCDay: DaySummary] = [:]
    private var cache: InsightsCache?
    /// Sightings edited in the app whose days were dropped from the cache, as last seen.
    private var invalidated: [String: Sighting] = [:]

    static let concurrentDays = 4

    var timeZone: TimeZone { status?.serverTime.timeZone ?? .current }

    func load(app: AppModel) async {
        guard let client = app.client, let connection = app.connection else { return }
        if cache == nil {
            let c = InsightsCache(serverURL: connection.baseURL)
            cache = c
            summaries = c.load()
        }
        // A star, hide or label correction in the app can change a day's counts.
        for s in app.sightings.updated.values where invalidated[s.id] != s {
            summaries[s.day] = nil
            invalidated[s.id] = s
        }
        do {
            async let statusCall = client.status()
            async let dailiesCall = Self.allDailies(client)
            let (status, dailies) = try await (statusCall, dailiesCall)
            self.status = status
            self.dailies = Dictionary(dailies.map { ($0.day, $0) }) { a, _ in a }
            guard status.sightings.hasHistory else { return }

            let today = status.today
            let start: PCDay
            if range == .all {
                let firstSeen = (try? await app.loadCollection())?.items.compactMap { $0.firstSeenAt?.day }.min()
                // the first counted sighting; daily videos can predate sightings
                start = firstSeen ?? dailies.map(\.day).min() ?? today
            } else {
                start = today.adding(days: -(range.rawValue - 1))
            }
            let days = PCDay.range(start, through: today)
            let missing = days.filter { $0 >= today || summaries[$0]?.pending ?? true }
            update(days)
            progress = missing.isEmpty ? nil : (days.count - missing.count, days.count)
            defer {
                progress = nil
                cache?.save(summaries, today: today)
            }

            try await withThrowingTaskGroup(of: DaySummary.self) { group in
                var queue = missing.reversed().makeIterator()   // newest first: the start of the range fills in last
                for _ in 0..<Self.concurrentDays {
                    if let day = queue.next() { group.addTask { try await Self.summary(of: day, client: client) } }
                }
                var sinceUpdate = 0
                while let s = try await group.next() {
                    summaries[s.day] = s
                    progress?.done += 1
                    sinceUpdate += 1
                    if sinceUpdate >= 10 {
                        update(days)
                        sinceUpdate = 0
                    }
                    if let day = queue.next() { group.addTask { try await Self.summary(of: day, client: client) } }
                }
            }
            update(days)
            error = nil
        } catch where Task.isCancelled {
            // the range changed or the screen closed; the next load picks up the counted days
        } catch {
            self.error = error as? APIError ?? .unreachable(error.localizedDescription)
            app.report(error)
        }
    }

    /// Forgets every cached day and counts again (e.g. after corrections made on the PC).
    func recount(app: AppModel) async {
        cache?.clear()
        summaries = [:]
        insights = nil
        await load(app: app)
    }

    private func update(_ days: [PCDay]) {
        insights = Insights(days.compactMap { summaries[$0] })
        busiestEver = busiestDay(dailies: Array(dailies.values), summaries: Array(summaries.values))
    }

    nonisolated private static func summary(of day: PCDay, client: APIClient) async throws -> DaySummary {
        var q = SightingQuery(from: day, to: day, limit: 200)
        q.hideUnsure = true   // §6.1; hidden rows are excluded by default
        var rows: [Sighting] = []
        while true {
            let page = try await client.sightings(q)
            rows += page.items
            guard let next = page.nextCursor else { break }
            q.cursor = next
        }
        return DaySummary(day: day, sightings: rows)
    }

    nonisolated private static func allDailies(_ client: APIClient) async throws -> [Daily] {
        var out: [Daily] = []
        var cursor: String?
        repeat {
            let page = try await client.dailies(limit: 200, cursor: cursor)
            out += page.items
            cursor = page.nextCursor
        } while cursor != nil
        return out
    }
}
