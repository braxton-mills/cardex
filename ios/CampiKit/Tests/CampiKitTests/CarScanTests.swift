import Foundation
import simd
import Testing
@testable import CampiKit

/// `scan.glb` is the mock PC's stand-in scan: a white box car with dark glass, black tires and two lamps on its
/// front (+x in the file), laid out like trimesh's export.
@Suite struct CarScanTests {
    func scan() throws -> CarScan { try CarScan(glb: Fixtures.data("scan.glb")) }

    @Test func readsTheGLB() throws {
        let s = try scan()
        #expect(s.positions.count == 216 && s.normals.count == 216 && s.colors.count == 216)
        #expect(s.indices.count == 324 && s.indices.allSatisfy { Int($0) < s.positions.count })
        // sRGB bytes come through as 0–1
        #expect(s.colors.contains { simd_distance($0, SIMD3(232, 232, 228) / 255) < 0.001 })
        // the file's +x (the front) is +z, like CarMesh; y stays up from the ground
        let lo = s.positions.reduce(SIMD3<Float>(repeating: 9)) { simd_min($0, $1) }
        let hi = s.positions.reduce(SIMD3<Float>(repeating: -9)) { simd_max($0, $1) }
        #expect(abs(hi.z - 0.505) < 0.001 && abs(lo.z + 0.5) < 0.001 && abs(lo.y) < 0.001)
        let lamp = SIMD3<Float>(250, 246, 220) / 255
        let lampZ = zip(s.positions, s.colors).filter { simd_distance($1, lamp) < 0.01 }.map(\.0.z)
        #expect(!lampZ.isEmpty && lampZ.allSatisfy { $0 > 0.49 })
    }

    @Test func rejectsWhatIsNotAGLB() {
        #expect(throws: CarScan.Failure.notGLB) { try CarScan(glb: Data("{\"asset\":{}}".utf8)) }
        #expect(throws: CarScan.Failure.notGLB) { try CarScan(glb: Data()) }
    }

    @Test func partsKeepPhotoColorsWithoutPaint() throws {
        let s = try scan()
        let parts = s.parts()
        #expect(parts.reduce(0) { $0 + $1.indices.count } == s.indices.count)
        let glass = SIMD3<Float>(30, 36, 46) / 255, white = SIMD3<Float>(232, 232, 228) / 255
        #expect(parts.contains { simd_distance($0.color, glass) < 0.01 && !$0.isBody })
        #expect(parts.contains { simd_distance($0.color, white) < 0.01 && $0.isBody })
        #expect(parts.contains { $0.color.x < 0.1 && !$0.isBody })   // tires
    }

    @Test func paintRepaintsOnlyTheBody() throws {
        let red = try #require(PaintColor.from("red"))
        let parts = try scan().parts(paint: red)
        let body = parts.filter(\.isBody)
        #expect(!body.isEmpty)
        // the main body color becomes the paint; glass and tires keep theirs
        #expect(body.contains { simd_distance($0.color, SIMD3(Float(red.red), Float(red.green), Float(red.blue))) < 0.02 })
        let glass = SIMD3<Float>(30, 36, 46) / 255
        #expect(parts.contains { simd_distance($0.color, glass) < 0.01 })
        #expect(!parts.filter { !$0.isBody }.contains { $0.color.x > 0.5 && $0.color.y < 0.2 })
    }

    @Test func aDarkCarsBodyIsItsDarkColor() {
        // a black car: three quarters of it is near-black body, plus a little light trim
        let body = SIMD3<Float>(repeating: 0.06), trim = SIMD3<Float>(repeating: 0.7)
        let means = [body, trim], areas: [Float] = [3, 1]
        let groups = CarScan.bodyGroups(means: means, areas: areas)
        #expect(groups.members == [0])
    }
}
