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
