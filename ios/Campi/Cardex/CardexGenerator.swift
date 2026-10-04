import CampiKit
import Foundation
import FoundationModels

/// What the on-device model fills in for a card. Game-style ratings only: a small model's "specs" would often be wrong.
@Generable
struct GeneratedCard {
    @Guide(description: "The vehicle's name as a collector would say it, at most 4 words")
    var displayName: String

    @Guide(description: "A short category such as Commuter, Work Truck, JDM, Muscle, Luxury, Family Hauler, Off-Roader, Hot Hatch, Classic or Delivery")
    var type: String

    @Guide(description: "Three playful ratings of different qualities, such as Speed, Practicality, Street Cred, Comfort, Toughness or Head Turns", .count(3))
    var ratings: [GeneratedRating]

    @Guide(description: "One playful line of flavor text, at most 90 characters, with no numbers or specifications")
    var flavor: String

    @Guide(description: "The vehicle's body shape. suv is a crossover like a RAV4; fullSizeSUV is a big one like a Tahoe; offRoader is a boxy 4x4 like a Wrangler or Bronco; van is a minivan; cargoVan is a work van like a Sprinter; boxTruck covers box, dump and other big work trucks")
    var bodyStyle: GeneratedBodyStyle
}

/// `CarBodyStyle`, for the model to pick from.
@Generable
enum GeneratedBodyStyle: String {
    case sedan, coupe, supercar, suv, fullSizeSUV, offRoader, hatchback, wagon, pickup, van, cargoVan, boxTruck,
         bus, motorcycle
}

@Generable
struct GeneratedRating {
    @Guide(description: "The quality being rated, one or two words")
    var name: String

    @Guide(description: "The score", .range(1...10))
    var value: Int
}

/// Writes card text with Apple's on-device model.
enum CardexGenerator {
    enum Failure: Error {
        case unavailable(String)
        case failed(String)
    }

    /// Why card text can't be generated right now, or `nil` when it can.
    static var unavailableReason: String? {
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("-cardexFallback") { return "Card text is turned off for this test run." }
        #endif
        switch SystemLanguageModel.default.availability {
        case .available: return nil
        case .unavailable(.deviceNotEligible): return "Card text needs an iPhone with Apple Intelligence."
        case .unavailable(.appleIntelligenceNotEnabled): return "Turn on Apple Intelligence in Settings for card text."
        case .unavailable(.modelNotReady): return "Apple Intelligence is still getting ready. Card text will appear later."
        case .unavailable: return "Apple Intelligence isn't available."
        }
    }

    static func generate(for item: CollectionItem) async throws(Failure) -> CardexText {
        if let why = unavailableReason { throw .unavailable(why) }
        // A fresh session per card: one request each, no shared transcript to grow or to run concurrently.
        let session = LanguageModelSession(instructions: CardexPrompt.instructions)
        do {
            let card = try await session.respond(to: CardexPrompt.prompt(for: item), generating: GeneratedCard.self).content
            return CardexText(displayName: card.displayName, type: card.type,
                              ratings: card.ratings.map { .init(name: $0.name, value: $0.value) },
                              flavor: card.flavor,
                              bodyStyle: CarBodyStyle(rawValue: card.bodyStyle.rawValue)
                                  ?? CarBodyStyle.guess(label: item.label, make: item.make, model: item.model),
                              generated: true).sanitized()
        } catch let e as LanguageModelSession.GenerationError {
            switch e {
            case .guardrailViolation, .refusal: throw .failed("Apple Intelligence declined to write this card.")
            case .assetsUnavailable: throw .unavailable("Apple Intelligence is still getting ready. Card text will appear later.")
            default: throw .failed("Couldn't write this card.")
            }
        } catch {
            throw .failed("Couldn't write this card.")
        }
    }
}
