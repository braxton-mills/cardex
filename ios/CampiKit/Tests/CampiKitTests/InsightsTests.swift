import Foundation
import Testing
@testable import CampiKit

@Suite struct InsightsTests {
    func sighting(_ day: String = "2026-10-03", at time: String = "13:49:08", direction: Sighting.Direction? = .leftToRight,
                  color: String? = "Green", make: String? = "Jeep") throws -> Sighting {
        var s = try Fixtures.decode(Sighting.self, "sighting_cloud.json")
        s.id = UUID().uuidString
        s.day = PCDay(day)!
        s.startedAt = PCDate(rfc3339: "\(day)T\(time).000-05:00")!
        s.direction = direction
        s.color = color
        s.make = make
        s.label = "Jeep Grand Cherokee"
        s.hidden = false
        s.unsure = false
        s.cloudStatus = .done
        return s
    }

    @Test func countsFollowTheContract() throws {
        var s = try sighting()
        #expect(s.counts)
        s.hidden = true
        #expect(!s.counts)
        s.hidden = false
        s.label = nil
        #expect(!s.counts)
        s.label = "Jeep Grand Cherokee"
        s.unsure = true
        s.decidedBy = .siglip
        #expect(!s.counts)
        s.decidedBy = .user
        #expect(s.counts)
    }

    @Test func summarizesADayOnThePCClock() throws {
        var hidden = try sighting(at: "08:10:00")
        hidden.hidden = true
        let rows = [
            try sighting(at: "08:05:00", direction: .leftToRight, color: " Green "),
            try sighting(at: "08:55:00", direction: .rightToLeft, color: "green", make: "Ford"),
            try sighting(at: "17:30:00", direction: nil, color: nil, make: nil),
            hidden,
            try sighting("2026-10-02", at: "09:00:00"),   // another day: ignored
        ]
        let s = DaySummary(day: PCDay("2026-10-03")!, sightings: rows)
        #expect(s.total == 3)
        #expect(s.hours[8] == 2 && s.hours[17] == 1)
        #expect(s.leftToRight[8] == 1 && s.rightToLeft[8] == 1 && s.leftToRight[17] == 0)
        #expect(s.colors == ["green": 2])
        #expect(s.makes == ["Jeep": 1, "Ford": 1])
        #expect(!s.pending)
        #expect(s.weekday == 7)   // Saturday

        var waiting = try sighting()
        waiting.cloudStatus = .pending
        #expect(DaySummary(day: PCDay("2026-10-03")!, sightings: [waiting]).pending)
    }

    @Test func dayArithmetic() {
        let d = PCDay("2026-10-30")!
        #expect(d.adding(days: 3).rawValue == "2026-11-02")
        #expect(d.adding(days: -30).rawValue == "2026-09-30")
        #expect(d.days(to: PCDay("2026-11-02")!) == 3)
        #expect(PCDay.range(PCDay("2026-12-30")!, through: PCDay("2027-01-02")!).map(\.rawValue)
                == ["2026-12-30", "2026-12-31", "2027-01-01", "2027-01-02"])
        #expect(PCDay.range(PCDay("2026-10-02")!, through: PCDay("2026-10-01")!).isEmpty)
    }

    @Test func aggregatesARange() throws {
        // Two Saturdays and a Friday
        let sat1 = DaySummary(day: PCDay("2026-10-03")!, sightings: [
            try sighting(at: "08:05:00"), try sighting(at: "08:10:00", direction: .rightToLeft),
        ])
        let sat2 = DaySummary(day: PCDay("2026-10-10")!, sightings: [try sighting("2026-10-10", at: "08:30:00")])
        let fri = DaySummary(day: PCDay("2026-10-09")!, sightings: [
            try sighting("2026-10-09", at: "17:00:00", color: "White", make: "Toyota"),
            try sighting("2026-10-09", at: "17:20:00", color: "white", make: "Toyota"),
            try sighting("2026-10-09", at: "17:40:00", color: nil, make: "Toyota"),
        ])
        let i = Insights([sat1, sat2, fri])
        #expect(i.days == 3 && i.total == 6)
        #expect(i.heatmap.count == 7 * 24)
        #expect(i.heatmap.first { $0.weekday == 7 && $0.hour == 8 }?.average == 1.5)
        #expect(i.heatmap.first { $0.weekday == 6 && $0.hour == 17 }?.average == 3)
        #expect(i.heatmap.first { $0.weekday == 1 && $0.hour == 8 }?.average == 0)
        #expect(i.directions[8].leftToRight == 2.0 / 3 && i.directions[8].rightToLeft == 1.0 / 3)
        #expect(i.leftToRightTotal == 5 && i.rightToLeftTotal == 1)
        #expect(i.colors.map(\.name) == ["green", "white"] && i.colorsKnown == 5)
        #expect(i.makes == [.init(name: "Jeep", count: 3), .init(name: "Toyota", count: 3)])   // ties: by name
        #expect(i.busiest == DayCount(day: PCDay("2026-10-09")!, count: 3))
    }

    @Test func foldsTheTailIntoOther() {
        let counts = ["a": 5, "b": 4, "c": 3, "d": 2, "e": 1]
        #expect(Insights.top(counts, limit: 5).map(\.name) == ["a", "b", "c", "d", "e"])
        let top = Insights.top(counts, limit: 3)
        #expect(top.map(\.name) == ["a", "b", "Other"] && top.last?.count == 6)
    }

    @Test func busiestDayEverUsesDailiesAndToday() throws {
        var dailies = try Fixtures.decode(Page<Daily>.self, "daily_page.json").items
        #expect(dailies.count >= 2)
        dailies[0].sightingsCount = 40
        dailies[1].sightingsCount = 55
        let busiest = busiestDay(dailies: dailies, summaries: [])
        #expect(busiest == DayCount(day: dailies[1].day, count: 55))

        let today = DaySummary(day: PCDay("2030-01-01")!, sightings: try (0..<60).map { _ in
            try sighting("2030-01-01", at: "10:00:00")
        })
        #expect(busiestDay(dailies: dailies, summaries: [today]) == DayCount(day: today.day, count: 60))
        #expect(busiestDay(dailies: [], summaries: []) == nil)
    }

    @Test func cacheKeepsOnlyFinishedDays() throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let cache = InsightsCache(serverURL: URL(string: "https://pc.tail.ts.net")!, directory: dir)
        #expect(cache.load().isEmpty)

        var waiting = try sighting("2026-10-02")
        waiting.cloudStatus = .pending
        let done = DaySummary(day: PCDay("2026-10-01")!, sightings: [try sighting("2026-10-01")])
        let pending = DaySummary(day: PCDay("2026-10-02")!, sightings: [waiting])
        let today = DaySummary(day: PCDay("2026-10-03")!, sightings: [try sighting()])
        cache.save([done.day: done, pending.day: pending, today.day: today], today: today.day)
        #expect(cache.load() == [done.day: done])
        cache.clear()
        #expect(cache.load().isEmpty)
    }
}
