import Foundation
import Testing
@testable import CampiKit

@Suite struct CardexTests {
    func items() throws -> [CollectionItem] {
        try Fixtures.decode(LabelCollection.self, "collection.json").items
    }

    @Test func promptUsesLabelLevelFactsOnly() throws {
        let all = try items()
        let car = try #require(all.first { !$0.generic && $0.make != nil && $0.origin == .labelsFile })
        let p = CardexPrompt.prompt(for: car)
        #expect(p.contains("Vehicle: \(car.label)"))
        #expect(p.contains("Make: \(car.make!)"))
        #expect(!p.contains("general vehicle type"))
        if let generic = all.first(where: \.generic) {
            #expect(CardexPrompt.prompt(for: generic).contains("general vehicle type"))
            #expect(!CardexPrompt.prompt(for: generic).contains("Make:"))
        }
        if let found = all.first(where: { $0.origin == .discovered }) {
            #expect(CardexPrompt.prompt(for: found).contains("wasn't on the watch list"))
        }
        #expect(CardexPrompt.instructions.contains("Never state specifications"))
    }

    @Test func fallbackIsPlain() throws {
        let all = try items()
        let car = try #require(all.first { !$0.generic && $0.make != nil })
        let f = CardexPrompt.fallback(for: car)
        #expect(f.displayName == car.label && f.type == car.make && f.ratings.isEmpty && f.flavor == nil && !f.generated)
        if let generic = all.first(where: \.generic) {
            #expect(CardexPrompt.fallback(for: generic).type == "Vehicle type")
        }
    }

    @Test func sanitizeClampsAndTrims() {
        let raw = CardexText(
            displayName: "  \"Toyota GR86\" ", type: "JDM",
            ratings: [.init(name: "Speed", value: 14), .init(name: "speed", value: 3), .init(name: "Street Cred", value: 0),
                      .init(name: "", value: 5), .init(name: "Comfort", value: 6), .init(name: "Toughness", value: 7),
                      .init(name: "Head Turns", value: 8)],
            flavor: String(repeating: "vroom ", count: 40), generated: true)
        let s = raw.sanitized()
        #expect(s.displayName == "Toyota GR86")
        #expect(s.ratings.map(\.name) == ["Speed", "Street Cred", "Comfort", "Toughness"])
        #expect(s.ratings.map(\.value) == [10, 1, 6, 7])
        #expect((s.flavor?.count ?? 0) <= 120 && s.flavor?.hasSuffix("…") == true)
        #expect(CardexText(displayName: "x", type: "y", ratings: [], flavor: "  ", generated: true).sanitized().flavor == nil)
    }
}
