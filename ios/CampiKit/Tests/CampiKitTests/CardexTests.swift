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

    private func item(_ label: String, count: Int, tier: Tier, origin: CollectionItem.Origin = .labelsFile) -> CollectionItem {
        CollectionItem(label: label, make: nil, model: nil, generic: false, origin: origin, count: count,
                       firstSeenAt: nil, lastSeenAt: nil, tier: tier, cover: nil)
    }

    @Test func finishFollowsRarityAndChaseRules() {
        #expect(CardFinish(for: item("A", count: 0, tier: .uncaught)) == nil)
        #expect(CardFinish(for: item("A", count: 40, tier: .common)) == .plain)
        #expect(CardFinish(for: item("A", count: 9, tier: .uncommon)) == .reverseHolo)
        #expect(CardFinish(for: item("A", count: 2, tier: .rare)) == .holo)
        #expect(CardFinish(for: item("A", count: 1, tier: .rare)) == .specialIllustration)
        #expect(CardFinish(for: item("A", count: 5, tier: .uncommon, origin: .discovered)) == .fullArt)
        // seen-once rares beat discovered
        #expect(CardFinish(for: item("A", count: 1, tier: .rare, origin: .discovered)) == .specialIllustration)
        #expect(CardFinish.plain < CardFinish.specialIllustration)
    }

    @Test func bodyStyleGuesses() {
        #expect(CarBodyStyle.guess(label: "Ford F-150") == .pickup)
        #expect(CarBodyStyle.guess(label: "Ram 1500") == .pickup)
        #expect(CarBodyStyle.guess(label: "Chevrolet Silverado 1500") == .pickup)
        #expect(CarBodyStyle.guess(label: "Honda Odyssey") == .van)
        #expect(CarBodyStyle.guess(label: "Jeep Wrangler") == .offRoader)
        #expect(CarBodyStyle.guess(label: "Toyota RAV4") == .suv)
        #expect(CarBodyStyle.guess(label: "Tesla Model Y") == .suv)
        #expect(CarBodyStyle.guess(label: "Subaru Outback") == .wagon)
        #expect(CarBodyStyle.guess(label: "Ford Mustang") == .coupe)
        #expect(CarBodyStyle.guess(label: "school bus") == .boxTruck)
        #expect(CarBodyStyle.guess(label: "Honda Civic") == .sedan)
        #expect(CarBodyStyle.guess(label: "Tesla Model 3") == .sedan)
        // whole words only: "fit" isn't in "Fitzgerald"
        #expect(CarBodyStyle.guess(label: "Fitzgerald Special") == .sedan)
        #expect(CarBodyStyle.wagon.modelName == "hatchback")
    }

    @Test func fallbackGuessesBodyStyle() {
        #expect(CardexPrompt.fallback(for: item("Toyota Tacoma", count: 3, tier: .rare)).bodyStyle == .pickup)
    }

    @Test func paintColors() {
        #expect(PaintColor.from(nil) == nil)
        #expect(PaintColor.from("chartreuse") == nil)
        #expect(PaintColor.from("silver")?.metallic == true)
        let blue = try! #require(PaintColor.from("blue"))
        let dark = try! #require(PaintColor.from("Dark Blue"))
        #expect(dark.blue < blue.blue)
        #expect((PaintColor.from("light gray")?.red ?? 0) > (PaintColor.from("gray")?.red ?? 1))
    }
}
