import Foundation

/// The bundled 3D model a Cardex card shows (`Campi/Resources/Cars/<rawValue>.usdz`, built by
/// tools/make_car_models.py). Picked by the on-device model when it writes the card, else guessed from the label.
public enum CarBodyStyle: String, Codable, Hashable, Sendable, CaseIterable {
    case sedan, coupe, suv, offRoader, hatchback, wagon, pickup, van, boxTruck

    /// The model file: wagons share the hatchback's.
    public var modelName: String { self == .wagon ? CarBodyStyle.hatchback.rawValue : rawValue }

    public var title: String {
        switch self {
        case .sedan: "Sedan"
        case .coupe: "Coupe"
        case .suv: "SUV"
        case .offRoader: "Off-Roader"
        case .hatchback: "Hatchback"
        case .wagon: "Wagon"
        case .pickup: "Pickup"
        case .van: "Van"
        case .boxTruck: "Truck"
        }
    }

    /// Keyword guesses, checked in order: first match wins. Lowercased, matched as whole words.
    private static let keywords: [(CarBodyStyle, [String])] = [
        (.boxTruck, ["box truck", "dump truck", "semi", "garbage", "mixer", "bus", "excavator", "delivery",
                     "sprinter", "promaster"]),
        (.pickup, ["pickup", "truck", "f-150", "f-250", "silverado", "sierra", "ram 1500", "1500", "tacoma",
                   "tundra", "ranger", "colorado", "frontier", "titan", "ridgeline", "maverick", "gladiator",
                   "cybertruck", "r1t"]),
        (.van, ["van", "minivan", "odyssey", "sienna", "pacifica", "carnival", "transit", "caravan"]),
        (.offRoader, ["wrangler", "bronco", "4runner", "land cruiser", "defender", "g-class", "g-wagon"]),
        (.wagon, ["wagon", "outback", "v60", "v90", "allroad"]),
        (.hatchback, ["hatchback", "golf", "fit", "prius", "bolt", "mini", "veloster", "impreza", "leaf"]),
        (.coupe, ["coupe", "mustang", "camaro", "challenger", "corvette", "911", "gr86", "brz", "miata", "mx-5",
                  "supra", "roma", "huracan"]),
        (.suv, ["suv", "rav4", "cr-v", "explorer", "equinox", "tahoe", "suburban", "model y", "forester",
                "cherokee", "rogue", "tucson", "telluride", "cx-5", "cx-50", "x3", "x5", "urus", "r1s", "highlander",
                "pilot", "escape", "santa fe", "sorento", "pathfinder", "murano", "traverse", "expedition", "yukon",
                "escalade", "q5", "q7", "gx", "rx", "nx", "atlas", "tiguan", "kona", "seltos", "sportage", "crosstrek",
                "ascent", "4xe", "bronco sport", "blazer", "trax", "durango", "palisade", "polestar 3"]),
    ]

    /// A body style from the label alone, for cards without a generated one. Defaults to sedan.
    public static func guess(label: String, make: String? = nil, model: String? = nil) -> CarBodyStyle {
        let text = " " + [label, model ?? ""].joined(separator: " ").lowercased()
            .replacingOccurrences(of: #"[^a-z0-9\-]+"#, with: " ", options: .regularExpression) + " "
        for (style, words) in keywords where words.contains(where: { text.contains(" \($0) ") }) {
            return style
        }
        return .sedan
    }
}

/// A card's print finish, after the Pokémon TCG's: what the card looks like and how its foil shimmers.
public enum CardFinish: String, Codable, Hashable, Sendable, CaseIterable, Comparable {
    /// Matte, no foil (commons).
    case plain
    /// Foil on the frame, not the art (uncommons).
    case reverseHolo
    /// Foil in the art window (rares).
    case holo
    /// Art across the whole card, etched foil (cars the camera's AI discovered).
    case fullArt
    /// Special illustration rare: a full illustrated scene, rainbow foil, gold border (rares seen exactly once).
    case specialIllustration

    /// The card for a caught car, or `nil` while it's uncaught (an empty binder slot).
    public init?(for item: CollectionItem) {
        guard item.isCaught else { return nil }
        if item.tier == .rare && item.count == 1 {
            self = .specialIllustration
        } else if item.origin == .discovered {
            self = .fullArt
        } else {
            switch item.tier {
            case .rare: self = .holo
            case .uncommon: self = .reverseHolo
            case .common, .uncaught, .unknown: self = .plain
            }
        }
    }

    public var title: String {
        switch self {
        case .plain: "Plain"
        case .reverseHolo: "Reverse Holo"
        case .holo: "Holo"
        case .fullArt: "Full Art"
        case .specialIllustration: "Special Illustration"
        }
    }

    private var order: Int { CardFinish.allCases.firstIndex(of: self) ?? 0 }
    public static func < (a: CardFinish, b: CardFinish) -> Bool { a.order < b.order }
}

/// A car paint color from the cloud's color word ("silver", "dark blue", ...), in sRGB 0–1.
public struct PaintColor: Codable, Hashable, Sendable {
    public var red: Double
    public var green: Double
    public var blue: Double
    /// Metallic look (silver, gray, gold) vs. solid.
    public var metallic: Bool

    public init(red: Double, green: Double, blue: Double, metallic: Bool = false) {
        self.red = red
        self.green = green
        self.blue = blue
        self.metallic = metallic
    }

    /// For cars whose color isn't known.
    public static let neutral = PaintColor(red: 0.62, green: 0.64, blue: 0.68, metallic: true)

    private static let named: [(String, PaintColor)] = [
        ("white", .init(red: 0.93, green: 0.93, blue: 0.92)),
        ("black", .init(red: 0.07, green: 0.07, blue: 0.08)),
        ("silver", .init(red: 0.75, green: 0.76, blue: 0.78, metallic: true)),
        ("gray", .init(red: 0.42, green: 0.43, blue: 0.45, metallic: true)),
        ("grey", .init(red: 0.42, green: 0.43, blue: 0.45, metallic: true)),
        ("charcoal", .init(red: 0.22, green: 0.23, blue: 0.25, metallic: true)),
        ("blue", .init(red: 0.10, green: 0.28, blue: 0.70)),
        ("navy", .init(red: 0.08, green: 0.12, blue: 0.32)),
        ("red", .init(red: 0.72, green: 0.08, blue: 0.08)),
        ("maroon", .init(red: 0.40, green: 0.05, blue: 0.08)),
        ("burgundy", .init(red: 0.40, green: 0.05, blue: 0.10)),
        ("green", .init(red: 0.12, green: 0.42, blue: 0.22)),
        ("beige", .init(red: 0.80, green: 0.73, blue: 0.60)),
        ("tan", .init(red: 0.72, green: 0.60, blue: 0.44)),
        ("brown", .init(red: 0.36, green: 0.22, blue: 0.13)),
        ("gold", .init(red: 0.80, green: 0.64, blue: 0.30, metallic: true)),
        ("yellow", .init(red: 0.95, green: 0.78, blue: 0.10)),
        ("orange", .init(red: 0.92, green: 0.42, blue: 0.08)),
        ("purple", .init(red: 0.38, green: 0.18, blue: 0.55)),
        ("pink", .init(red: 0.92, green: 0.55, blue: 0.66)),
    ]

    /// `nil` for no color or a word it doesn't know. "dark"/"light" darken or lighten the base color.
    public static func from(_ name: String?) -> PaintColor? {
        guard let name else { return nil }
        let words = name.lowercased().split { !$0.isLetter }.map(String.init)
        guard let base = words.reversed().lazy.compactMap({ w in named.first { $0.0 == w }?.1 }).first else {
            return nil
        }
        if words.contains("dark") { return base.mixed(with: 0, by: 0.45) }
        if words.contains("light") || words.contains("pale") { return base.mixed(with: 1, by: 0.4) }
        return base
    }

    private func mixed(with gray: Double, by t: Double) -> PaintColor {
        PaintColor(red: red + (gray - red) * t, green: green + (gray - green) * t, blue: blue + (gray - blue) * t,
                   metallic: metallic)
    }
}
