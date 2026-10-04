import Foundation
import simd

/// A low-poly, flat-shaded car mesh built from a `CarShape`, split by material. Meters; y up, the nose toward +z,
/// the ground at y = 0. The body is lofted through cross-sections along the length (rocker, side, shoulder,
/// greenhouse, roof), then wheels, lights, bumpers and extras are added.
public struct CarMesh: Sendable {
    public enum Part: Int, CaseIterable, Sendable {
        case paint, glass, trim, tire, rim, headlight, taillight
    }

    public struct Piece: Sendable {
        public var positions: [SIMD3<Float>] = []
        public var normals: [SIMD3<Float>] = []
        public var indices: [UInt32] = []
        public var triangleCount: Int { indices.count / 3 }
    }

    public private(set) var pieces: [Part: Piece]

    public init(_ shape: CarShape) {
        var b = Builder(shape)
        b.build()
        pieces = b.pieces
    }

    public var triangleCount: Int { pieces.values.reduce(0) { $0 + $1.triangleCount } }

    public var bounds: (min: SIMD3<Float>, max: SIMD3<Float>) {
        var lo = SIMD3<Float>(repeating: .infinity), hi = SIMD3<Float>(repeating: -.infinity)
        for p in pieces.values.flatMap(\.positions) {
            lo = simd_min(lo, p)
            hi = simd_max(hi, p)
        }
        return (lo, hi)
    }
}

private struct Builder {
    let s: CarShape
    var pieces: [CarMesh.Part: CarMesh.Piece] = [:]

    init(_ shape: CarShape) { s = shape }

    var halfW: Float { s.width / 2 }
    var halfL: Float { s.length / 2 }
    /// A little bigger than life: low-poly cars read better with chunky wheels.
    var radius: Float { s.wheelDiameter / 2 * 1.1 }
    var frontAxle: Float { halfL - s.frontOverhang }
    var rearAxle: Float { frontAxle - s.wheelbase }
    var singleTrack: Bool { s.roof == .open }

    mutating func build() {
        body()
        wheels()
        details()
    }

    // MARK: Triangles

    mutating func tri(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ c: SIMD3<Float>, _ part: CarMesh.Part) {
        let n = simd_cross(b - a, c - a)
        let len = simd_length(n)
        guard len > 1e-9 else { return }
        var piece = pieces[part] ?? .init()
        let base = UInt32(piece.positions.count)
        piece.positions += [a, b, c]
        piece.normals += Array(repeating: n / len, count: 3)
        piece.indices += [base, base + 1, base + 2]
        pieces[part] = piece
    }

    /// a→b→c→d counterclockwise seen from outside.
    mutating func quad(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ c: SIMD3<Float>, _ d: SIMD3<Float>,
                       _ part: CarMesh.Part) {
        tri(a, b, c, part)
        tri(a, c, d, part)
    }

    /// A quad wound to face away from `inside`.
    mutating func quad(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ c: SIMD3<Float>, _ d: SIMD3<Float>,
                       _ part: CarMesh.Part, awayFrom inside: SIMD3<Float>) {
        let n = simd_cross(b - a, c - a) + simd_cross(c - a, d - a)
        if simd_dot(n, (a + b + c + d) / 4 - inside) >= 0 { quad(a, b, c, d, part) } else { quad(d, c, b, a, part) }
    }

    mutating func box(_ center: SIMD3<Float>, _ size: SIMD3<Float>, _ part: CarMesh.Part) {
        let h = size / 2
        func p(_ x: Float, _ y: Float, _ z: Float) -> SIMD3<Float> { center + SIMD3(x * h.x, y * h.y, z * h.z) }
        quad(p(1, -1, -1), p(1, 1, -1), p(1, 1, 1), p(1, -1, 1), part, awayFrom: center)
        quad(p(-1, -1, -1), p(-1, 1, -1), p(-1, 1, 1), p(-1, -1, 1), part, awayFrom: center)
        quad(p(-1, 1, -1), p(1, 1, -1), p(1, 1, 1), p(-1, 1, 1), part, awayFrom: center)
        quad(p(-1, -1, -1), p(1, -1, -1), p(1, -1, 1), p(-1, -1, 1), part, awayFrom: center)
        quad(p(-1, -1, 1), p(1, -1, 1), p(1, 1, 1), p(-1, 1, 1), part, awayFrom: center)
        quad(p(-1, -1, -1), p(1, -1, -1), p(1, 1, -1), p(-1, 1, -1), part, awayFrom: center)
    }

    /// Revolves a (radius, offset along the axis) profile around `axis` through `center`.
    mutating func lathe(_ center: SIMD3<Float>, axis: SIMD3<Float>, _ profile: [(Float, Float)],
                        _ part: CarMesh.Part, segments: Int = 18) {
        let u = simd_normalize(simd_cross(axis, abs(axis.y) < 0.9 ? SIMD3(0, 1, 0) : SIMD3(1, 0, 0)))
        let v = simd_cross(axis, u)
        func point(_ r: Float, _ off: Float, _ a: Float) -> SIMD3<Float> {
            center + axis * off + (u * cos(a) + v * sin(a)) * r
        }
        for i in 0..<(profile.count - 1) {
            let (r0, o0) = profile[i], (r1, o1) = profile[i + 1]
            for j in 0..<segments {
                let a0 = Float(j) / Float(segments) * 2 * .pi, a1 = Float(j + 1) / Float(segments) * 2 * .pi
                let p00 = point(r0, o0, a0), p01 = point(r0, o0, a1), p11 = point(r1, o1, a1), p10 = point(r1, o1, a0)
                // the solids are convex enough that "away from the center" is outward for treads, sides and discs
                quad(p00, p01, p11, p10, part, awayFrom: center)
            }
        }
    }

    // MARK: Body

    enum Segment { case hood, windshield, roof, pillar, rearGlass, rearPaint, deck, bed, box, boxWindows, seat, step }

    struct Station {
        var z: Float
        var top: Float
        /// Kind of the segment from this station toward the next one (toward the tail).
        var segment: Segment
        var widthScale: Float = 1
        var lift: Float = 0
        /// A pickup bed's open top: the top sinks to the bed floor inside the rails.
        var cavity = false
    }

    /// Profile keypoints from the nose back to the tail.
    func profile() -> [Station] {
        let h = s.height, cab = s.height * s.cabHeight
        var z = halfL
        var st: [Station] = []
        func add(_ top: Float, _ seg: Segment) { st.append(Station(z: z, top: top, segment: seg)) }
        if s.roof == .open {
            add(s.nose * h, .hood); z -= s.hood
            add(0.8 * h, .hood); z -= s.windshield
            add(0.82 * h, .seat); z -= s.roofLength
            add(0.7 * h, .deck); z = -halfL
            add(0.72 * h, .deck)
            return st
        }
        add(s.nose * h, .hood); z -= s.hood
        add(s.belt * h, .windshield); z -= s.windshield
        let roofTop = s.roof == .boxTruck ? cab : h
        let roofEnd = z - s.roofLength
        add(roofTop, .roof)
        // pillars split the roof's side glass
        if s.pillars > 0 && s.roofLength > 0.3 {
            for i in 1...s.pillars {
                let f = Float(i) / Float(s.pillars + 1)
                let pz = z - s.roofLength * (s.pillars == 1 ? 0.45 : f)
                st.append(Station(z: pz + 0.05, top: roofTop, segment: .pillar))
                st.append(Station(z: pz - 0.05, top: roofTop, segment: .roof))
            }
        }
        z = roofEnd
        switch s.roof {
        case .notchback, .fastback, .hatch, .van:
            let deck = s.deck * h
            let glass = s.roof == .hatch || s.roof == .van ? min(s.rearGlass, 0.62) : s.rearGlass
            add(roofTop, glass > 0 ? .rearGlass : .rearPaint)
            if glass > 0 && glass < 1 {
                z -= s.rear * glass
                add(roofTop + (deck - roofTop) * glass, .rearPaint)
                z -= s.rear * (1 - glass)
            } else {
                z -= s.rear
            }
            if z > -halfL + 0.02 {
                add(deck, .deck)
                z = -halfL
            }
            add(deck, .deck)
        case .pickup:
            add(roofTop, .rearGlass); z -= 0.06
            add(s.belt * h, .bed); z -= 0.08
            st.append(Station(z: z, top: s.belt * h, segment: .bed, cavity: true))
            z = -halfL + 0.1
            st.append(Station(z: z, top: s.belt * h, segment: .bed, cavity: true))
            z = -halfL
            add(s.belt * h, .bed)
        case .boxTruck:
            if s.extras.contains(.mixerDrum) {
                // a low chassis behind the cab for the drum to sit on
                add(cab, .step); z -= 0.12
                add(s.deck * h, .deck); z = -halfL
                add(s.deck * h, .deck)
                break
            }
            add(cab, .step); z -= 0.12
            let boxStart = z
            add(h, s.pillars > 0 ? .boxWindows : .box)
            if s.pillars > 0 {
                let run = boxStart - (-halfL + 0.4)
                for i in 1...s.pillars {
                    let pz = boxStart - run * Float(i) / Float(s.pillars + 1)
                    st.append(Station(z: pz + 0.06, top: h, segment: .pillar))
                    st.append(Station(z: pz - 0.06, top: h, segment: .boxWindows))
                }
            }
            z = -halfL
            add(h, .box)
        case .open:
            break
        }
        return st
    }

    /// Adds rounded nose and tail stations and sorts from the tail forward.
    func stations() -> [Station] {
        var st = profile()
        guard st.count >= 2 else { return st }
        let r: Float = min(0.14, s.length * 0.03)
        // nose: pull in and lift the very front; a full-width station just behind it
        let n0 = st[0], n1 = st[1]
        let t = r / max(n0.z - n1.z, 0.001)
        var behind = Station(z: n0.z - r, top: n0.top + (n1.top - n0.top) * t, segment: n0.segment)
        st[0].widthScale = 0.9
        st[0].lift = 0.05
        st[0].top -= 0.02
        st.insert(behind, at: 1)
        // tail
        let last = st.count - 1
        let tl = st[last], tp = st[last - 1]
        let tt = r / max(tp.z - tl.z, 0.001)
        behind = Station(z: tl.z + r, top: tl.top + (tp.top - tl.top) * tt, segment: tp.segment)
        st[last].widthScale = 0.92
        st[last].lift = 0.06
        st[last].top -= 0.02
        st.insert(behind, at: last)
        return st.reversed()
    }

    /// The right half of a cross-section, bottom center to top center (8 points).
    func section(_ st: Station) -> [SIMD2<Float>] {
        let hw = halfW * st.widthScale
        let bottom = s.clearance + st.lift
        let top = max(st.top, bottom + 0.1)
        // the beltline rises a little toward the tail, like most modern cars
        let rise: Float = s.roof == .boxTruck || s.roof == .van || s.roof == .open ? 0 : 0.06
        let belt = min(s.belt * s.height * (1 + rise * (halfL - st.z) / s.length), top)
        let cabin = min(max((top - belt) / max(0.25 * s.height, 0.01), 0), 1)
        let cb: Float = min(0.07, (belt - bottom) * 0.25), cs: Float = min(0.06, hw * 0.1)
        if st.segment == .bed {
            // rails, then (inside the bed) down to the floor
            let rail = hw - cs - 0.1, floor = st.cavity ? max(bottom + 0.12, belt - 0.42) : belt
            return [SIMD2(0, bottom), SIMD2(hw - cb, bottom), SIMD2(hw, bottom + cb), SIMD2(hw, belt - cs),
                    SIMD2(hw - cs, belt), SIMD2(rail, belt), SIMD2(rail - 0.01, floor), SIMD2(0, floor)]
        }
        let roofHalf = (hw - cs) + (hw * s.roofWidth - (hw - cs)) * cabin
        let cr: Float = min(0.07, roofHalf * 0.2) * max(cabin, 0.4)
        // buses and big vans: a painted band between the windows and the roof
        let band: Float = s.height > 2.4 && s.pillars > 2 ? 0.45 * cabin : 0
        return [
            SIMD2(0, bottom),
            SIMD2(hw - cb, bottom),
            SIMD2(hw, bottom + cb),
            SIMD2(hw, belt - cs),
            SIMD2(hw - cs, belt),
            SIMD2(roofHalf, top - cr - band),
            SIMD2(roofHalf - cr, top),
            SIMD2(0, top),
        ]
    }

    /// Material of cross-section edge `edge` (0 underbody … 6 top strip) in a segment.
    func material(_ seg: Segment, edge: Int) -> CarMesh.Part {
        switch edge {
        case 0, 1: return .trim
        case 2, 3: return .paint
        case 4:   // greenhouse side: door glass runs up under the windshield and (on sedans) the rear pillar
            switch seg {
            case .roof: return s.roof == .open || (s.roof == .van && s.pillars == 0) ? .paint : .glass
            case .boxWindows, .windshield: return s.roof == .open ? .paint : .glass
            case .rearGlass: return s.roof == .notchback || s.roof == .fastback ? .glass : .paint
            default: return .paint
            }
        case 5:   // roof edge: the pillars around the windshield and rear window
            switch seg {
            case .bed, .seat: return .trim
            default: return .paint
            }
        default:  // top strip
            switch seg {
            case .windshield, .rearGlass: return .glass
            case .roof: return s.extras.contains(.glassRoof) ? .glass : .paint
            case .bed: return .trim
            case .seat: return .trim
            default: return .paint
            }
        }
    }

    mutating func body() {
        let st = stations()
        let sections = st.map(section)
        func p3(_ p: SIMD2<Float>, _ z: Float, _ side: Float) -> SIMD3<Float> { SIMD3(p.x * side, p.y, z) }
        for i in 0..<(st.count - 1) {
            let a = sections[i], b = sections[i + 1]
            let za = st[i].z, zb = st[i + 1].z
            // the segment's kind belongs to the station nearer the nose
            let seg = st[i + 1].segment
            for k in 0..<(a.count - 1) {
                let part = material(seg, edge: k)
                // right side (x > 0)
                quad(p3(a[k], za, 1), p3(a[k + 1], za, 1), p3(b[k + 1], zb, 1), p3(b[k], zb, 1), part)
                // left side, mirrored
                quad(p3(b[k], zb, -1), p3(b[k + 1], zb, -1), p3(a[k + 1], za, -1), p3(a[k], za, -1), part)
            }
        }
        cap(sections[st.count - 1], z: st[st.count - 1].z, facing: 1)
        cap(sections[0], z: st[0].z, facing: -1)
    }

    /// Closes the nose or tail with a fan from the section's middle.
    mutating func cap(_ sec: [SIMD2<Float>], z: Float, facing: Float) {
        let ring = sec.map { SIMD3($0.x, $0.y, z) } + sec.reversed().dropFirst().dropLast().map { SIMD3(-$0.x, $0.y, z) }
        let mid = SIMD3<Float>(0, (sec[0].y + sec[sec.count - 1].y) / 2, z)
        for i in 0..<ring.count {
            let a = ring[i], b = ring[(i + 1) % ring.count]
            let n = simd_cross(a - mid, b - mid)
            if n.z * facing >= 0 { tri(mid, a, b, .paint) } else { tri(mid, b, a, .paint) }
        }
    }

    // MARK: Wheels

    mutating func wheels() {
        let r = radius
        let tw: Float = singleTrack ? 0.14 : min(0.26, s.wheelDiameter * 0.32)
        let x: Float = singleTrack ? 0 : halfW + 0.02 - tw / 2
        for z in [frontAxle, rearAxle] {
            for side: Float in singleTrack ? [1] : [1, -1] {
                wheel(SIMD3(x * side, r, z), r: r, width: tw, axis: SIMD3(side, 0, 0))
                if singleTrack {
                    let rim = r * 0.62
                    lathe(SIMD3(0, r, z), axis: SIMD3(-1, 0, 0), [(rim, tw / 2), (rim * 0.9, tw / 2 + 0.012),
                                                                    (rim * 0.3, tw / 2 + 0.012), (0, tw / 2 + 0.02)],
                          .rim, segments: 12)
                }
                if !singleTrack { arch(z: z, side: side) }
            }
        }
    }

    /// A tire and rim; `axis` points out of the rim's face.
    mutating func wheel(_ c: SIMD3<Float>, r: Float, width w: Float, axis: SIMD3<Float>) {
        let rim = r * 0.62
        lathe(c, axis: axis, [(rim, w / 2), (r * 0.93, w / 2), (r, w / 2 - 0.035), (r, -w / 2 + 0.035),
                              (r * 0.93, -w / 2), (rim, -w / 2)], .tire)
        // inner face, then the rim and hub on the outer face
        lathe(c, axis: axis, [(0, -w / 2), (rim, -w / 2)], .tire, segments: 12)
        lathe(c, axis: axis, [(rim, w / 2), (rim * 0.9, w / 2 + 0.012), (rim * 0.35, w / 2 + 0.012),
                              (rim * 0.25, w / 2 + 0.03), (0, w / 2 + 0.03)], .rim, segments: 12)
    }

    /// A dark wheel-well arch on the body side around the top of the wheel; wider with `.flares`.
    mutating func arch(z: Float, side: Float) {
        let flare = s.extras.contains(.flares)
        let r0 = radius * 1.04, r1 = radius * (flare ? 1.3 : 1.14)
        let xIn = halfW - 0.02, xOut = halfW + (flare ? 0.07 : 0.025)
        let steps = 10
        for j in 0..<steps {
            let a0 = Float(j) / Float(steps) * .pi, a1 = Float(j + 1) / Float(steps) * .pi
            func pt(_ r: Float, _ a: Float, _ x: Float) -> SIMD3<Float> { SIMD3(x * side, radius + sin(a) * r, z + cos(a) * r) }
            let inside = SIMD3<Float>(0, radius, z)
            quad(pt(r0, a0, xOut), pt(r1, a0, xOut), pt(r1, a1, xOut), pt(r0, a1, xOut), .trim,
                 awayFrom: SIMD3(0, pt(r0, (a0 + a1) / 2, 0).y, pt(r0, (a0 + a1) / 2, 0).z))
            quad(pt(r1, a0, xIn), pt(r1, a1, xIn), pt(r1, a1, xOut), pt(r1, a0, xOut), .trim, awayFrom: inside)
            quad(pt(r0, a0, xIn), pt(r0, a1, xIn), pt(r0, a1, xOut), pt(r0, a0, xOut), .trim,
                 awayFrom: SIMD3(0, radius + 10, z))
        }
    }

    // MARK: Details

    mutating func details() {
        let h = s.height, hw = halfW
        let noseTop = s.nose * h
        let e = s.extras
        if singleTrack {
            box(SIMD3(0, noseTop + 0.12, halfL - 0.15), SIMD3(0.7, 0.03, 0.03), .trim)   // handlebar
            // fork from the front axle up to the bars
            let forkTop = SIMD3<Float>(0, noseTop + 0.1, halfL - 0.15), forkBottom = SIMD3<Float>(0, radius, frontAxle)
            let len = simd_length(forkTop - forkBottom)
            for side: Float in [1, -1] {
                // the raked fork leg as a stack of short boxes
                for k in 0..<4 {
                    let t = (Float(k) + 0.5) / 4
                    let c = forkBottom + (forkTop - forkBottom) * t + SIMD3(side * 0.09, 0, 0)
                    box(c, SIMD3(0.04, len / 4 + 0.02, 0.04), .rim)
                }
            }
            lathe(SIMD3(0, noseTop - 0.02, halfL + 0.01), axis: SIMD3(0, 0, 1), [(0, 0.03), (0.08, 0.03), (0.08, 0)],
                  .headlight, segments: 12)
            box(SIMD3(0, 0.62 * h, -halfL + 0.05), SIMD3(0.16, 0.05, 0.03), .taillight)
            box(SIMD3(0, radius + 0.1, 0.05), SIMD3(0.24, 0.26, 0.5), .trim)   // engine
            return
        }
        let front = halfL, back = -halfL
        let bumperH = min(0.16, (noseTop - s.clearance) * 0.35)
        // bumpers
        box(SIMD3(0, s.clearance + 0.06 + bumperH / 2, front - 0.02), SIMD3(hw * 1.84, bumperH, 0.14), .trim)
        box(SIMD3(0, s.clearance + 0.06 + bumperH / 2, back + 0.02), SIMD3(hw * 1.84, bumperH, 0.14), .trim)
        // headlights
        let lightY = noseTop - 0.08
        if e.contains(.roundLights) {
            for side: Float in [1, -1] {
                lathe(SIMD3(side * hw * 0.62, lightY - 0.02, front - 0.01), axis: SIMD3(0, 0, 1),
                      [(0, 0.03), (0.09, 0.03), (0.11, 0)], .headlight, segments: 14)
            }
        } else {
            for side: Float in [1, -1] {
                box(SIMD3(side * hw * 0.64, lightY, front - 0.01), SIMD3(hw * 0.42, 0.07, 0.06), .headlight)
            }
        }
        if e.contains(.lightStrip) {
            box(SIMD3(0, lightY + 0.04, front - 0.005), SIMD3(hw * 1.5, 0.025, 0.05), .headlight)
            box(SIMD3(0, s.deck * h - 0.07, back + 0.005), SIMD3(hw * 1.6, 0.04, 0.05), .taillight)
        }
        // grille
        if !e.contains(.noGrille) {
            let big = e.contains(.bigGrille)
            let gh = (lightY - 0.05) - (s.clearance + 0.06 + bumperH)
            if gh > 0.04 {
                box(SIMD3(0, s.clearance + 0.06 + bumperH + gh / 2, front - 0.01),
                    SIMD3(hw * (big ? 0.95 : 0.6), gh * (big ? 1 : 0.7), 0.06), .trim)
            }
        }
        // taillights
        let tailY = s.roof == .boxTruck || s.roof == .van && s.height > 2.2
            ? s.clearance + 0.45 : min(s.deck, s.belt) * h - 0.07
        for side: Float in [1, -1] {
            box(SIMD3(side * hw * 0.74, tailY, back - 0.005), SIMD3(hw * 0.36, 0.08, 0.05), .taillight)
        }
        // mirrors at the windshield base
        let mz = front - s.hood - 0.08
        for side: Float in [1, -1] {
            box(SIMD3(side * (hw + 0.07), s.belt * h + 0.07, mz), SIMD3(0.12, 0.08, 0.1), .paint)
        }
        let roofZ0 = front - s.hood - s.windshield, roofZ1 = roofZ0 - s.roofLength
        let roofHalf = hw * s.roofWidth
        if e.contains(.roofRack) && s.roofLength > 0.5 {
            for side: Float in [1, -1] {
                box(SIMD3(side * roofHalf * 0.8, h + 0.04, (roofZ0 + roofZ1) / 2), SIMD3(0.04, 0.04, s.roofLength * 0.9), .trim)
            }
            for f: Float in [0.25, 0.75] {
                box(SIMD3(0, h + 0.07, roofZ0 - s.roofLength * f), SIMD3(roofHalf * 1.7, 0.03, 0.05), .trim)
            }
        }
        if e.contains(.lightBar) {
            box(SIMD3(0, h + 0.05, roofZ0 - 0.1), SIMD3(roofHalf * 1.4, 0.06, 0.08), .headlight)
        }
        if e.contains(.spareTire) {
            let y = max(s.clearance + radius + 0.1, (s.deck * h + s.belt * h) / 2)
            wheel(SIMD3(0, y, back - 0.12), r: radius * 0.92, width: 0.2, axis: SIMD3(0, 0, -1))
        }
        if e.contains(.spoiler) {
            let y = s.deck * h + 0.16, z = back + 0.22
            box(SIMD3(0, y, z), SIMD3(hw * 1.7, 0.035, 0.26), .trim)
            for side: Float in [1, -1] { box(SIMD3(side * hw * 0.55, y - 0.08, z), SIMD3(0.04, 0.16, 0.08), .trim) }
        }
        if e.contains(.ducktail) {
            box(SIMD3(0, s.deck * h + 0.025, back + 0.12), SIMD3(hw * 1.6, 0.05, 0.22), .paint)
        }
        if e.contains(.sideSteps) {
            for side: Float in [1, -1] {
                box(SIMD3(side * (hw + 0.04), s.clearance + 0.02, (frontAxle + rearAxle) / 2),
                    SIMD3(0.1, 0.04, s.wheelbase * 0.6), .trim)
            }
        }
        if e.contains(.mixerDrum) {
            let len = s.rear * 0.75, zc = back + 0.4 + len / 2
            lathe(SIMD3(0, s.height * 0.62, zc), axis: SIMD3(0, 0, 1),
                  [(0, -len / 2), (0.5, -len / 2), (1.0, -len * 0.15), (0.85, len * 0.3), (0.4, len / 2), (0, len / 2)],
                  .rim, segments: 16)
        }
    }
}
