import Foundation
import simd

/// The PC's 3D scan of a car (`CollectionItem.Mesh`), read from its GLB: triangles with the photo's colors per
/// vertex. Turned into a few flat-colored parts (`parts(paint:)`) because RealityKit's materials don't take vertex
/// colors; the parts of the car's body can be repainted in another sighting's color.
public struct CarScan: Sendable {
    /// Same axes as `CarMesh`: y up, the nose toward +z, the ground at y = 0. The scan's length is 1.
    public var positions: [SIMD3<Float>]
    public var normals: [SIMD3<Float>]
    /// sRGB, 0–1.
    public var colors: [SIMD3<Float>]
    public var indices: [UInt32]

    public enum Failure: Error, Equatable {
        case notGLB, unsupported(String)
    }

    /// One flat color's triangles, with their own vertices.
    public struct Part: Sendable {
        /// sRGB, 0–1.
        public var color: SIMD3<Float>
        /// Repainted body (takes the paint's finish) rather than glass, tires or lights.
        public var isBody: Bool
        public var positions: [SIMD3<Float>]
        public var normals: [SIMD3<Float>]
        public var indices: [UInt32]
    }

    public init(positions: [SIMD3<Float>], normals: [SIMD3<Float>], colors: [SIMD3<Float>], indices: [UInt32]) {
        self.positions = positions
        self.normals = normals
        self.colors = colors
        self.indices = indices
    }

    // MARK: - GLB

    /// Reads every triangle primitive in the default scene (node transforms applied). The scan's +x (the car's
    /// front, per api-contract §5.6) becomes +z.
    public init(glb data: Data) throws(Failure) {
        let bytes = [UInt8](data)
        func u32(_ at: Int) -> UInt32 {
            UInt32(bytes[at]) | UInt32(bytes[at + 1]) << 8 | UInt32(bytes[at + 2]) << 16 | UInt32(bytes[at + 3]) << 24
        }
        guard bytes.count >= 20, u32(0) == 0x4654_6C67, u32(4) == 2 else { throw .notGLB }
        var json: [String: Any]?
        var bin: ArraySlice<UInt8>?
        var at = 12
        while at + 8 <= bytes.count {
            let length = Int(u32(at)), type = u32(at + 4)
            let start = at + 8, end = start + length
            guard end <= bytes.count else { throw .notGLB }
            if type == 0x4E4F_534A {   // "JSON"
                json = try? JSONSerialization.jsonObject(with: Data(bytes[start..<end])) as? [String: Any]
            } else if type == 0x004E_4942 {   // "BIN\0"
                bin = bytes[start..<end]
            }
            at = end
        }
        guard let json, let bin else { throw .notGLB }
        var reader = GLTFReader(json: json, bin: bin)
        try reader.read()
        self.init(positions: reader.positions.map { SIMD3(-$0.z, $0.y, $0.x) },
                  normals: reader.normals.map { SIMD3(-$0.z, $0.y, $0.x) },
                  colors: reader.colors, indices: reader.indices)
    }

    // MARK: - Parts

    /// The scan as about `clusters` flat colors. With `paint`, the body's colors are swapped for the paint's,
    /// keeping each one's light or shade relative to the main body color.
    public func parts(paint: PaintColor? = nil, clusters: Int = 24) -> [Part] {
        let triangles = indices.count / 3
        guard triangles > 0 else { return [] }
        var color = [SIMD3<Float>](repeating: .zero, count: triangles)
        var area = [Float](repeating: 0, count: triangles)
        for t in 0..<triangles {
            let a = Int(indices[t * 3]), b = Int(indices[t * 3 + 1]), c = Int(indices[t * 3 + 2])
            color[t] = (colors[a] + colors[b] + colors[c]) / 3
            area[t] = simd_length(simd_cross(positions[b] - positions[a], positions[c] - positions[a])) / 2 + 1e-9
        }
        let groups = Self.medianCut(Array(0..<triangles), color: color, area: area, into: clusters)
        let means = groups.map { g -> SIMD3<Float> in
            let w = g.reduce(Float(0)) { $0 + area[$1] }
            return g.reduce(SIMD3<Float>.zero) { $0 + color[$1] * area[$1] } / w
        }
        let areas = groups.map { $0.reduce(Float(0)) { $0 + area[$1] } }
        let body = Self.bodyGroups(means: means, areas: areas)

        var parts: [Part] = []
        for (g, tris) in groups.enumerated() where !tris.isEmpty {
            var part = Part(color: means[g], isBody: body.members.contains(g), positions: [], normals: [], indices: [])
            if let paint, part.isBody {
                let ratio = min(max(Self.luma(means[g]) / max(body.luma, 0.02), 0.25), 1.6)
                part.color = simd_clamp(SIMD3(Float(paint.red), Float(paint.green), Float(paint.blue)) * ratio,
                                        .zero, .one)
            }
            var remap: [UInt32: UInt32] = [:]
            for t in tris {
                for k in 0..<3 {
                    let v = indices[t * 3 + k]
                    if let i = remap[v] {
                        part.indices.append(i)
                    } else {
                        let i = UInt32(part.positions.count)
                        remap[v] = i
                        part.positions.append(positions[Int(v)])
                        part.normals.append(normals.isEmpty ? .zero : normals[Int(v)])
                        part.indices.append(i)
                    }
                }
            }
            parts.append(part)
        }
        return parts
    }

    static func luma(_ c: SIMD3<Float>) -> Float { 0.2126 * c.x + 0.7152 * c.y + 0.0722 * c.z }

    /// Splits triangles by color, always cutting the group with the most area along its widest channel at its
    /// area-weighted median. Deterministic, unlike k-means.
    static func medianCut(_ all: [Int], color: [SIMD3<Float>], area: [Float], into n: Int) -> [[Int]] {
        var groups = [all]
        while groups.count < n {
            var best: (index: Int, channel: Int, spread: Float)?
            for (i, g) in groups.enumerated() where g.count > 1 {
                var lo = SIMD3<Float>(repeating: 1), hi = SIMD3<Float>(repeating: 0)
                for t in g {
                    lo = simd_min(lo, color[t])
                    hi = simd_max(hi, color[t])
                }
                let span = hi - lo
                let channel = span.x >= span.y && span.x >= span.z ? 0 : span.y >= span.z ? 1 : 2
                // weight by area so a big body region keeps splitting into its shades before a small trim region
                let weight = g.reduce(Float(0)) { $0 + area[$1] }
                let spread = span[channel] * weight.squareRoot()
                if span[channel] > 0.02, spread > (best?.spread ?? 0) { best = (i, channel, spread) }
            }
            guard let best else { break }
            let g = groups[best.index].sorted { color[$0][best.channel] < color[$1][best.channel] }
            let half = g.reduce(Float(0)) { $0 + area[$1] } / 2
            var acc: Float = 0, cut = 1
            for (k, t) in g.enumerated() {
                acc += area[t]
                if acc >= half { cut = min(max(k + 1, 1), g.count - 1); break }
            }
            groups[best.index] = Array(g[..<cut])
            groups.append(Array(g[cut...]))
        }
        return groups
    }

    /// The main body color is the color group with the most area that isn't near-black (glass, tires), unless the
    /// whole car is dark; body groups share its hue and are within a range of its lightness.
    static func bodyGroups(means: [SIMD3<Float>], areas: [Float]) -> (members: Set<Int>, luma: Float) {
        let order = means.indices.sorted { areas[$0] > areas[$1] }
        let bright = order.filter { luma(means[$0]) >= 0.14 }
        let total = areas.reduce(0, +)
        let brightArea = bright.reduce(Float(0)) { $0 + areas[$1] }
        // a black car: most of its area is dark, so the dark group is the body
        guard let main = (brightArea > total * 0.3 ? bright.first : order.first) else { return ([], 1) }
        let ref = means[main], refLuma = max(luma(ref), 0.01)
        func chroma(_ c: SIMD3<Float>) -> SIMD3<Float> { c / max(c.x + c.y + c.z, 0.03) }
        var members: Set<Int> = []
        for g in means.indices {
            let l = luma(means[g]) / refLuma
            if l > 0.35, l < 2.2, simd_distance(chroma(means[g]), chroma(ref)) < 0.07 { members.insert(g) }
        }
        members.insert(main)
        return (members, refLuma)
    }
}

/// Reads glTF 2.0 accessors from a GLB's JSON and binary chunk.
private struct GLTFReader {
    let json: [String: Any]
    let bin: ArraySlice<UInt8>
    var positions: [SIMD3<Float>] = []
    var normals: [SIMD3<Float>] = []
    var colors: [SIMD3<Float>] = []
    var indices: [UInt32] = []

    init(json: [String: Any], bin: ArraySlice<UInt8>) {
        self.json = json
        self.bin = bin
    }

    private func list(_ key: String) -> [[String: Any]] { json[key] as? [[String: Any]] ?? [] }

    mutating func read() throws(CarScan.Failure) {
        let nodes = list("nodes")
        let scenes = list("scenes")
        let sceneIndex = json["scene"] as? Int ?? 0
        let roots = scenes.indices.contains(sceneIndex) ? scenes[sceneIndex]["nodes"] as? [Int] ?? [] : []
        // no scene: every mesh as is
        if roots.isEmpty {
            for m in list("meshes").indices { try addMesh(m, transform: matrix_identity_float4x4) }
        }
        for r in roots { try visit(r, nodes: nodes, parent: matrix_identity_float4x4, depth: 0) }
        guard !indices.isEmpty else { throw .unsupported("no triangles") }
    }

    private mutating func visit(_ n: Int, nodes: [[String: Any]], parent: simd_float4x4, depth: Int) throws(CarScan.Failure) {
        guard nodes.indices.contains(n), depth < 32 else { return }
        let node = nodes[n]
        let transform = parent * Self.local(node)
        if let mesh = node["mesh"] as? Int { try addMesh(mesh, transform: transform) }
        for child in node["children"] as? [Int] ?? [] {
            try visit(child, nodes: nodes, parent: transform, depth: depth + 1)
        }
    }

    private static func local(_ node: [String: Any]) -> simd_float4x4 {
        func floats(_ key: String) -> [Float]? { (node[key] as? [NSNumber])?.map(\.floatValue) }
        if let m = floats("matrix"), m.count == 16 {   // column-major
            return simd_float4x4(columns: (SIMD4(m[0], m[1], m[2], m[3]), SIMD4(m[4], m[5], m[6], m[7]),
                                           SIMD4(m[8], m[9], m[10], m[11]), SIMD4(m[12], m[13], m[14], m[15])))
        }
        var out = matrix_identity_float4x4
        if let s = floats("scale"), s.count == 3 { out = simd_float4x4(diagonal: SIMD4(s[0], s[1], s[2], 1)) }
        if let r = floats("rotation"), r.count == 4 {
            out = simd_float4x4(simd_quatf(ix: r[0], iy: r[1], iz: r[2], r: r[3])) * out
        }
        if let t = floats("translation"), t.count == 3 { out.columns.3 = SIMD4(t[0], t[1], t[2], 1) }
        return out
    }

    private mutating func addMesh(_ m: Int, transform: simd_float4x4) throws(CarScan.Failure) {
        let meshes = list("meshes")
        guard meshes.indices.contains(m) else { return }
        let normalMatrix = simd_float3x3(SIMD3(transform.columns.0.x, transform.columns.0.y, transform.columns.0.z),
                                         SIMD3(transform.columns.1.x, transform.columns.1.y, transform.columns.1.z),
                                         SIMD3(transform.columns.2.x, transform.columns.2.y, transform.columns.2.z))
            .inverse.transpose
        for prim in meshes[m]["primitives"] as? [[String: Any]] ?? [] {
            guard (prim["mode"] as? Int ?? 4) == 4, let attrs = prim["attributes"] as? [String: Int],
                  let p = attrs["POSITION"] else { continue }
            let pos = try floats(p, width: 3)
            let count = pos.count / 3
            let base = UInt32(positions.count)
            for i in 0..<count {
                let v = transform * SIMD4(pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2], 1)
                positions.append(SIMD3(v.x, v.y, v.z))
            }
            if let n = attrs["NORMAL"], case let nrm = try floats(n, width: 3), nrm.count == count * 3 {
                for i in 0..<count {
                    normals.append(simd_normalize(normalMatrix * SIMD3(nrm[i * 3], nrm[i * 3 + 1], nrm[i * 3 + 2])))
                }
            } else {
                normals.append(contentsOf: repeatElement(SIMD3<Float>(0, 1, 0), count: count))
            }
            if let c = attrs["COLOR_0"], let width = componentCount(c), width >= 3,
               case let col = try floats(c, width: width), col.count == count * width {
                for i in 0..<count { colors.append(SIMD3(col[i * width], col[i * width + 1], col[i * width + 2])) }
            } else {
                colors.append(contentsOf: repeatElement(SIMD3<Float>(repeating: 0.6), count: count))
            }
            if let ix = prim["indices"] as? Int {
                indices.append(contentsOf: try floats(ix, width: 1).map { base + UInt32($0) })
            } else {
                indices.append(contentsOf: (0..<UInt32(count - count % 3)).map { base + $0 })
            }
        }
    }

    private func componentCount(_ accessor: Int) -> Int? {
        let accessors = list("accessors")
        guard accessors.indices.contains(accessor) else { return nil }
        return ["SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4][accessors[accessor]["type"] as? String ?? ""]
    }

    /// An accessor's values as floats (normalized integers scaled to 0–1; index integers as is).
    private func floats(_ accessor: Int, width: Int) throws(CarScan.Failure) -> [Float] {
        let accessors = list("accessors"), views = list("bufferViews")
        guard accessors.indices.contains(accessor) else { throw .unsupported("accessor \(accessor)") }
        let a = accessors[accessor]
        guard let count = a["count"] as? Int, let type = a["componentType"] as? Int,
              let comps = componentCount(accessor), comps == width else { throw .unsupported("accessor \(accessor)") }
        guard let v = a["bufferView"] as? Int, views.indices.contains(v) else {
            return [Float](repeating: 0, count: count * width)   // sparse/zero-filled: not used by scans
        }
        let view = views[v]
        let size = [5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4][type] ?? 0
        guard size > 0 else { throw .unsupported("component type \(type)") }
        let normalized = a["normalized"] as? Bool ?? false
        let offset = (view["byteOffset"] as? Int ?? 0) + (a["byteOffset"] as? Int ?? 0)
        let stride = view["byteStride"] as? Int ?? size * width
        let start = bin.startIndex + offset
        guard count == 0 || start + stride * (count - 1) + size * width <= bin.endIndex else {
            throw .unsupported("accessor \(accessor) past the buffer")
        }
        var out = [Float]()
        out.reserveCapacity(count * width)
        bin.withUnsafeBytes { raw in
            let base = offset
            for i in 0..<count {
                for k in 0..<width {
                    let at = base + i * stride + k * size
                    let value: Float = switch type {
                    case 5126: raw.loadUnaligned(fromByteOffset: at, as: Float.self)
                    case 5125: Float(raw.loadUnaligned(fromByteOffset: at, as: UInt32.self))
                    case 5123: Float(raw.loadUnaligned(fromByteOffset: at, as: UInt16.self)) / (normalized ? 65535 : 1)
                    case 5121: Float(raw.loadUnaligned(fromByteOffset: at, as: UInt8.self)) / (normalized ? 255 : 1)
                    case 5122: Float(raw.loadUnaligned(fromByteOffset: at, as: Int16.self)) / (normalized ? 32767 : 1)
                    default: Float(raw.loadUnaligned(fromByteOffset: at, as: Int8.self)) / (normalized ? 127 : 1)
                    }
                    out.append(value)
                }
            }
        }
        return out
    }
}
