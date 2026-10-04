import CampiKit
import SwiftUI

/// The illustrated scene behind a card's car (`SceneTheme`), drawn in layers that drift with the card's tilt for
/// depth: sky first, then far, middle and near layers. Fills whatever size it's given; the ground line sits at
/// `horizon` of the height, where the car stands.
struct SceneArt: View {
    let theme: SceneTheme
    let energy: Energy
    var tilt: CGPoint = .zero
    var horizon: CGFloat = 0.62

    var body: some View {
        Canvas { ctx, size in
            var p = ScenePainter(ctx: ctx, size: size, tilt: tilt, horizon: horizon, energy: energy)
            switch theme {
            case .sunsetHighway: p.sunsetHighway()
            case .neonCity: p.neonCity()
            case .mountainPass: p.mountainPass()
            case .coast: p.coast()
            case .desertNight: p.desertNight()
            case .synthwave: p.synthwave()
            case .aurora: p.aurora()
            case .autumnForest: p.autumnForest()
            case .space: p.space()
            }
        }
        .allowsHitTesting(false)
    }
}

/// Repeatable randomness for scenery: the same scene every time it's drawn.
private struct Seeded {
    var state: UInt64
    init(_ seed: UInt64) { state = seed &* 6_364_136_223_846_793_005 &+ 1 }
    mutating func next() -> Double {
        state = state &* 6_364_136_223_846_793_005 &+ 1_442_695_040_888_963_407
        return Double(state >> 11) / Double(1 << 53)
    }
    mutating func range(_ a: Double, _ b: Double) -> Double { a + (b - a) * next() }
}

private struct ScenePainter {
    var ctx: GraphicsContext
    let size: CGSize
    let tilt: CGPoint
    let horizon: CGFloat
    let energy: Energy

    var w: CGFloat { size.width }
    var h: CGFloat { size.height }
    var groundY: CGFloat { h * horizon }

    /// Draws in a copy of the context shifted by the tilt; `depth` 0 (sky, fixed) to 1 (nearest).
    func layer(_ depth: CGFloat, _ draw: (inout GraphicsContext) -> Void) {
        var c = ctx
        c.translateBy(x: -tilt.x * depth * w * 0.035, y: -tilt.y * depth * h * 0.02)
        draw(&c)
    }

    func sky(_ colors: [Color], to y: CGFloat? = nil) {
        ctx.fill(Path(CGRect(origin: .zero, size: size)),
                 with: .linearGradient(Gradient(colors: colors), startPoint: .zero, endPoint: CGPoint(x: 0, y: y ?? groundY)))
    }

    func disc(_ c: inout GraphicsContext, _ center: CGPoint, _ r: CGFloat, _ color: Color, glow: CGFloat = 0) {
        if glow > 0 {
            c.fill(Path(ellipseIn: CGRect(x: center.x - r * glow, y: center.y - r * glow, width: 2 * r * glow,
                                          height: 2 * r * glow)),
                   with: .radialGradient(Gradient(colors: [color.opacity(0.55), color.opacity(0)]), center: center,
                                         startRadius: r * 0.8, endRadius: r * glow))
        }
        c.fill(Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: 2 * r, height: 2 * r)), with: .color(color))
    }

    func stars(_ c: inout GraphicsContext, count: Int, seed: UInt64, maxY: CGFloat, color: Color = .white) {
        var rng = Seeded(seed)
        for _ in 0..<count {
            let x = rng.range(0, w), y = rng.range(0, maxY), r = rng.range(0.4, 1.4)
            c.fill(Path(ellipseIn: CGRect(x: x, y: y, width: r * 2, height: r * 2)),
                   with: .color(color.opacity(rng.range(0.4, 1))))
        }
    }

    /// A jagged ridge from left to right, filled down to the bottom.
    func ridge(_ c: inout GraphicsContext, base: CGFloat, height: CGFloat, peaks: Int, seed: UInt64, color: Color,
               snow: Color? = nil) {
        var rng = Seeded(seed)
        var pts: [CGPoint] = [CGPoint(x: -w * 0.1, y: base)]
        let step = w * 1.2 / CGFloat(peaks * 2)
        for i in 0..<(peaks * 2 + 1) {
            let x = -w * 0.1 + CGFloat(i) * step
            let y = i.isMultiple(of: 2) ? base - height * rng.range(0.15, 0.4) : base - height * rng.range(0.65, 1)
            pts.append(CGPoint(x: x, y: y))
        }
        var p = Path()
        p.addLines(pts)
        p.addLine(to: CGPoint(x: w * 1.1, y: h))
        p.addLine(to: CGPoint(x: -w * 0.1, y: h))
        p.closeSubpath()
        c.fill(p, with: .color(color))
        if let snow {
            for i in stride(from: 2, to: pts.count - 1, by: 2) {
                let top = pts[i], l = pts[i - 1], r = pts[i + 1]
                var cap = Path()
                cap.move(to: top)
                cap.addLine(to: CGPoint(x: top.x + (l.x - top.x) * 0.3, y: top.y + (l.y - top.y) * 0.3))
                cap.addLine(to: CGPoint(x: top.x, y: top.y + (min(l.y, r.y) - top.y) * 0.22))
                cap.addLine(to: CGPoint(x: top.x + (r.x - top.x) * 0.3, y: top.y + (r.y - top.y) * 0.3))
                cap.closeSubpath()
                c.fill(cap, with: .color(snow))
            }
        }
    }

    func hills(_ c: inout GraphicsContext, base: CGFloat, amp: CGFloat, waves: Double, phase: Double, color: Color) {
        var p = Path()
        p.move(to: CGPoint(x: -w * 0.1, y: base))
        for x in stride(from: -w * 0.1, through: w * 1.1, by: 6) {
            p.addLine(to: CGPoint(x: x, y: base - (sin(Double(x / w) * .pi * waves + phase) + 1) / 2 * amp))
        }
        p.addLine(to: CGPoint(x: w * 1.1, y: h))
        p.addLine(to: CGPoint(x: -w * 0.1, y: h))
        p.closeSubpath()
        c.fill(p, with: .color(color))
    }

    /// A road running from the horizon toward the viewer.
    func road(_ c: inout GraphicsContext, color: Color, line: Color, top: CGFloat? = nil) {
        let y0 = top ?? groundY
        var r = Path()
        r.move(to: CGPoint(x: w * 0.47, y: y0))
        r.addLine(to: CGPoint(x: w * 0.53, y: y0))
        r.addLine(to: CGPoint(x: w * 1.25, y: h))
        r.addLine(to: CGPoint(x: -w * 0.25, y: h))
        r.closeSubpath()
        c.fill(r, with: .color(color))
        for i in 0..<7 {
            let t0 = pow(Double(i) / 7, 1.7), t1 = pow((Double(i) + 0.45) / 7, 1.7)
            let ya = y0 + (h - y0) * t0, yb = y0 + (h - y0) * t1
            var d = Path()
            d.move(to: CGPoint(x: w / 2 - 0.6 - t0 * 4, y: ya))
            d.addLine(to: CGPoint(x: w / 2 + 0.6 + t0 * 4, y: ya))
            d.addLine(to: CGPoint(x: w / 2 + 0.6 + t1 * 4, y: yb))
            d.addLine(to: CGPoint(x: w / 2 - 0.6 - t1 * 4, y: yb))
            d.closeSubpath()
            c.fill(d, with: .color(line))
        }
    }

    func pine(_ c: inout GraphicsContext, x: CGFloat, base: CGFloat, height: CGFloat, color: Color, snow: Bool = false) {
        for k in 0..<3 {
            let tierBase = base - height * CGFloat(k) * 0.28, half = height * (0.32 - CGFloat(k) * 0.07)
            var t = Path()
            t.move(to: CGPoint(x: x, y: tierBase - height * 0.5))
            t.addLine(to: CGPoint(x: x + half, y: tierBase))
            t.addLine(to: CGPoint(x: x - half, y: tierBase))
            t.closeSubpath()
            c.fill(t, with: .color(color))
            if snow {
                var cap = Path()
                cap.move(to: CGPoint(x: x, y: tierBase - height * 0.5))
                cap.addLine(to: CGPoint(x: x + half * 0.4, y: tierBase - height * 0.3))
                cap.addLine(to: CGPoint(x: x - half * 0.4, y: tierBase - height * 0.3))
                cap.closeSubpath()
                c.fill(cap, with: .color(.white.opacity(0.85)))
            }
        }
        c.fill(Path(CGRect(x: x - height * 0.03, y: base, width: height * 0.06, height: height * 0.08)),
               with: .color(color.mix(with: .black, by: 0.3)))
    }

    func palm(_ c: inout GraphicsContext, x: CGFloat, base: CGFloat, height: CGFloat, color: Color, lean: CGFloat) {
        var trunk = Path()
        trunk.move(to: CGPoint(x: x, y: base))
        trunk.addQuadCurve(to: CGPoint(x: x + lean, y: base - height),
                           control: CGPoint(x: x + lean * 0.2, y: base - height * 0.6))
        c.stroke(trunk, with: .color(color), style: StrokeStyle(lineWidth: height * 0.05, lineCap: .round))
        let top = CGPoint(x: x + lean, y: base - height)
        for k in 0..<6 {
            let a = Double(k) / 6 * .pi * 2 + 0.3
            var leaf = Path()
            leaf.move(to: top)
            let end = CGPoint(x: top.x + cos(a) * height * 0.45, y: top.y + sin(a) * height * 0.22 + height * 0.1)
            leaf.addQuadCurve(to: end, control: CGPoint(x: top.x + cos(a) * height * 0.25, y: top.y - height * 0.12))
            c.stroke(leaf, with: .color(color), style: StrokeStyle(lineWidth: height * 0.045, lineCap: .round))
        }
    }

    // MARK: Themes

    func sunsetHighway() {
        sky([Color(hex: 0x2B1E5C), energy.color.mix(with: Color(hex: 0xFF7A59), by: 0.55), Color(hex: 0xFFC27A)])
        layer(0.1) { c in
            stars(&c, count: 22, seed: 1, maxY: h * 0.25)
            disc(&c, CGPoint(x: w * 0.68, y: groundY - h * 0.1), w * 0.15, Color(hex: 0xFFF0B8), glow: 1.8)
        }
        layer(0.35) { c in ridge(&c, base: groundY, height: h * 0.14, peaks: 4, seed: 3, color: Color(hex: 0x5B3A6B)) }
        layer(0.6) { c in
            hills(&c, base: groundY + h * 0.03, amp: h * 0.06, waves: 2.6, phase: 1, color: Color(hex: 0x3A2850))
        }
        layer(1) { c in road(&c, color: Color(hex: 0x2A2833), line: Color(hex: 0xFFD966)) }
    }

    func neonCity() {
        sky([Color(hex: 0x0B0820), Color(hex: 0x261445), energy.color.mix(with: Color(hex: 0x6A1B9A), by: 0.6)])
        layer(0.1) { c in
            stars(&c, count: 14, seed: 5, maxY: h * 0.3)
            disc(&c, CGPoint(x: w * 0.22, y: h * 0.16), w * 0.06, Color(hex: 0xF4F1FF), glow: 2.2)
        }
        // far towers, then near towers with lit windows and neon signs
        for (depth, shade, seed) in [(0.3, 0.55, UInt64(7)), (0.65, 0.25, UInt64(8))] {
            layer(CGFloat(depth)) { c in
                var rng = Seeded(seed)
                var x = -w * 0.08
                while x < w * 1.08 {
                    let bw = w * rng.range(0.07, 0.15), bh = h * rng.range(0.16, 0.42) * (depth > 0.5 ? 1 : 1.15)
                    let rect = CGRect(x: x, y: groundY - bh, width: bw, height: bh + 2)
                    c.fill(Path(rect), with: .color(Color(hex: 0x120C26).mix(with: Color(hex: 0x6A5ACD), by: shade * 0.4)))
                    if depth > 0.5 {
                        for wy in stride(from: rect.minY + 5, to: rect.maxY - 4, by: 6) {
                            for wx in stride(from: rect.minX + 3, to: rect.maxX - 3, by: 5) where rng.next() > 0.55 {
                                c.fill(Path(CGRect(x: wx, y: wy, width: 2, height: 2.5)),
                                       with: .color(Color(hex: rng.next() > 0.3 ? 0xFFE08A : 0x7FE7FF).opacity(0.85)))
                            }
                        }
                        if rng.next() > 0.6 {
                            let sign = CGRect(x: rect.midX - bw * 0.3, y: rect.minY + bh * 0.25, width: bw * 0.6, height: 7)
                            let neon = [Color(hex: 0xFF3FD8), Color(hex: 0x33F0FF), Color(hex: 0xFFE14D)][Int(rng.next() * 3)]
                            c.stroke(Path(roundedRect: sign, cornerRadius: 3), with: .color(neon.opacity(0.4)), lineWidth: 5)
                            c.stroke(Path(roundedRect: sign, cornerRadius: 3), with: .color(neon), lineWidth: 1.5)
                        }
                    }
                    x += bw + w * rng.range(0.0, 0.02)
                }
            }
        }
        // wet street with reflections
        layer(1) { c in
            c.fill(Path(CGRect(x: -w, y: groundY, width: w * 3, height: h)),
                   with: .linearGradient(Gradient(colors: [Color(hex: 0x1B1530), Color(hex: 0x09070F)]),
                                         startPoint: CGPoint(x: 0, y: groundY), endPoint: CGPoint(x: 0, y: h)))
            var rng = Seeded(11)
            for _ in 0..<16 {
                let x = rng.range(0, w), y = rng.range(groundY + 4, h)
                let neon = [Color(hex: 0xFF3FD8), Color(hex: 0x33F0FF), Color(hex: 0xFFE08A)][Int(rng.next() * 3)]
                c.fill(Path(CGRect(x: x, y: y, width: rng.range(10, 30), height: 1.5)), with: .color(neon.opacity(0.45)))
            }
        }
    }

    func mountainPass() {
        sky([Color(hex: 0x6FB7F2), Color(hex: 0xBFE3FA), Color(hex: 0xF4FBFF)])
        layer(0.1) { c in
            for (x, y, s) in [(0.2, 0.16, 1.0), (0.75, 0.1, 0.7)] {
                let cx = w * x, cy = h * y, r = w * 0.06 * s
                for dx in [-1.0, 0, 1] {
                    c.fill(Path(ellipseIn: CGRect(x: cx + dx * r * 1.1 - r, y: cy - r * (dx == 0 ? 1.2 : 0.8), width: r * 2,
                                                  height: r * 1.6)), with: .color(.white.opacity(0.9)))
                }
            }
        }
        layer(0.25) { c in
            ridge(&c, base: groundY - h * 0.02, height: h * 0.36, peaks: 3, seed: 21, color: Color(hex: 0x7C8FB0),
                  snow: .white)
        }
        layer(0.5) { c in
            ridge(&c, base: groundY + h * 0.02, height: h * 0.2, peaks: 4, seed: 22, color: Color(hex: 0x4E6E8E),
                  snow: Color.white.opacity(0.8))
        }
        layer(0.8) { c in
            hills(&c, base: groundY + h * 0.06, amp: h * 0.05, waves: 3, phase: 0.4,
                  color: energy.color.mix(with: Color(hex: 0x2F6B3F), by: 0.7))
            var rng = Seeded(23)
            for i in 0..<9 {
                let x = w * (CGFloat(i) / 8.5) + CGFloat(rng.range(-8, 8))
                if abs(x - w / 2) < w * 0.16 { continue }
                pine(&c, x: x, base: groundY + h * 0.07, height: h * CGFloat(rng.range(0.12, 0.2)), color: Color(hex: 0x1E4D33))
            }
        }
        layer(1) { c in road(&c, color: Color(hex: 0x3A3F47), line: .white.opacity(0.85), top: groundY + h * 0.04) }
    }

    func coast() {
        sky([Color(hex: 0xFF8E72), Color(hex: 0xFFC48A), Color(hex: 0xFFE7B5)])
        layer(0.1) { c in
            disc(&c, CGPoint(x: w * 0.3, y: groundY - h * 0.08), w * 0.13, Color(hex: 0xFFF3C4), glow: 1.7)
        }
        layer(0.3) { c in
            // the sea, with glints
            c.fill(Path(CGRect(x: -w, y: groundY - h * 0.08, width: w * 3, height: h)),
                   with: .linearGradient(Gradient(colors: [Color(hex: 0x2E86AB), Color(hex: 0x1B4F72)]),
                                         startPoint: CGPoint(x: 0, y: groundY - h * 0.08), endPoint: CGPoint(x: 0, y: groundY)))
            var rng = Seeded(31)
            for _ in 0..<26 {
                let y = rng.range(Double(groundY - h * 0.075), Double(groundY - 2))
                c.fill(Path(CGRect(x: rng.range(0, w), y: y, width: rng.range(4, 16), height: 1)),
                       with: .color(Color(hex: 0xFFE7B5).opacity(0.7)))
            }
        }
        layer(0.7) { c in
            hills(&c, base: groundY + h * 0.03, amp: h * 0.03, waves: 1.5, phase: 2, color: Color(hex: 0xE9C891))
            palm(&c, x: w * 0.1, base: groundY + h * 0.05, height: h * 0.34, color: Color(hex: 0x2C3E2E), lean: w * 0.06)
            palm(&c, x: w * 0.88, base: groundY + h * 0.04, height: h * 0.28, color: Color(hex: 0x2C3E2E), lean: -w * 0.05)
        }
        layer(1) { c in road(&c, color: Color(hex: 0x4A4552), line: .white.opacity(0.9), top: groundY + h * 0.02) }
    }

    func desertNight() {
        sky([Color(hex: 0x0E1440), Color(hex: 0x3B2C6B), energy.color.mix(with: Color(hex: 0xE07A5F), by: 0.6)])
        layer(0.1) { c in
            stars(&c, count: 60, seed: 41, maxY: groundY * 0.8)
            disc(&c, CGPoint(x: w * 0.74, y: h * 0.2), w * 0.11, Color(hex: 0xFFF6E0), glow: 1.9)
            // craters
            for (dx, dy, r) in [(-0.02, -0.01, 0.025), (0.03, 0.025, 0.018), (0.01, -0.04, 0.012)] {
                c.fill(Path(ellipseIn: CGRect(x: w * (0.74 + dx) - w * r, y: h * 0.2 + w * dy - w * r, width: w * r * 2,
                                              height: w * r * 2)), with: .color(Color(hex: 0xE8DCC0)))
            }
        }
        layer(0.4) { c in
            // mesas: flat-topped buttes
            for (x, bw, bh) in [(0.08, 0.26, 0.18), (0.55, 0.36, 0.13), (0.86, 0.2, 0.22)] {
                var p = Path()
                let bx = w * x, base = groundY + 2
                p.move(to: CGPoint(x: bx - w * bw / 2, y: base))
                p.addLine(to: CGPoint(x: bx - w * bw * 0.36, y: base - h * bh))
                p.addLine(to: CGPoint(x: bx + w * bw * 0.36, y: base - h * bh))
                p.addLine(to: CGPoint(x: bx + w * bw / 2, y: base))
                p.closeSubpath()
                c.fill(p, with: .color(Color(hex: 0x5A2E4A)))
            }
        }
        layer(0.8) { c in
            c.fill(Path(CGRect(x: -w, y: groundY, width: w * 3, height: h)),
                   with: .linearGradient(Gradient(colors: [Color(hex: 0x8A4B4B), Color(hex: 0x3E2335)]),
                                         startPoint: CGPoint(x: 0, y: groundY), endPoint: CGPoint(x: 0, y: h)))
            for (x, s) in [(0.12, 1.0), (0.9, 0.8)] {
                let cx = w * x, base = groundY + h * 0.1, ch = h * 0.16 * s
                let g = Color(hex: 0x24402E)
                c.fill(Path(roundedRect: CGRect(x: cx - ch * 0.08, y: base - ch, width: ch * 0.16, height: ch), cornerRadius: ch * 0.08), with: .color(g))
                c.fill(Path(roundedRect: CGRect(x: cx - ch * 0.32, y: base - ch * 0.75, width: ch * 0.11, height: ch * 0.38), cornerRadius: ch * 0.05), with: .color(g))
                c.fill(Path(roundedRect: CGRect(x: cx - ch * 0.32, y: base - ch * 0.42, width: ch * 0.3, height: ch * 0.09), cornerRadius: ch * 0.04), with: .color(g))
                c.fill(Path(roundedRect: CGRect(x: cx + ch * 0.2, y: base - ch * 0.62, width: ch * 0.11, height: ch * 0.3), cornerRadius: ch * 0.05), with: .color(g))
                c.fill(Path(roundedRect: CGRect(x: cx + ch * 0.02, y: base - ch * 0.38, width: ch * 0.29, height: ch * 0.09), cornerRadius: ch * 0.04), with: .color(g))
            }
        }
        layer(1) { c in road(&c, color: Color(hex: 0x2B2230), line: Color(hex: 0xFFC857)) }
    }

    func synthwave() {
        sky([Color(hex: 0x12002B), Color(hex: 0x4B0C6B), Color(hex: 0xFF3D8B)])
        layer(0.1) { c in
            stars(&c, count: 30, seed: 51, maxY: groundY * 0.5)
            // striped sun
            let center = CGPoint(x: w / 2, y: groundY - h * 0.02), r = w * 0.24
            var sun = c
            sun.clip(to: Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r, width: 2 * r, height: 2 * r)))
            sun.fill(Path(CGRect(x: center.x - r, y: center.y - r, width: 2 * r, height: 2 * r)),
                     with: .linearGradient(Gradient(colors: [Color(hex: 0xFFE66D), Color(hex: 0xFF5E7E)]),
                                           startPoint: CGPoint(x: 0, y: center.y - r), endPoint: CGPoint(x: 0, y: center.y + r * 0.2)))
            for k in 0..<6 {
                let y = center.y - r * 0.05 + CGFloat(k) * r * 0.16
                sun.fill(Path(CGRect(x: center.x - r, y: y, width: 2 * r, height: r * (0.03 + CGFloat(k) * 0.018))),
                         with: .color(Color(hex: 0x4B0C6B)))
            }
        }
        layer(0.35) { c in
            ridge(&c, base: groundY, height: h * 0.1, peaks: 6, seed: 52, color: Color(hex: 0x1D0638))
        }
        layer(1) { c in
            c.fill(Path(CGRect(x: -w, y: groundY, width: w * 3, height: h)), with: .color(Color(hex: 0x0D0221)))
            let grid = Color(hex: 0xFF4FD8)
            for i in -10...10 {
                var l = Path()
                l.move(to: CGPoint(x: w / 2 + CGFloat(i) * w * 0.02, y: groundY))
                l.addLine(to: CGPoint(x: w / 2 + CGFloat(i) * w * 0.28, y: h))
                c.stroke(l, with: .color(grid.opacity(0.8)), lineWidth: 1)
            }
            for k in 0..<9 {
                let t = pow(Double(k) / 9, 1.8), y = groundY + (h - groundY) * t
                var l = Path()
                l.move(to: CGPoint(x: -w, y: y))
                l.addLine(to: CGPoint(x: w * 2, y: y))
                c.stroke(l, with: .color(grid.opacity(0.4 + 0.5 * t)), lineWidth: 1)
            }
        }
    }

    func aurora() {
        sky([Color(hex: 0x020B1F), Color(hex: 0x06284A), Color(hex: 0x0F4C5C)])
        layer(0.1) { c in
            stars(&c, count: 50, seed: 61, maxY: groundY)
            for (k, color) in [Color(hex: 0x3DFFB0), Color(hex: 0x7A5CFF), Color(hex: 0x3DE0FF)].enumerated() {
                var band = Path()
                let y0 = h * (0.14 + CGFloat(k) * 0.07)
                band.move(to: CGPoint(x: -w * 0.1, y: y0))
                band.addCurve(to: CGPoint(x: w * 1.1, y: y0 + h * 0.04),
                              control1: CGPoint(x: w * 0.3, y: y0 - h * 0.14), control2: CGPoint(x: w * 0.65, y: y0 + h * 0.16))
                c.stroke(band, with: .color(color.opacity(0.25)), lineWidth: h * 0.07)
                c.stroke(band, with: .color(color.opacity(0.5)), lineWidth: h * 0.018)
            }
        }
        layer(0.4) { c in
            ridge(&c, base: groundY, height: h * 0.16, peaks: 4, seed: 62, color: Color(hex: 0x1B3550), snow: .white.opacity(0.9))
        }
        layer(0.85) { c in
            c.fill(Path(CGRect(x: -w, y: groundY, width: w * 3, height: h)),
                   with: .linearGradient(Gradient(colors: [Color(hex: 0xDDEBF5), Color(hex: 0x9FB8CC)]),
                                         startPoint: CGPoint(x: 0, y: groundY), endPoint: CGPoint(x: 0, y: h)))
            var rng = Seeded(63)
            for i in 0..<8 {
                let x = w * CGFloat(i) / 7.5 + CGFloat(rng.range(-6, 6))
                if abs(x - w / 2) < w * 0.18 { continue }
                pine(&c, x: x, base: groundY + h * 0.05, height: h * CGFloat(rng.range(0.14, 0.22)), color: Color(hex: 0x0E2A2A),
                     snow: true)
            }
        }
        layer(1) { c in
            var rng = Seeded(64)
            for _ in 0..<40 {
                let r = rng.range(0.6, 1.8)
                c.fill(Path(ellipseIn: CGRect(x: rng.range(0, w), y: rng.range(0, h), width: r * 2, height: r * 2)),
                       with: .color(.white.opacity(0.8)))
            }
        }
    }

    func autumnForest() {
        sky([Color(hex: 0xF6B26B), Color(hex: 0xFAD7A0), Color(hex: 0xFDF2E0)])
        layer(0.1) { c in disc(&c, CGPoint(x: w * 0.8, y: h * 0.18), w * 0.07, Color(hex: 0xFFF8E1), glow: 2) }
        layer(0.35) { c in
            hills(&c, base: groundY, amp: h * 0.12, waves: 2, phase: 0.3, color: Color(hex: 0xC56A3B))
        }
        layer(0.65) { c in
            var rng = Seeded(71)
            let colors = [Color(hex: 0xE4572E), Color(hex: 0xF3A712), Color(hex: 0xC1292E), Color(hex: 0xA44A3F)]
            for i in 0..<11 {
                let x = w * CGFloat(i) / 10 + CGFloat(rng.range(-6, 6))
                if abs(x - w / 2) < w * 0.14 { continue }
                let r = h * CGFloat(rng.range(0.07, 0.11)), base = groundY + h * 0.05
                c.fill(Path(CGRect(x: x - 1.5, y: base - r * 1.2, width: 3, height: r * 1.2)), with: .color(Color(hex: 0x4E342E)))
                for (dx, dy) in [(0.0, 1.9), (-0.55, 1.4), (0.55, 1.45)] {
                    c.fill(Path(ellipseIn: CGRect(x: x + r * CGFloat(dx) - r * 0.7, y: base - r * CGFloat(dy) - r * 0.7,
                                                  width: r * 1.4, height: r * 1.4)),
                           with: .color(colors[Int(rng.next() * Double(colors.count))]))
                }
            }
        }
        layer(1) { c in
            c.fill(Path(CGRect(x: -w, y: groundY + h * 0.04, width: w * 3, height: h)), with: .color(Color(hex: 0x8C5A3C)))
            road(&c, color: Color(hex: 0x5D4A3F), line: Color(hex: 0xF3D9A4), top: groundY + h * 0.04)
            var rng = Seeded(72)
            for _ in 0..<18 {
                let x = rng.range(0, w), y = rng.range(0, h)
                var leaf = Path(ellipseIn: CGRect(x: -3, y: -1.5, width: 6, height: 3))
                leaf = leaf.applying(CGAffineTransform(rotationAngle: rng.range(0, .pi)).concatenating(.init(translationX: x, y: y)))
                c.fill(leaf, with: .color([Color(hex: 0xE4572E), Color(hex: 0xF3A712)][Int(rng.next() * 2)]))
            }
        }
    }

    func space() {
        sky([Color(hex: 0x05010F), Color(hex: 0x140A35), Color(hex: 0x2A0E4F)], to: h)
        layer(0.05) { c in
            // nebula glow
            c.fill(Path(ellipseIn: CGRect(x: -w * 0.2, y: h * 0.05, width: w * 0.9, height: h * 0.45)),
                   with: .radialGradient(Gradient(colors: [energy.color.opacity(0.45), .clear]),
                                         center: CGPoint(x: w * 0.25, y: h * 0.27), startRadius: 0, endRadius: w * 0.5))
            stars(&c, count: 90, seed: 81, maxY: h)
        }
        layer(0.3) { c in
            // ringed planet
            let center = CGPoint(x: w * 0.74, y: h * 0.22), r = w * 0.13
            disc(&c, center, r, Color(hex: 0xF2A65A), glow: 1.4)
            c.fill(Path(ellipseIn: CGRect(x: center.x - r, y: center.y - r * 0.15, width: r * 2, height: r * 0.5)),
                   with: .color(Color(hex: 0xD2693C).opacity(0.6)))
            var ring = Path(ellipseIn: CGRect(x: -r * 1.8, y: -r * 0.35, width: r * 3.6, height: r * 0.7))
            ring = ring.applying(CGAffineTransform(rotationAngle: -0.3).concatenating(.init(translationX: center.x, y: center.y)))
            c.stroke(ring, with: .color(Color(hex: 0xFFE3B3).opacity(0.85)), lineWidth: 2.5)
        }
        layer(0.7) { c in
            // a small moon surface underfoot
            var ground = Path()
            ground.move(to: CGPoint(x: -w * 0.1, y: groundY + h * 0.04))
            ground.addQuadCurve(to: CGPoint(x: w * 1.1, y: groundY + h * 0.04), control: CGPoint(x: w / 2, y: groundY - h * 0.06))
            ground.addLine(to: CGPoint(x: w * 1.1, y: h))
            ground.addLine(to: CGPoint(x: -w * 0.1, y: h))
            ground.closeSubpath()
            c.fill(ground, with: .linearGradient(Gradient(colors: [Color(hex: 0x9A93B5), Color(hex: 0x4B4566)]),
                                                 startPoint: CGPoint(x: 0, y: groundY), endPoint: CGPoint(x: 0, y: h)))
            var rng = Seeded(82)
            for _ in 0..<7 {
                let x = rng.range(0, w), y = rng.range(Double(groundY + h * 0.06), Double(h)), r = rng.range(4, 12)
                c.fill(Path(ellipseIn: CGRect(x: x - r, y: y - r * 0.35, width: r * 2, height: r * 0.7)),
                       with: .color(Color(hex: 0x3B3555).opacity(0.7)))
            }
        }
    }
}

/// A full-art card's background: the energy's symbol scattered in big and small sizes over its colors, with
/// speed lines from the middle. Drifts with the tilt.
struct EnergyBackdrop: View {
    let energy: Energy
    var tilt: CGPoint = .zero

    var body: some View {
        Canvas { ctx, size in
            ctx.fill(Path(CGRect(origin: .zero, size: size)),
                     with: .linearGradient(Gradient(colors: [energy.light, energy.color, energy.color.mix(with: .black, by: 0.45)]),
                                           startPoint: .zero, endPoint: CGPoint(x: size.width, y: size.height)))
            let c = CGPoint(x: size.width / 2, y: size.height * 0.42)
            let r = max(size.width, size.height)
            for i in 0..<36 where i.isMultiple(of: 2) {
                let a0 = Double(i) / 36 * 2 * .pi, a1 = Double(i) / 36 * 2 * .pi + 0.06
                var p = Path()
                p.move(to: c)
                p.addLine(to: CGPoint(x: c.x + r * cos(a0), y: c.y + r * sin(a0)))
                p.addLine(to: CGPoint(x: c.x + r * cos(a1), y: c.y + r * sin(a1)))
                p.closeSubpath()
                ctx.fill(p, with: .color(.white.opacity(0.1)))
            }
            guard let symbol = ctx.resolveSymbol(id: 0) else { return }
            var rng = Seeded(UInt64(energy.symbol.utf8.reduce(0) { $0 &* 31 &+ Int($1) }.magnitude))
            for k in 0..<14 {
                let depth = CGFloat(k % 3 + 1) / 3
                let s = CGFloat(rng.range(0.4, 1.6)) * depth
                var g = ctx
                g.translateBy(x: CGFloat(rng.range(0, Double(size.width))) - tilt.x * depth * 14,
                              y: CGFloat(rng.range(0, Double(size.height))) - tilt.y * depth * 9)
                g.rotate(by: .radians(rng.range(-0.6, 0.6)))
                g.scaleBy(x: s, y: s)
                g.opacity = 0.12 + 0.12 * depth
                g.draw(symbol, at: .zero)
            }
        } symbols: {
            Image(systemName: energy.symbol).font(.system(size: 64, weight: .black)).foregroundStyle(.white).tag(0)
        }
        .allowsHitTesting(false)
    }
}

/// The energy symbol tiled across a region: the mask for an energy-pattern reverse holo.
struct EnergyPattern: View {
    let symbol: String

    var body: some View {
        Canvas { ctx, size in
            guard let s = ctx.resolveSymbol(id: 0) else { return }
            let step: CGFloat = 26
            var row = 0
            for y in stride(from: CGFloat(8), to: size.height + step, by: step * 0.86) {
                let offset = row.isMultiple(of: 2) ? 0 : step / 2
                for x in stride(from: CGFloat(4) + offset, to: size.width + step, by: step) {
                    ctx.draw(s, at: CGPoint(x: x, y: y))
                }
                row += 1
            }
        } symbols: {
            Image(systemName: symbol).font(.system(size: 13, weight: .bold)).foregroundStyle(.white).tag(0)
        }
    }
}
