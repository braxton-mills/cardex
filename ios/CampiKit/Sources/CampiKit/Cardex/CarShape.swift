import Foundation

/// The proportions of a Cardex card's 3D car, in meters: enough to give a Wrangler, a Mustang and a Model 3 their
/// own silhouettes. Built into a low-poly mesh by `CarMesh`. Presets come from the body style, then per-model
/// tweaks for well-known cars (`CarShape.for(label:make:model:style:)`).
public struct CarShape: Hashable, Sendable {
    /// The side profile above the beltline.
    public enum Roof: String, Hashable, Sendable {
        /// Separate trunk: sedans.
        case notchback
        /// One long slope from the roof to the tail: coupes, liftbacks, the Cybertruck.
        case fastback
        /// Near-vertical rear: hatchbacks, wagons, SUVs.
        case hatch
        /// Cab and an open bed: pickups.
        case pickup
        /// One box from a short nose: minivans, cargo vans, buses.
        case van
        /// A cab and a taller cargo box behind it.
        case boxTruck
        /// Nothing above the tank: motorcycles.
        case open
    }

    public enum Extra: String, Hashable, Sendable, CaseIterable {
        case spareTire, roofRack, spoiler, ducktail, flares, lightBar, roundLights, glassRoof, noGrille, bigGrille,
             lightStrip, mixerDrum, sideSteps
    }

    public var roof: Roof
    public var length: Float
    public var width: Float
    public var height: Float
    public var clearance: Float
    public var wheelDiameter: Float
    public var wheelbase: Float
    /// Front axle to nose.
    public var frontOverhang: Float
    /// Body side height (to the shoulder), as a fraction of `height`.
    public var belt: Float
    /// Hood height at the nose, as a fraction of `height`.
    public var nose: Float
    /// Nose to windshield base.
    public var hood: Float
    /// Horizontal run of the windshield.
    public var windshield: Float
    /// Flat roof length.
    public var roofLength: Float
    /// Horizontal run of the rear window or hatch (pickup: the bed).
    public var rear: Float
    /// Height of the trunk deck, tailgate or tail, as a fraction of `height`.
    public var deck: Float
    /// Share of the rear slope that's glass (the rest is trunk lid or tonneau).
    public var rearGlass: Float
    /// Roof width as a fraction of `width` (tumblehome).
    public var roofWidth: Float
    /// Pillars between the front doors and the rear window (1 for most cars, more for vans and buses).
    public var pillars: Int
    /// For `.boxTruck`: the cargo box's height, as a fraction of `height` (the cab is shorter).
    public var cabHeight: Float
    public var extras: Set<Extra>

    public init(roof: Roof, length: Float, width: Float, height: Float, clearance: Float, wheelDiameter: Float,
                wheelbase: Float, frontOverhang: Float, belt: Float, nose: Float, hood: Float, windshield: Float,
                roofLength: Float, rear: Float, deck: Float, rearGlass: Float = 1, roofWidth: Float = 0.78,
                pillars: Int = 1, cabHeight: Float = 1, extras: Set<Extra> = []) {
        self.roof = roof
        self.length = length
        self.width = width
        self.height = height
        self.clearance = clearance
        self.wheelDiameter = wheelDiameter
        self.wheelbase = wheelbase
        self.frontOverhang = frontOverhang
        self.belt = belt
        self.nose = nose
        self.hood = hood
        self.windshield = windshield
        self.roofLength = roofLength
        self.rear = rear
        self.deck = deck
        self.rearGlass = rearGlass
        self.roofWidth = roofWidth
        self.pillars = pillars
        self.cabHeight = cabHeight
        self.extras = extras
    }

    /// The body length the profile pieces add up to; `length` minus this is trunk/tail.
    var profileLength: Float { hood + windshield + roofLength + rear }

    // MARK: Presets

    /// A typical car of the style.
    public static func preset(_ style: CarBodyStyle) -> CarShape {
        switch style {
        case .sedan:
            CarShape(roof: .notchback, length: 4.8, width: 1.84, height: 1.44, clearance: 0.15, wheelDiameter: 0.68,
                     wheelbase: 2.8, frontOverhang: 0.95, belt: 0.6, nose: 0.47, hood: 1.35, windshield: 0.75,
                     roofLength: 1.15, rear: 0.62, deck: 0.66)
        case .coupe:
            CarShape(roof: .fastback, length: 4.5, width: 1.84, height: 1.32, clearance: 0.13, wheelDiameter: 0.7,
                     wheelbase: 2.6, frontOverhang: 0.9, belt: 0.6, nose: 0.45, hood: 1.6, windshield: 0.7,
                     roofLength: 0.6, rear: 1.2, deck: 0.66, rearGlass: 0.65, roofWidth: 0.74)
        case .supercar:
            CarShape(roof: .fastback, length: 4.6, width: 1.98, height: 1.18, clearance: 0.11, wheelDiameter: 0.72,
                     wheelbase: 2.65, frontOverhang: 1.0, belt: 0.58, nose: 0.36, hood: 1.15, windshield: 0.95,
                     roofLength: 0.45, rear: 1.6, deck: 0.7, rearGlass: 0.35, roofWidth: 0.66,
                     extras: [.spoiler, .lightStrip])
        case .hatchback:
            CarShape(roof: .hatch, length: 4.3, width: 1.79, height: 1.46, clearance: 0.15, wheelDiameter: 0.66,
                     wheelbase: 2.65, frontOverhang: 0.9, belt: 0.58, nose: 0.48, hood: 1.15, windshield: 0.8,
                     roofLength: 1.55, rear: 0.4, deck: 0.62)
        case .wagon:
            CarShape(roof: .hatch, length: 4.85, width: 1.86, height: 1.6, clearance: 0.2, wheelDiameter: 0.7,
                     wheelbase: 2.75, frontOverhang: 0.95, belt: 0.58, nose: 0.47, hood: 1.25, windshield: 0.75,
                     roofLength: 2.2, rear: 0.35, deck: 0.6, pillars: 2, extras: [.roofRack])
        case .suv:
            CarShape(roof: .hatch, length: 4.65, width: 1.87, height: 1.68, clearance: 0.2, wheelDiameter: 0.74,
                     wheelbase: 2.7, frontOverhang: 0.92, belt: 0.6, nose: 0.55, hood: 1.15, windshield: 0.72,
                     roofLength: 1.85, rear: 0.38, deck: 0.62, pillars: 2)
        case .fullSizeSUV:
            CarShape(roof: .hatch, length: 5.35, width: 2.06, height: 1.93, clearance: 0.24, wheelDiameter: 0.82,
                     wheelbase: 3.07, frontOverhang: 0.95, belt: 0.6, nose: 0.6, hood: 1.4, windshield: 0.7,
                     roofLength: 2.6, rear: 0.25, deck: 0.62, roofWidth: 0.84, pillars: 2,
                     extras: [.bigGrille, .roofRack, .sideSteps])
        case .offRoader:
            CarShape(roof: .hatch, length: 4.8, width: 1.92, height: 1.84, clearance: 0.27, wheelDiameter: 0.84,
                     wheelbase: 2.95, frontOverhang: 0.75, belt: 0.58, nose: 0.6, hood: 1.25, windshield: 0.32,
                     roofLength: 2.6, rear: 0.12, deck: 0.62, roofWidth: 0.88, pillars: 2,
                     extras: [.spareTire, .flares, .roundLights])
        case .pickup:
            CarShape(roof: .pickup, length: 5.85, width: 2.03, height: 1.95, clearance: 0.24, wheelDiameter: 0.82,
                     wheelbase: 3.68, frontOverhang: 0.95, belt: 0.62, nose: 0.66, hood: 1.6, windshield: 0.62,
                     roofLength: 1.25, rear: 2.0, deck: 0.62, roofWidth: 0.86,
                     extras: [.bigGrille])
        case .van:
            CarShape(roof: .van, length: 5.15, width: 1.99, height: 1.76, clearance: 0.17, wheelDiameter: 0.72,
                     wheelbase: 3.0, frontOverhang: 1.0, belt: 0.55, nose: 0.5, hood: 0.95, windshield: 1.05,
                     roofLength: 2.85, rear: 0.3, deck: 0.6, roofWidth: 0.86, pillars: 2)
        case .cargoVan:
            CarShape(roof: .van, length: 5.9, width: 2.02, height: 2.5, clearance: 0.2, wheelDiameter: 0.74,
                     wheelbase: 3.65, frontOverhang: 0.95, belt: 0.45, nose: 0.38, hood: 0.8, windshield: 0.75,
                     roofLength: 4.2, rear: 0.12, deck: 0.62, rearGlass: 0, roofWidth: 0.92, pillars: 0)
        case .boxTruck:
            CarShape(roof: .boxTruck, length: 7.4, width: 2.4, height: 3.4, clearance: 0.32, wheelDiameter: 0.98,
                     wheelbase: 4.4, frontOverhang: 1.1, belt: 0.36, nose: 0.36, hood: 0.7, windshield: 0.45,
                     roofLength: 0.9, rear: 5.2, deck: 0.36, roofWidth: 0.9, pillars: 0, cabHeight: 0.8,
                     extras: [.bigGrille])
        case .bus:
            CarShape(roof: .van, length: 11.5, width: 2.45, height: 3.2, clearance: 0.3, wheelDiameter: 1.0,
                     wheelbase: 6.4, frontOverhang: 2.0, belt: 0.36, nose: 0.36, hood: 0.35, windshield: 0.35,
                     roofLength: 10.65, rear: 0.15, deck: 0.62, roofWidth: 0.95, pillars: 7)
        case .motorcycle:
            CarShape(roof: .open, length: 1.55, width: 0.3, height: 1.12, clearance: 0.42, wheelDiameter: 0.64,
                     wheelbase: 1.45, frontOverhang: -0.1, belt: 0.74, nose: 0.66, hood: 0.35, windshield: 0.25,
                     roofLength: 0.6, rear: 0.35, deck: 0.62, roofWidth: 0.9, pillars: 0)
        }
    }

    /// The shape for a collection label: a known model's tuned proportions, else its body style's preset.
    public static func `for`(label: String, make: String? = nil, model: String? = nil,
                             style: CarBodyStyle? = nil) -> CarShape {
        let text = CarBodyStyle.words(label, model)
        if let known = known.first(where: { entry in entry.keys.contains { text.contains(" \($0) ") } }) {
            var shape = preset(known.style)
            known.tune(&shape)
            return shape
        }
        return preset(style ?? CarBodyStyle.guess(label: label, make: make, model: model))
    }

    private struct Known {
        var keys: [String]
        var style: CarBodyStyle
        var tune: @Sendable (inout CarShape) -> Void
    }

    /// Proportions of well-known models (approximate public dimensions, tuned by eye). First match wins.
    private static let known: [Known] = [
        // pickups
        Known(keys: ["cybertruck"], style: .pickup) { s in
            s = CarShape(roof: .fastback, length: 5.68, width: 2.03, height: 1.79, clearance: 0.27,
                         wheelDiameter: 0.88, wheelbase: 3.81, frontOverhang: 0.95, belt: 0.55, nose: 0.5,
                         hood: 0.5, windshield: 2.0, roofLength: 0.05, rear: 3.13, deck: 0.62, rearGlass: 0.15,
                         roofWidth: 0.7, extras: [.lightStrip, .noGrille, .flares])
        },
        Known(keys: ["tacoma", "ranger", "colorado", "frontier", "gladiator", "maverick"], style: .pickup) { s in
            s.length = 5.4; s.width = 1.91; s.height = 1.8; s.wheelbase = 3.3; s.rear = 1.55; s.hood = 1.45
            s.wheelDiameter = 0.78
        },
        Known(keys: ["r1t"], style: .pickup) { s in
            s.length = 5.5; s.height = 1.82; s.rear = 1.4; s.extras = [.roundLights, .noGrille, .lightStrip]
        },
        Known(keys: ["f-150", "f-250", "silverado", "sierra", "ram", "tundra", "titan"], style: .pickup) { _ in },
        // off-roaders
        Known(keys: ["wrangler"], style: .offRoader) { s in
            s.length = 4.78; s.width = 1.89; s.height = 1.85; s.hood = 1.3; s.windshield = 0.22; s.belt = 0.56
            s.extras = [.spareTire, .flares, .roundLights, .roofRack]
        },
        Known(keys: ["bronco"], style: .offRoader) { s in
            s.length = 4.81; s.width = 1.93; s.height = 1.83; s.windshield = 0.35
            s.extras = [.spareTire, .flares, .roundLights]
        },
        Known(keys: ["4runner", "land cruiser", "defender", "g-class"], style: .offRoader) { s in
            s.windshield = 0.55; s.extras = [.roofRack, .flares]
        },
        Known(keys: ["r1s"], style: .suv) { s in
            s.length = 5.1; s.width = 2.08; s.height = 1.96; s.clearance = 0.27; s.wheelDiameter = 0.84
            s.wheelbase = 3.08; s.hood = 1.2; s.roofLength = 2.65; s.roofWidth = 0.86
            s.extras = [.roundLights, .noGrille, .lightStrip]
        },
        // full-size SUVs
        Known(keys: ["tahoe", "suburban", "yukon", "expedition", "escalade", "sequoia"], style: .fullSizeSUV) { _ in },
        // crossovers and SUVs
        Known(keys: ["model y"], style: .suv) { s in
            s.length = 4.75; s.height = 1.62; s.hood = 1.05; s.windshield = 1.0; s.roofLength = 1.3; s.rear = 0.85
            s.roof = .fastback; s.rearGlass = 0.7; s.deck = 0.66; s.nose = 0.46
            s.extras = [.glassRoof, .noGrille, .lightStrip]
        },
        Known(keys: ["urus"], style: .suv) { s in
            s.length = 5.11; s.width = 2.02; s.height = 1.64; s.wheelDiameter = 0.8; s.roof = .fastback
            s.windshield = 0.95; s.roofLength = 1.2; s.rear = 1.05; s.rearGlass = 0.55; s.roofWidth = 0.72
            s.nose = 0.48; s.extras = [.spoiler]
        },
        Known(keys: ["x5", "x3", "q7", "q5", "gx", "rx"], style: .suv) { s in
            s.length = 4.92; s.width = 2.0; s.height = 1.75; s.wheelDiameter = 0.78; s.extras = [.bigGrille]
        },
        Known(keys: ["telluride", "palisade", "explorer", "pilot", "highlander", "atlas", "traverse", "durango",
                     "grand cherokee"], style: .suv) { s in
            s.length = 5.0; s.width = 1.99; s.height = 1.76; s.wheelbase = 2.9; s.roofLength = 2.3; s.roofWidth = 0.84
            s.wheelDiameter = 0.78; s.extras = [.roofRack]
        },
        Known(keys: ["forester"], style: .suv) { s in
            s.height = 1.73; s.roofWidth = 0.84; s.windshield = 0.62; s.extras = [.roofRack]
        },
        Known(keys: ["rav4", "cr-v", "rogue", "equinox", "tucson", "cx-5", "escape", "sportage", "santa fe"],
              style: .suv) { _ in },
        // wagons
        Known(keys: ["outback", "v60", "v90", "allroad"], style: .wagon) { _ in },
        // vans
        Known(keys: ["odyssey", "sienna", "pacifica", "carnival", "caravan"], style: .van) { _ in },
        Known(keys: ["sprinter", "transit", "promaster"], style: .cargoVan) { _ in },
        // sports cars
        Known(keys: ["911"], style: .coupe) { s in
            s.length = 4.52; s.width = 1.85; s.height = 1.3; s.hood = 1.2; s.nose = 0.4; s.windshield = 0.75
            s.roofLength = 0.4; s.rear = 1.6; s.deck = 0.62; s.rearGlass = 0.45; s.extras = [.ducktail, .roundLights,
                                                                                          .noGrille, .lightStrip]
        },
        Known(keys: ["mustang", "camaro", "challenger"], style: .coupe) { s in
            s.length = 4.81; s.width = 1.92; s.height = 1.39; s.hood = 1.85; s.windshield = 0.65
            s.roofLength = 0.55; s.rear = 0.95; s.rearGlass = 0.75; s.nose = 0.5; s.belt = 0.62
            s.extras = [.spoiler, .bigGrille]
        },
        Known(keys: ["corvette"], style: .supercar) { _ in },
        Known(keys: ["roma", "portofino"], style: .coupe) { s in
            s.length = 4.66; s.width = 1.97; s.height = 1.3; s.hood = 1.9; s.nose = 0.4; s.roofLength = 0.45
            s.rear = 1.1; s.roofWidth = 0.7
        },
        Known(keys: ["huracan", "aventador", "488", "f8", "296", "720s", "r8"], style: .supercar) { _ in },
        Known(keys: ["gr86", "brz", "supra", "miata", "mx-5"], style: .coupe) { s in
            s.length = 4.26; s.width = 1.78; s.height = 1.31; s.wheelbase = 2.58; s.hood = 1.55; s.rear = 1.0
            s.extras = [.ducktail]
        },
        // sedans
        Known(keys: ["model 3"], style: .sedan) { s in
            s.length = 4.72; s.width = 1.85; s.height = 1.44; s.wheelbase = 2.88; s.hood = 1.05; s.nose = 0.44
            s.windshield = 1.0; s.roofLength = 1.05; s.rear = 1.0; s.roof = .fastback; s.rearGlass = 0.7
            s.extras = [.glassRoof, .noGrille]
        },
        Known(keys: ["lucid air", "air"], style: .sedan) { s in
            s.length = 4.98; s.width = 1.94; s.height = 1.41; s.wheelbase = 2.96; s.hood = 1.0; s.nose = 0.42
            s.windshield = 1.15; s.roofLength = 1.2; s.rear = 1.05; s.roof = .fastback; s.rearGlass = 0.65
            s.extras = [.glassRoof, .noGrille, .lightStrip]
        },
        Known(keys: ["polestar 2", "polestar"], style: .sedan) { s in
            s.length = 4.61; s.height = 1.48; s.clearance = 0.18; s.roof = .fastback; s.rear = 0.95
            s.rearGlass = 0.7; s.roofLength = 1.05; s.extras = [.noGrille]
        },
        Known(keys: ["civic", "mazda3", "mazda 3", "elantra", "sentra", "jetta"], style: .sedan) { s in
            s.length = 4.67; s.height = 1.41; s.rear = 0.85; s.deck = 0.65; s.roofLength = 0.95
        },
        Known(keys: ["accord", "camry", "altima", "sonata", "k5", "malibu", "fusion"], style: .sedan) { s in
            s.length = 4.9; s.width = 1.86; s.height = 1.45; s.wheelbase = 2.83
        },
        Known(keys: ["corolla"], style: .sedan) { s in
            s.length = 4.63; s.width = 1.78; s.height = 1.44; s.wheelbase = 2.7
        },
        // work trucks and buses
        Known(keys: ["school bus"], style: .bus) { s in
            s.roof = .boxTruck; s.hood = 1.4; s.nose = 0.4; s.windshield = 0.3; s.roofLength = 0.2; s.cabHeight = 1
            s.rear = 8.9; s.frontOverhang = 2.2; s.pillars = 8; s.belt = 0.45; s.extras = [.bigGrille]
        },
        Known(keys: ["city bus", "bus"], style: .bus) { s in s.belt = 0.42 },
        Known(keys: ["concrete mixer", "mixer"], style: .boxTruck) { s in
            s.rear = 5.0; s.extras = [.mixerDrum, .bigGrille]; s.cabHeight = 1; s.deck = 0.3
        },
        Known(keys: ["semi", "semi truck"], style: .boxTruck) { s in
            s.length = 6.4; s.height = 4.0; s.hood = 1.7; s.nose = 0.42; s.windshield = 0.4; s.roofLength = 1.2
            s.rear = 2.4; s.cabHeight = 0.76; s.belt = 0.47; s.wheelbase = 3.9; s.frontOverhang = 0.9
            s.extras = [.bigGrille, .sideSteps]
        },
        Known(keys: ["dump truck", "garbage truck", "box truck", "excavator"], style: .boxTruck) { _ in },
        Known(keys: ["motorcycle", "motorbike"], style: .motorcycle) { _ in },
    ]
}

extension CarBodyStyle {
    /// " word word " for whole-word matching: lowercased, punctuation other than "-" removed.
    static func words(_ label: String, _ model: String?) -> String {
        " " + [label, model ?? ""].joined(separator: " ").lowercased()
            .replacingOccurrences(of: #"[^a-z0-9\-]+"#, with: " ", options: .regularExpression) + " "
    }
}
