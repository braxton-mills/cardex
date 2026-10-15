import Foundation

/// The text of a Cardex card for one collection label: generated on device (Foundation Models) or a plain fallback.
/// Catch stats (times seen, tier, first seen) aren't part of it: they come from the PC and are shown next to it.
public struct CardexText: Codable, Hashable, Sendable {
    public struct Rating: Codable, Hashable, Sendable, Identifiable {
        public var name: String
        /// 1–10.
        public var value: Int
        public var id: String { name }

        public init(name: String, value: Int) {
            self.name = name
            self.value = value
        }
    }

    public var displayName: String
    /// A short category: "Commuter", "Work Truck", "JDM", ...
    public var type: String
    public var ratings: [Rating]
    public var flavor: String?
    /// The 3D model on the trading card.
    public var bodyStyle: CarBodyStyle
    /// `false` for the plain fallback.
    public var generated: Bool

    public init(displayName: String, type: String, ratings: [Rating], flavor: String?,
                bodyStyle: CarBodyStyle = .sedan, generated: Bool) {
        self.displayName = displayName
        self.type = type
        self.ratings = ratings
        self.flavor = flavor
        self.bodyStyle = bodyStyle
        self.generated = generated
    }

    /// Generated text, trimmed to what the card has room for (the model doesn't always respect lengths).
    public func sanitized() -> CardexText {
        func clip(_ s: String, _ n: Int) -> String {
            let t = s.trimmingCharacters(in: .whitespacesAndNewlines.union(CharacterSet(charactersIn: "\"“”")))
            return t.count <= n ? t : String(t.prefix(n - 1)).trimmingCharacters(in: .whitespaces) + "…"
        }
        var seen = Set<String>()
        let ratings = self.ratings
            .map { Rating(name: clip($0.name, 16), value: min(max($0.value, 1), 10)) }
            .filter { !$0.name.isEmpty && seen.insert($0.name.lowercased()).inserted }
        let flavor = self.flavor.map { clip($0, 120) }
        return CardexText(displayName: clip(displayName, 40), type: clip(type, 20), ratings: Array(ratings.prefix(4)),
                          flavor: flavor?.isEmpty == true ? nil : flavor, bodyStyle: bodyStyle, generated: generated)
    }
}

/// Prompt and fallback for Cardex cards. Bump `version` when the prompt or the generated shape changes: cached cards
/// from older versions are regenerated.
public enum CardexPrompt {
    public static let version = 2

    public static let instructions = """
        You write short, playful trading-card text for vehicles spotted by a home traffic camera, like a \
        collectible card game. Ratings are fun opinions on a 1 to 10 scale, not measurements. Never state \
        specifications or facts: no horsepower, prices, speeds, acceleration times, engine sizes, production \
        numbers or years. Keep it friendly and family-safe.
        """

    /// The request for one label. Only label-level facts the PC knows: no per-sighting details.
    public static func prompt(for item: CollectionItem) -> String {
        var lines = ["Vehicle: \(item.label)"]
        if let make = item.make { lines.append("Make: \(make)") }
        if let model = item.model { lines.append("Model: \(model)") }
        if item.generic { lines.append("This is a general vehicle type, not a specific make and model.") }
        if item.origin == .discovered { lines.append("A rare find: the camera's AI named it, it wasn't on the watch list.") }
        lines.append("Write this vehicle's card.")
        return lines.joined(separator: "\n")
    }

    /// The request for one sighting's own card: the label's facts plus what this car looked like and when it passed.
    /// No year range: the instructions forbid years, so the model isn't shown one.
    public static func prompt(for sighting: Sighting, item: CollectionItem?) -> String {
        let label = sighting.label ?? item?.label ?? sighting.displayLabel
        var lines = ["Vehicle: \(label)"]
        if let make = sighting.make ?? item?.make { lines.append("Make: \(make)") }
        if let model = sighting.model ?? item?.model { lines.append("Model: \(model)") }
        if item?.generic == true { lines.append("This is a general vehicle type, not a specific make and model.") }
        if item?.origin == .discovered { lines.append("A rare find: the camera's AI named it, it wasn't on the watch list.") }
        if let color = sighting.color { lines.append("Color: \(color)") }
        lines.append("Seen: in the \(timeOfDay(sighting.startedAt))")
        if sighting.stationary { lines.append("It was parked, not driving past.") }
        lines.append("Write the card for this particular car."
                     + (sighting.color == nil ? "" : " Work its color into the name."))
        return lines.joined(separator: "\n")
    }

    /// The plain card for one sighting: the label's, named with the car's color when it has one.
    public static func fallback(for sighting: Sighting, item: CollectionItem?) -> CardexText {
        let label = sighting.label ?? item?.label ?? sighting.displayLabel
        var text = item.map(fallback(for:)) ?? CardexText(
            displayName: label, type: sighting.make ?? "Vehicle", ratings: [], flavor: nil,
            bodyStyle: CarBodyStyle.guess(label: label, make: sighting.make, model: sighting.model), generated: false)
        text.displayName = sighting.color.map { "\($0.capitalized) \(label)" } ?? label
        return text
    }

    /// The cache key of a sighting's card. It includes the label and color, so a correction (or Gemini's answer
    /// arriving) writes a new card. Can't collide with a collection label.
    public static func sightingKey(_ sighting: Sighting) -> String {
        "sighting:\(sighting.id)|\(sighting.label ?? "")|\(sighting.color ?? "")"
    }

    static func timeOfDay(_ at: PCDate) -> String {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = at.timeZone
        return switch cal.component(.hour, from: at.date) {
        case 5..<12: "morning"
        case 12..<17: "afternoon"
        case 17..<21: "evening"
        default: "night"
        }
    }

    /// The plain card when on-device generation isn't available or fails.
    public static func fallback(for item: CollectionItem) -> CardexText {
        let type: String = if item.generic {
            "Vehicle type"
        } else if let make = item.make {
            make
        } else {
            "Vehicle"
        }
        return CardexText(displayName: item.label, type: type, ratings: [], flavor: nil,
                          bodyStyle: CarBodyStyle.guess(label: item.label, make: item.make, model: item.model),
                          generated: false)
    }
}
