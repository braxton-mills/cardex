import Foundation
import simd
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
        #expect(CarBodyStyle.guess(label: "school bus") == .bus)
        #expect(CarBodyStyle.guess(label: "Chevrolet Tahoe") == .fullSizeSUV)
        #expect(CarBodyStyle.guess(label: "Ford Transit") == .cargoVan)
        #expect(CarBodyStyle.guess(label: "motorcycle") == .motorcycle)
        #expect(CarBodyStyle.guess(label: "Honda Civic") == .sedan)
        #expect(CarBodyStyle.guess(label: "Tesla Model 3") == .sedan)
        // whole words only: "fit" isn't in "Fitzgerald"
        #expect(CarBodyStyle.guess(label: "Fitzgerald Special") == .sedan)
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

@Suite struct CarShapeTests {
    @Test func everyStyleBuildsAClosedLookingMesh() {
        for style in CarBodyStyle.allCases {
            let shape = CarShape.preset(style)
            let mesh = CarMesh(shape)
            #expect(mesh.triangleCount > 300 && mesh.triangleCount < 4000, "\(style): \(mesh.triangleCount) triangles")
            #expect(mesh.pieces[.paint]?.triangleCount ?? 0 > 0)
            #expect(mesh.pieces[.tire]?.triangleCount ?? 0 > 0)
            for piece in mesh.pieces.values {
                #expect(piece.normals.allSatisfy { abs(simd_length($0) - 1) < 0.001 })
                #expect(piece.indices.allSatisfy { Int($0) < piece.positions.count })
            }
            // on the ground, about as long and tall as the shape says
            let (lo, hi) = mesh.bounds
            #expect(abs(lo.y) < 0.01, "\(style) floats or sinks: \(lo.y)")
            // the body, or the wheels where they reach past it (motorcycles)
            let reach = max(shape.length, shape.wheelbase + shape.wheelDiameter * 1.1)
            #expect(hi.z - lo.z > reach * 0.95 && hi.z - lo.z < reach * 1.15, "\(style) length \(hi.z - lo.z)")
            #expect(hi.y < shape.height * 1.15, "\(style) height")
        }
    }

    @Test func knownModelsGetTheirOwnShapes() {
        let wrangler = CarShape.for(label: "Jeep Wrangler")
        #expect(wrangler.extras.contains(.spareTire) && wrangler.roof == .hatch)
        #expect(CarShape.for(label: "Tesla Cybertruck").roof == .fastback)
        #expect(CarShape.for(label: "Tesla Model 3").extras.contains(.glassRoof))
        #expect(CarShape.for(label: "Ford F-150").roof == .pickup)
        #expect(CarShape.for(label: "Ford Mustang") != CarShape.for(label: "Toyota GR86"))
        #expect(CarShape.for(label: "Toyota Camry") != CarShape.for(label: "Toyota Corolla"))
        // unknown labels fall back to the body style's preset
        #expect(CarShape.for(label: "Zorblax 9000", style: .van) == CarShape.preset(.van))
        #expect(CarShape.for(label: "Zorblax 9000") == CarShape.preset(.sedan))
    }

    @Test func pickupBedIsOpen() {
        // the bed floor sits below the rails: some trim faces point up from below the belt
        let shape = CarShape.preset(.pickup)
        let trim = CarMesh(shape).pieces[.trim]!
        let belt = shape.belt * shape.height
        let floorFaces = stride(from: 0, to: trim.indices.count, by: 3).filter { i in
            let p = trim.positions[Int(trim.indices[i])], n = trim.normals[Int(trim.indices[i])]
            return n.y > 0.9 && p.y < belt - 0.2 && p.y > shape.clearance + 0.1
        }
        #expect(!floorFaces.isEmpty)
    }
}

@Suite struct CardStyleTests {
    @Test func patternsAreStableAndFitTheFinish() {
        for finish in CardFinish.allCases {
            let p = HoloPattern.for(label: "Toyota Camry", finish: finish)
            #expect(HoloPattern.options(for: finish).contains(p))
            #expect(HoloPattern.for(label: "Toyota Camry", finish: finish) == p)
        }
        #expect(HoloPattern.for(label: "anything", finish: .plain) == .gloss)
        #expect(stableHash("a") == 0xE40C_292C)   // FNV-1a, the same on every launch
    }

    @Test func aBinderGetsAMix() {
        let labels = (1...40).map { "Car \($0)" }
        #expect(Set(labels.map { HoloPattern.for(label: $0, finish: .holo) }).count >= 4)
        #expect(Set(labels.map { SceneTheme.for(label: $0, style: .sedan, finish: .holo) }).count >= 3)
        // only special illustrations reach the rare themes, and some do
        #expect(labels.allSatisfy { ![.space, .aurora].contains(SceneTheme.for(label: $0, style: .sedan, finish: .holo)) })
        #expect(labels.contains { [.space, .aurora].contains(SceneTheme.for(label: $0, style: .sedan, finish: .specialIllustration)) })
    }
}
