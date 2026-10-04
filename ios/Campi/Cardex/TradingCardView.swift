import CampiKit
import SwiftUI

/// Opens a label's trading card in the inspector.
struct CardexCardRoute: Hashable {
    let item: CollectionItem
}

/// A caught car as a Pokémon-style trading card: generated name and ratings, real catch stats, the 3D car in the
/// art window, and a finish set by rarity (`CardFinish`). Drawn at a fixed 300 × 419 pt (63:88) and scaled to fit,
/// like a printed card: thumbnails in the binder, full size in the inspector.
struct TradingCardView: View {
    @Environment(AppModel.self) private var app
    let item: CollectionItem
    /// Collector number: the label's position in the collection, and the collection's size.
    var number: Int = 0
    var total: Int = 0
    /// -1...1 per axis while the card is held; moves the foil and the glare.
    var tilt: CGPoint = .zero
    /// The live 3D car instead of the still snapshot (the inspector).
    var live = false
    var spins = true

    static let size = CGSize(width: 300, height: 419)

    var body: some View {
        Color.clear
            .aspectRatio(Self.size.width / Self.size.height, contentMode: .fit)
            .overlay {
                GeometryReader { geo in
                    face
                        .frame(width: Self.size.width, height: Self.size.height)
                        .clipShape(.rect(cornerRadius: 14, style: .continuous))
                        .scaleEffect(geo.size.width / Self.size.width)
                        .frame(width: geo.size.width, height: geo.size.height)
                }
            }
            // the printed card is decoration for VoiceOver: one summary instead of dozens of tiny labels
            .accessibilityRepresentation { Text(accessibilityText) }
            .accessibilityIdentifier("cardex.trading")
            .task(id: item.label) {
                await app.cardex.load(item)
                await app.cardex.loadPaint(item, app: app)
            }
    }

    // MARK: - Data

    private var card: CardexStore.Card? { app.cardex.card(for: item.label) }
    private var finish: CardFinish {
        #if DEBUG
        if let forced = Self.forcedFinish { return forced }
        #endif
        return CardFinish(for: item) ?? .plain
    }

    #if DEBUG
    /// `-cardexForceFinish fullArt`: every card in one finish, for reviewing finishes the data doesn't produce.
    private static let forcedFinish: CardFinish? = {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "-cardexForceFinish"), i + 1 < args.count else { return nil }
        return CardFinish(rawValue: args[i + 1])
    }()
    #endif
    private var style: CarBodyStyle {
        card?.text.bodyStyle ?? CarBodyStyle.guess(label: item.label, make: item.make, model: item.model)
    }
    private var energy: Energy { Energy(style) }
    private var name: String { card?.text.displayName ?? item.label }

    private var accessibilityText: String {
        var parts = ["\(name) trading card", finish.title, item.tier.title, "seen \(item.count) times"]
        if let ratings = card?.text.ratings, !ratings.isEmpty {
            parts.append(ratings.map { "\($0.name) \($0.value) of 10" }.joined(separator: ", "))
        }
        if let flavor = card?.text.flavor { parts.append(flavor) }
        return parts.joined(separator: ". ")
    }

    // MARK: - Faces

    @ViewBuilder private var face: some View {
        switch finish {
        case .plain, .reverseHolo, .holo: standardFace
        case .fullArt: fullArtFace
        case .specialIllustration: illustrationFace
        }
    }

    /// Frame, header, art window, attacks, flavor box, footer: plain, reverse holo and holo.
    private var standardFace: some View {
        let shape = RoundedRectangle(cornerRadius: 14, style: .continuous)
        let inner = RoundedRectangle(cornerRadius: 8, style: .continuous)
        return ZStack {
            shape.fill(LinearGradient(colors: [Color(hex: 0xF7D94C), Color(hex: 0xE3B021)],
                                      startPoint: .topLeading, endPoint: .bottomTrailing))
            ZStack {
                inner.fill(LinearGradient(colors: [energy.light, energy.color], startPoint: .top, endPoint: .bottom))
                if finish == .reverseHolo { foil(style: 1, strength: 1.1).clipShape(inner) }
                VStack(spacing: 0) {
                    header(onDark: false).padding(.horizontal, 10).padding(.top, 6)
                    artWindow
                        .frame(height: 158)
                        .clipShape(.rect(cornerRadius: 3))
                        .overlay { RoundedRectangle(cornerRadius: 3).strokeBorder(silverRim, lineWidth: 3) }
                        .shadow(color: .black.opacity(0.25), radius: 2, y: 1)
                        .padding(.horizontal, 10)
                        .padding(.top, 4)
                    infoStrip.padding(.horizontal, 22).padding(.top, -1)
                    attacks(onDark: false).padding(.horizontal, 14).padding(.top, 8)
                    Spacer(minLength: 4)
                    flavorBox(onDark: false).padding(.horizontal, 12)
                    footer(onDark: false).padding(.horizontal, 12).padding(.vertical, 5)
                }
            }
            .padding(9)
            glare(shape)
        }
        .frame(width: Self.size.width, height: Self.size.height)
        .clipShape(shape)
    }

    /// The art across the whole card, text on scrims, etched foil over everything.
    private var fullArtFace: some View {
        let shape = RoundedRectangle(cornerRadius: 14, style: .continuous)
        return ZStack {
            LinearGradient(colors: [energy.light, energy.color, energy.color.mix(with: .black, by: 0.45)],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
            sunburst.opacity(0.18)
            foil(style: 2, strength: 1.4)
            // in an overlay: art bigger than the card mustn't widen the text layout
            Color.clear.overlay { carImage.frame(width: 360, height: 240).offset(y: -18) }
            foil(style: 2, strength: 0.4)
            VStack(spacing: 0) {
                header(onDark: true).padding(.horizontal, 16).padding(.top, 14)
                Spacer()
                VStack(spacing: 6) {
                    attacks(onDark: true)
                    flavorBox(onDark: true)
                    footer(onDark: true)
                }
                .padding(12)
                .background(.black.opacity(0.42), in: .rect(cornerRadius: 10))
                .padding(10)
            }
            shape.strokeBorder(LinearGradient(colors: [.white.opacity(0.9), Color(hex: 0xB8C0CC), .white.opacity(0.7)],
                                              startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 7)
            glare(shape)
        }
        .frame(width: Self.size.width, height: Self.size.height)
        .clipShape(shape)
    }

    /// Special illustration rare: an illustrated scene around the car, rainbow glitter foil, gold border.
    private var illustrationFace: some View {
        let shape = RoundedRectangle(cornerRadius: 14, style: .continuous)
        return ZStack {
            IllustratedScene(energy: energy)
            foil(style: 3, strength: 1.2)
            Color.clear.overlay { carImage.frame(width: 400, height: 266).offset(y: 34) }
            foil(style: 3, strength: 0.45)
            VStack(spacing: 0) {
                header(onDark: true).padding(.horizontal, 16).padding(.top, 14)
                Spacer()
                VStack(spacing: 6) {
                    if let flavor = card?.text.flavor {
                        Text("“\(flavor)”").font(.system(size: 10, weight: .medium).italic())
                            .foregroundStyle(.white).multilineTextAlignment(.center).lineLimit(3)
                    }
                    footer(onDark: true)
                }
                .padding(10)
                .background(.black.opacity(0.38), in: .rect(cornerRadius: 10))
                .padding(10)
            }
            shape.strokeBorder(LinearGradient(colors: [Color(hex: 0xFFF1B0), Color(hex: 0xD4A017), Color(hex: 0xFFE680),
                                                       Color(hex: 0xB8860B)],
                                              startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 8)
            glare(shape)
        }
        .frame(width: Self.size.width, height: Self.size.height)
        .clipShape(shape)
    }

    // MARK: - Parts

    private func header(onDark: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            VStack(alignment: .leading, spacing: 0) {
                Text(style.title.uppercased())
                    .font(.system(size: 7, weight: .heavy)).tracking(0.6)
                    .padding(.horizontal, 5).padding(.vertical, 1)
                    .background(onDark ? AnyShapeStyle(.white.opacity(0.25)) : AnyShapeStyle(silverRim), in: .capsule)
                Text(name)
                    .font(.system(size: 18, weight: .heavy))
                    .lineLimit(1).minimumScaleFactor(0.6)
            }
            Spacer(minLength: 4)
            Text("SEEN").font(.system(size: 8, weight: .heavy))
            Text("×\(item.count)").font(.system(size: 20, weight: .black)).monospacedDigit()
            EnergyBadge(energy: energy, size: 20)
                .alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + 5 }
        }
        .foregroundStyle(onDark ? Color.white : Color.black)
        .shadow(color: onDark ? .black.opacity(0.5) : .clear, radius: 2)
    }

    /// The car on a backdrop in its energy's colors, with a floor shadow.
    private var artWindow: some View {
        ZStack {
            RadialGradient(colors: [energy.light.mix(with: .white, by: 0.5), energy.color], center: .init(x: 0.5, y: 0.35),
                           startRadius: 10, endRadius: 220)
            sunburst.opacity(0.12)
            if finish == .holo { foil(style: 1, strength: 1.5) }
            Ellipse().fill(.black.opacity(0.28)).frame(width: 180, height: 26).blur(radius: 8).offset(y: 52)
            carImage
            if finish == .holo { foil(style: 1, strength: 0.35) }
        }
    }

    @ViewBuilder private var carImage: some View {
        let paint = app.cardex.paint(for: item.label)
        if live {
            CarModelView(style: style, paint: paint, tilt: tilt, spins: spins)
        } else {
            CarSnapshotImage(style: style, paint: paint)
        }
    }

    private var infoStrip: some View {
        let parts = [item.make, item.model].compactMap { $0 } + (item.firstSeenAt.map {
            ["First seen \($0.date.formatted(Date.FormatStyle(timeZone: $0.timeZone).month(.abbreviated).day().year()))"]
        } ?? [])
        return Text(parts.isEmpty ? item.label : parts.joined(separator: " · "))
            .font(.system(size: 8, weight: .semibold).italic())
            .lineLimit(1)
            .foregroundStyle(.black.opacity(0.75))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 2)
            .background(silverRim, in: .rect(cornerRadius: 2))
    }

    /// The ratings as attacks; real catch stats when there are no ratings (the plain fallback card).
    private func attacks(onDark: Bool) -> some View {
        let ratings = card?.text.ratings ?? []
        let rows: [(String, String, Int)] = ratings.isEmpty
            ? [("Sightings", "×\(item.count)", 1), ("Rarity", item.tier.title, 2)]
            : ratings.map { ($0.name, "\($0.value * 10)", max(1, Int((Double($0.value) / 3.4).rounded(.up)))) }
        return VStack(spacing: 0) {
            if app.cardex.generating.contains(item.label) && ratings.isEmpty {
                Text("Writing card…").font(.system(size: 11, weight: .medium).italic())
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 6)
            }
            ForEach(Array(rows.enumerated()), id: \.offset) { i, row in
                if i > 0 { Rectangle().fill(onDark ? .white.opacity(0.3) : .black.opacity(0.18)).frame(height: 0.75) }
                HStack(spacing: 8) {
                    HStack(spacing: 2) {
                        ForEach(0..<min(row.2, 3), id: \.self) { _ in EnergyBadge(energy: energy, size: 13) }
                    }
                    .frame(width: 44, alignment: .leading)
                    Text(row.0).font(.system(size: 14, weight: .bold)).lineLimit(1).minimumScaleFactor(0.7)
                    Spacer(minLength: 4)
                    Text(row.1).font(.system(size: 16, weight: .heavy)).monospacedDigit()
                }
                .padding(.vertical, 5)
            }
        }
        .foregroundStyle(onDark ? Color.white : Color.black)
    }

    @ViewBuilder private func flavorBox(onDark: Bool) -> some View {
        if let flavor = card?.text.flavor {
            Text(flavor)
                .font(.system(size: 9, weight: .medium).italic())
                .lineLimit(3)
                .multilineTextAlignment(.leading)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 7).padding(.vertical, 4)
                .foregroundStyle(onDark ? Color.white : Color.black.opacity(0.85))
                .background {
                    if !onDark {
                        RoundedRectangle(cornerRadius: 3).strokeBorder(Color(hex: 0xC9A227), lineWidth: 1.2)
                            .background(Color.white.opacity(0.35), in: .rect(cornerRadius: 3))
                    }
                }
        }
    }

    private func footer(onDark: Bool) -> some View {
        HStack(spacing: 6) {
            Text("Illus. Campi Cam").font(.system(size: 7, weight: .semibold))
            Spacer()
            if total > 0 {
                Text(String(format: "%03d/%03d", number, total)).font(.system(size: 8, weight: .bold)).monospacedDigit()
            }
            RaritySymbol(finish: finish).font(.system(size: 8, weight: .black))
        }
        .foregroundStyle(onDark ? Color.white.opacity(0.9) : Color.black.opacity(0.7))
    }

    private var silverRim: LinearGradient {
        LinearGradient(colors: [Color(hex: 0xE9EDF2), Color(hex: 0xA9B2BE), Color(hex: 0xF4F6F8), Color(hex: 0x9AA3AF)],
                       startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    private var sunburst: some View {
        Canvas { ctx, size in
            let c = CGPoint(x: size.width / 2, y: size.height * 0.42)
            let r = max(size.width, size.height)
            for i in 0..<24 where i.isMultiple(of: 2) {
                let a0 = Double(i) / 24 * 2 * .pi, a1 = Double(i + 1) / 24 * 2 * .pi
                var p = Path()
                p.move(to: c)
                p.addLine(to: CGPoint(x: c.x + r * cos(a0), y: c.y + r * sin(a0)))
                p.addLine(to: CGPoint(x: c.x + r * cos(a1), y: c.y + r * sin(a1)))
                p.closeSubpath()
                ctx.fill(p, with: .color(.white))
            }
        }
        .allowsHitTesting(false)
    }

    private func foil(style: Float, strength: Float) -> some View {
        FoilLayer(style: style, strength: strength, tilt: tilt)
    }

    /// The light reflecting off the card's surface, opposite the tilt.
    private func glare(_ shape: some Shape) -> some View {
        GeometryReader { geo in
            RadialGradient(colors: [.white.opacity(finish == .plain ? 0.22 : 0.32), .clear],
                           center: UnitPoint(x: 0.5 - tilt.x * 0.75, y: 0.5 - tilt.y * 0.75),
                           startRadius: 0, endRadius: geo.size.width * 0.75)
        }
        .blendMode(.plusLighter)
        .clipShape(shape)
        .allowsHitTesting(false)
    }
}

/// The holographic foil shader (Foil.metal) over a region, blended so the art shows through.
struct FoilLayer: View {
    let style: Float
    var strength: Float = 1
    let tilt: CGPoint

    var body: some View {
        GeometryReader { geo in
            Rectangle()
                .fill(.white)
                .colorEffect(ShaderLibrary.foil(.float2(geo.size), .float2(Float(tilt.x), Float(tilt.y)),
                                                .float(style), .float(strength)))
        }
        .blendMode(.overlay)
        .allowsHitTesting(false)
    }
}

/// The car's still render, from the snapshot cache.
struct CarSnapshotImage: View {
    let style: CarBodyStyle
    let paint: PaintColor
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let image = image ?? CarSnapshots.shared.cached(style, paint: paint) {
                Image(uiImage: image).resizable().scaledToFit()
            } else {
                Image(systemName: "car.side.fill").font(.system(size: 60)).foregroundStyle(.white.opacity(0.4))
            }
        }
        .task(id: "\(style.rawValue)-\(paint)") {
            image = await CarSnapshots.shared.image(style, paint: paint)
        }
    }
}

/// A Pokémon-style energy type per body style: the card's colors and its cost symbols.
struct Energy {
    let color: Color
    let light: Color
    let symbol: String

    init(_ style: CarBodyStyle) {
        switch style {
        case .sedan: (color, light, symbol) = (Color(hex: 0xA8A193), Color(hex: 0xE4DFD3), "star.fill")
        case .coupe: (color, light, symbol) = (Color(hex: 0xE2553A), Color(hex: 0xF7B7A3), "flame.fill")
        case .suv: (color, light, symbol) = (Color(hex: 0x3D8BD6), Color(hex: 0xA9D4F5), "drop.fill")
        case .offRoader: (color, light, symbol) = (Color(hex: 0x4E9F48), Color(hex: 0xB7E0A6), "leaf.fill")
        case .hatchback: (color, light, symbol) = (Color(hex: 0xE9BC22), Color(hex: 0xFBE79A), "bolt.fill")
        case .wagon: (color, light, symbol) = (Color(hex: 0x9C5DC4), Color(hex: 0xDCC1EE), "eye.fill")
        case .pickup: (color, light, symbol) = (Color(hex: 0xB86A38), Color(hex: 0xE9C3A2), "hand.raised.fill")
        case .van: (color, light, symbol) = (Color(hex: 0x8A97A3), Color(hex: 0xD3DAE0), "gearshape.fill")
        case .boxTruck: (color, light, symbol) = (Color(hex: 0x3C4855), Color(hex: 0x8D9AA8), "moon.fill")
        }
    }
}

struct EnergyBadge: View {
    let energy: Energy
    var size: CGFloat = 16

    var body: some View {
        Circle()
            .fill(RadialGradient(colors: [energy.light, energy.color], center: .init(x: 0.35, y: 0.3),
                                 startRadius: 0, endRadius: size * 0.7))
            .overlay { Image(systemName: energy.symbol).font(.system(size: size * 0.5, weight: .bold)).foregroundStyle(.white) }
            .overlay { Circle().strokeBorder(.black.opacity(0.25), lineWidth: 0.75) }
            .frame(width: size, height: size)
    }
}

/// ● common, ◆ uncommon, ★ rare, ★★ full art, ✦✦ gold special illustration.
struct RaritySymbol: View {
    let finish: CardFinish

    var body: some View {
        switch finish {
        case .plain: Text("●")
        case .reverseHolo: Text("◆")
        case .holo: Text("★")
        case .fullArt: Text("★★")
        case .specialIllustration: Text("✦✦").foregroundStyle(Color(hex: 0xE0B020))
        }
    }
}

/// The special illustration rare's scene: a sunset sky in the car's energy colors, hills, and a road.
struct IllustratedScene: View {
    let energy: Energy

    var body: some View {
        Canvas { ctx, size in
            let w = size.width, h = size.height
            ctx.fill(Path(CGRect(origin: .zero, size: size)), with: .linearGradient(
                Gradient(colors: [Color(hex: 0x2B1E5C), energy.color.mix(with: Color(hex: 0xFF7A59), by: 0.5),
                                  Color(hex: 0xFFC27A)]),
                startPoint: .zero, endPoint: CGPoint(x: 0, y: h * 0.62)))
            // sun
            ctx.fill(Path(ellipseIn: CGRect(x: w * 0.58, y: h * 0.26, width: w * 0.34, height: w * 0.34)),
                     with: .color(Color(hex: 0xFFF0B8).opacity(0.9)))
            // stars
            for i in 0..<28 {
                let x = Double((i * 73) % 97) / 97 * w, y = Double((i * 37) % 53) / 53 * h * 0.3
                ctx.fill(Path(ellipseIn: CGRect(x: x, y: y, width: 1.6, height: 1.6)), with: .color(.white.opacity(0.8)))
            }
            // hills, far then near
            for (i, tone) in [(0, 0.35), (1, 0.6)] {
                var p = Path()
                let base = h * (0.52 + Double(i) * 0.06)
                p.move(to: CGPoint(x: 0, y: base))
                for x in stride(from: 0.0, through: w, by: 10) {
                    p.addLine(to: CGPoint(x: x, y: base - (sin(x / w * .pi * (2.2 + Double(i)) + Double(i)) + 1) * h * 0.05))
                }
                p.addLine(to: CGPoint(x: w, y: h))
                p.addLine(to: CGPoint(x: 0, y: h))
                p.closeSubpath()
                ctx.fill(p, with: .color(energy.color.mix(with: Color(hex: 0x1A1430), by: tone)))
            }
            // road in perspective
            var road = Path()
            road.move(to: CGPoint(x: w * 0.44, y: h * 0.6))
            road.addLine(to: CGPoint(x: w * 0.56, y: h * 0.6))
            road.addLine(to: CGPoint(x: w * 1.15, y: h))
            road.addLine(to: CGPoint(x: -w * 0.15, y: h))
            road.closeSubpath()
            ctx.fill(road, with: .color(Color(hex: 0x2A2833)))
            for i in 0..<6 {
                let t0 = pow(Double(i) / 6, 1.6), t1 = pow((Double(i) + 0.5) / 6, 1.6)
                let y0 = h * 0.6 + t0 * h * 0.4, y1 = h * 0.6 + t1 * h * 0.4
                var dash = Path()
                dash.move(to: CGPoint(x: w / 2 - 0.5 - t0 * 3, y: y0))
                dash.addLine(to: CGPoint(x: w / 2 + 0.5 + t0 * 3, y: y0))
                dash.addLine(to: CGPoint(x: w / 2 + 0.5 + t1 * 3, y: y1))
                dash.addLine(to: CGPoint(x: w / 2 - 0.5 - t1 * 3, y: y1))
                ctx.fill(dash, with: .color(Color(hex: 0xFFD966)))
            }
        }
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255)
    }
}
