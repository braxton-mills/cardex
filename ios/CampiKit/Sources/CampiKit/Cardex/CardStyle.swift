import Foundation

/// The foil pattern on a card, after the Pokémon TCG's many holo treatments. Each finish has a few; a card's is
/// picked from its label, so the same car always gets the same one and a binder shows a mix.
public enum HoloPattern: String, Codable, Hashable, Sendable, CaseIterable {
    /// Plain cards: a soft gloss only.
    case gloss
    /// Diagonal rainbow bands (the classic holo).
    case sheen
    /// Scattered foil circles of different sizes.
    case cosmos
    /// Shattered-glass shards, each its own color.
    case crackedIce
    /// Fine stars that twinkle as the card turns.
    case starlight
    /// A grid of tiny sequins.
    case sequin
    /// Rings spreading out from the art.
    case ripple
    /// Reverse holo printed in the card's energy symbol.
    case energy
    /// Etched diagonal lines (full art).
    case etched
    /// Etched contour waves (full art).
    case etchedWaves
    /// Etched honeycomb (full art).
    case etchedHex
    /// Rainbow swirl with glitter (special illustration).
    case swirl
    /// Nebula clouds and stars (special illustration).
    case galaxy

    public var title: String {
        switch self {
        case .gloss: "Gloss"
        case .sheen: "Sheen"
        case .cosmos: "Cosmos"
        case .crackedIce: "Cracked Ice"
        case .starlight: "Starlight"
        case .sequin: "Sequin"
        case .ripple: "Ripple"
        case .energy: "Energy"
        case .etched: "Etched"
        case .etchedWaves: "Etched Waves"
        case .etchedHex: "Etched Hex"
        case .swirl: "Swirl"
        case .galaxy: "Galaxy"
        }
    }

    /// The patterns a finish is printed in.
    public static func options(for finish: CardFinish) -> [HoloPattern] {
        switch finish {
        case .plain: [.gloss]
        case .reverseHolo: [.sheen, .energy, .sequin, .cosmos]
        case .holo: [.sheen, .cosmos, .crackedIce, .starlight, .ripple]
        case .fullArt: [.etched, .etchedWaves, .etchedHex]
        case .specialIllustration: [.swirl, .galaxy, .starlight]
        }
    }

    public static func `for`(label: String, finish: CardFinish) -> HoloPattern {
        let options = options(for: finish)
        return options[Int(stableHash(label + "|foil") % UInt32(options.count))]
    }
}

/// The illustrated scene behind a card's car. Each body style has a few that suit it; a card's is picked from its
/// label. Special illustration rares can also land on the rarer themes.
public enum SceneTheme: String, Codable, Hashable, Sendable, CaseIterable {
    case sunsetHighway, neonCity, mountainPass, coast, desertNight, synthwave, aurora, autumnForest, space

    public var title: String {
        switch self {
        case .sunsetHighway: "Sunset Highway"
        case .neonCity: "Neon City"
        case .mountainPass: "Mountain Pass"
        case .coast: "Coast Road"
        case .desertNight: "Desert Night"
        case .synthwave: "Synthwave"
        case .aurora: "Aurora"
        case .autumnForest: "Autumn Forest"
        case .space: "Deep Space"
        }
    }

    public static func options(for style: CarBodyStyle) -> [SceneTheme] {
        switch style {
        case .sedan: [.neonCity, .sunsetHighway, .autumnForest, .coast]
        case .coupe, .supercar: [.synthwave, .neonCity, .sunsetHighway, .coast]
        case .suv, .fullSizeSUV: [.mountainPass, .autumnForest, .coast, .sunsetHighway]
        case .offRoader: [.desertNight, .mountainPass, .autumnForest, .aurora]
        case .hatchback, .wagon: [.coast, .autumnForest, .neonCity, .mountainPass]
        case .pickup: [.desertNight, .mountainPass, .autumnForest, .sunsetHighway]
        case .van: [.coast, .sunsetHighway, .autumnForest]
        case .cargoVan, .boxTruck, .bus: [.neonCity, .sunsetHighway, .mountainPass]
        case .motorcycle: [.synthwave, .desertNight, .sunsetHighway]
        }
    }

    public static func `for`(label: String, style: CarBodyStyle, finish: CardFinish) -> SceneTheme {
        let h = stableHash(label + "|scene")
        if finish == .specialIllustration, h % 5 == 0 { return h % 2 == 0 ? .space : .aurora }
        let options = options(for: style)
        return options[Int(h / 5 % UInt32(options.count))]
    }
}

/// FNV-1a: the same on every launch and device, unlike `Hasher`.
func stableHash(_ s: String) -> UInt32 {
    var h: UInt32 = 2_166_136_261
    for b in s.utf8 {
        h ^= UInt32(b)
        h = h &* 16_777_619
    }
    return h
}
