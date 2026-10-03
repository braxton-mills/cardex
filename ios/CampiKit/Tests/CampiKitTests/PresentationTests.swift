import Foundation
import Testing
@testable import CampiKit

@Suite struct PresentationTests {
    func page() throws -> [Sighting] {
        try Fixtures.decode(Page<Sighting>.self, "sightings_page.json").items
    }

    @Test func groupsByDayKeepingOrder() {
        let days = ["2026-10-03", "2026-10-03", "2026-10-02", "2026-10-01", "2026-10-01"].map { PCDay($0)! }
        let groups = groupedByDay(Array(days.enumerated()), day: \.element)
        #expect(groups.map(\.day.rawValue) == ["2026-10-03", "2026-10-02", "2026-10-01"])
        #expect(groups.map { $0.items.map(\.offset) } == [[0, 1], [2], [3, 4]])
        // out-of-order input still lands in the existing group
        let messy = groupedByDay([PCDay("2026-10-03")!, PCDay("2026-10-02")!, PCDay("2026-10-03")!], day: { $0 })
        #expect(messy.count == 2 && messy[0].items.count == 2)
    }

    @Test func activeFilterCount() {
        var q = SightingQuery(limit: 50)
        q.cursor = "abc"
        #expect(q.activeFilterCount == 0)
        q.from = PCDay("2026-10-01")
        q.to = PCDay("2026-10-03")
        q.makes = ["Toyota"]
        q.hideUnsure = true
        #expect(q.activeFilterCount == 3)
        #expect(q.filtersOnly.cursor == nil)
    }

    @Test func admitsMirrorsServerFilters() throws {
        var s = try Fixtures.decode(Sighting.self, "sighting_cloud.json")
        var q = SightingQuery()
        #expect(q.admits(s))
        s.hidden = true
        #expect(!q.admits(s))
        q.includeHidden = true
        #expect(q.admits(s))
        q.starredOnly = true
        s.starred = false
        #expect(!q.admits(s))
        s.starred = true
        q.decidedBy = [.siglip]
        #expect(!q.admits(s))                 // decided by cloud
        q.decidedBy = []
        q.hideUnsure = true
        s.unsure = true
        #expect(q.admits(s))                  // unsure but Gemini decided: kept
        q.to = PCDay("2000-01-01")
        #expect(!q.admits(s))
    }

    @Test func collectionSections() throws {
        let c = try Fixtures.decode(LabelCollection.self, "collection.json")
        let sections = c.sections()
        #expect(sections.first?.title == "Toyota")
        #expect(sections.contains { $0.title == "Other vehicles" && $0.items.allSatisfy(\.generic) })
        #expect(sections.last?.title == "Discovered by Gemini")
        #expect(sections.reduce(0) { $0 + $1.items.count } == c.items.count)
        let missing = c.sections(caught: false).flatMap(\.items)
        #expect(!missing.isEmpty && missing.allSatisfy { !$0.isCaught })
        let search = c.sections(matching: "tesla").flatMap(\.items)
        #expect(!search.isEmpty && search.allSatisfy { $0.label.contains("Tesla") })
        #expect(c.makes.contains("Toyota") && c.makes == c.makes.sorted { $0.lowercased() < $1.lowercased() })
    }

    @Test func seekExplanations() throws {
        #expect(try Fixtures.decode(Seek.self, "seek_clip.json").explanation == nil)
        let none = try Fixtures.decode(Seek.self, "seek_none.json")
        #expect(none.explanation?.contains("too dark") == true)
    }

    @Test func clipsByHour() throws {
        let list = try Fixtures.decode(ClipList.self, "clips.json")
        let groups = list.byHour
        #expect(groups.flatMap(\.clips).map(\.id) == list.items.map(\.id))
        for g in groups {
            #expect(g.clips.allSatisfy { $0.id.hasPrefix(g.day.rawValue) })
            #expect(g.clips.allSatisfy { Int($0.id.suffix(4).prefix(2)) == g.hour })
        }
    }

    @Test func clipExpiry() throws {
        let clip = try #require(try Fixtures.decode(ClipList.self, "clips.json").items.first)
        let exp = clip.expiresAfter.date
        #expect(clip.expiryText(now: exp.addingTimeInterval(-5 * 3600)) == "expires in about 5 h")
        #expect(clip.expiryText(now: exp.addingTimeInterval(-600)) == "expires within the hour")
        #expect(clip.expiryText(now: exp.addingTimeInterval(60)) == "may be deleted any time now")
        #expect(clip.isExpiringSoon(now: exp.addingTimeInterval(-3600)))
        #expect(!clip.isExpiringSoon(now: exp.addingTimeInterval(-10 * 3600)))
        #expect(clip.fileName == "campi_\(clip.id).mp4")
    }
}
