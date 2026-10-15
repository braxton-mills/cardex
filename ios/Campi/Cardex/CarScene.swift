import CampiKit
import Metal
import RealityKit
import SwiftUI
import UIKit

/// What a card's 3D car is made from: the PC's scan of the real car when there is one, else a shape built on the
/// phone. Equal arts draw the same car, so they share a snapshot.
enum CarArt: Hashable {
    case shape(CarShape, PaintColor)
    case scan(ScannedCar)

    /// Identifies the art without holding a scan's geometry, for caches that outlive it.
    enum Key: Hashable {
        case shape(CarShape, PaintColor)
        case scan(String, PaintColor?)
    }

    var key: Key {
        switch self {
        case .shape(let shape, let paint): .shape(shape, paint)
        case .scan(let s): .scan(s.key, s.paint)
        }
    }
}

/// A loaded scan in its color regions, repainted in `paint` (nil: the photo's own colors). Identified by its file
/// and paint.
struct ScannedCar: Hashable {
    let key: String
    let paint: PaintColor?
    let parts: [CarScan.Part]

    /// Splits the scan into regions off the main actor: tens of thousands of triangles.
    static func make(key: String, scan: CarScan, paint: PaintColor?) async -> ScannedCar {
        let parts = await Task.detached(priority: .userInitiated) { scan.parts(paint: paint) }.value
        return ScannedCar(key: key, paint: paint, parts: parts)
    }

    static func == (a: ScannedCar, b: ScannedCar) -> Bool { a.key == b.key && a.paint == b.paint }
    func hash(into h: inout Hasher) {
        h.combine(key)
        h.combine(paint)
    }
}

/// The Cardex 3D car, centered and scaled to a 1 m box, lit by a soft procedural studio: the PC's scan in flat color
/// regions (`CarScan.parts`), or a low-poly mesh built from the car's `CarShape` (`CarMesh`) in its seen color.
@MainActor
enum CarScene {
    /// The three-quarter front view the binder snapshots use and the turntable starts from.
    nonisolated static let restingYaw: Float = .pi / 5

    private static var meshes: [CarShape: (MeshResource, [CarMesh.Part])] = [:]
    private static var scans: [CarArt.Key: (MeshResource, [any RealityKit.Material])] = [:]
    private static var studio: EnvironmentResource?

    /// A fresh car, centered on the origin.
    static func car(_ art: CarArt) throws -> Entity {
        switch art {
        case .shape(let shape, let paint): try car(shape, paint: paint)
        case .scan(let scanned): try car(scanned)
        }
    }

    /// A fresh painted car of this shape, centered on the origin.
    static func car(_ shape: CarShape, paint: PaintColor) throws -> Entity {
        let (mesh, parts) = try resource(for: shape)
        return centered(ModelEntity(mesh: mesh, materials: parts.map { material($0, paint: paint) }))
    }

    private static func car(_ scanned: ScannedCar) throws -> Entity {
        let key = CarArt.scan(scanned).key
        if scans[key] == nil {
            if scans.count >= 8 { scans.removeAll() }   // a scan mesh is big; keep the ones on screen
            var descriptors: [MeshDescriptor] = []
            var materials: [any RealityKit.Material] = []
            for part in scanned.parts where !part.indices.isEmpty {
                var d = MeshDescriptor(name: "scan\(materials.count)")
                d.positions = MeshBuffers.Positions(part.positions)
                d.normals = MeshBuffers.Normals(part.normals)
                d.primitives = .triangles(part.indices)
                d.materials = .allFaces(UInt32(materials.count))
                descriptors.append(d)
                materials.append(material(part, paint: scanned.paint))
            }
            scans[key] = (try MeshResource.generate(from: descriptors), materials)
        }
        let (mesh, materials) = scans[key]!
        return centered(ModelEntity(mesh: mesh, materials: materials))
    }

    private static func centered(_ model: ModelEntity) -> Entity {
        let holder = Entity()
        holder.addChild(model)
        let bounds = model.visualBounds(relativeTo: holder)
        let scale = 1 / max(bounds.extents.x, bounds.extents.y, bounds.extents.z, 0.001)
        model.scale = .init(repeating: scale)
        model.position = -bounds.center * scale
        return holder
    }

    /// One mesh per shape (cached): a part per material, in the order of the returned parts.
    private static func resource(for shape: CarShape) throws -> (MeshResource, [CarMesh.Part]) {
        if let cached = meshes[shape] { return cached }
        let mesh = CarMesh(shape)
        var descriptors: [MeshDescriptor] = []
        var parts: [CarMesh.Part] = []
        for part in CarMesh.Part.allCases {
            guard let piece = mesh.pieces[part], !piece.indices.isEmpty else { continue }
            var d = MeshDescriptor(name: "\(part)")
            d.positions = MeshBuffers.Positions(piece.positions)
            d.normals = MeshBuffers.Normals(piece.normals)
            d.primitives = .triangles(piece.indices)
            d.materials = .allFaces(UInt32(parts.count))
            descriptors.append(d)
            parts.append(part)
        }
        let resource = (try MeshResource.generate(from: descriptors), parts)
        meshes[shape] = resource
        return resource
    }

    /// A scan region: its photo color (or the paint), with car paint's clear coat on the body.
    private static func material(_ part: CarScan.Part, paint: PaintColor?) -> any RealityKit.Material {
        var m = PhysicallyBasedMaterial()
        m.baseColor = .init(tint: UIColor(red: CGFloat(part.color.x), green: CGFloat(part.color.y),
                                          blue: CGFloat(part.color.z), alpha: 1))
        if part.isBody {
            let metallic = paint?.metallic ?? false
            m.metallic = .init(floatLiteral: metallic ? 0.5 : 0.1)
            m.roughness = .init(floatLiteral: metallic ? 0.35 : 0.42)
            m.clearcoat = .init(floatLiteral: 0.8)
            m.clearcoatRoughness = .init(floatLiteral: 0.1)
        } else {
            m.metallic = .init(floatLiteral: 0.05)
            m.roughness = .init(floatLiteral: 0.55)
        }
        return m
    }

    private static func material(_ part: CarMesh.Part, paint: PaintColor) -> any RealityKit.Material {
        var m = PhysicallyBasedMaterial()
        switch part {
        case .paint:
            m.baseColor = .init(tint: UIColor(red: paint.red, green: paint.green, blue: paint.blue, alpha: 1))
            m.metallic = .init(floatLiteral: paint.metallic ? 0.6 : 0.15)
            m.roughness = .init(floatLiteral: paint.metallic ? 0.3 : 0.36)
            m.clearcoat = .init(floatLiteral: 1)
            m.clearcoatRoughness = .init(floatLiteral: 0.06)
        case .glass:
            m.baseColor = .init(tint: UIColor(red: 0.1, green: 0.13, blue: 0.19, alpha: 1))
            m.metallic = .init(floatLiteral: 0.6)
            m.roughness = .init(floatLiteral: 0.06)
        case .trim:
            m.baseColor = .init(tint: UIColor(white: 0.12, alpha: 1))
            m.roughness = .init(floatLiteral: 0.7)
        case .tire:
            m.baseColor = .init(tint: UIColor(white: 0.07, alpha: 1))
            m.roughness = .init(floatLiteral: 0.9)
        case .rim:
            m.baseColor = .init(tint: UIColor(white: 0.78, alpha: 1))
            m.metallic = .init(floatLiteral: 1)
            m.roughness = .init(floatLiteral: 0.25)
        case .headlight:
            m.baseColor = .init(tint: .white)
            m.emissiveColor = .init(color: .white)
            m.emissiveIntensity = 0.6
        case .taillight:
            m.baseColor = .init(tint: .red)
            m.emissiveColor = .init(color: .red)
            m.emissiveIntensity = 0.8
        }
        return m
    }

    /// Looks at the car from the front three-quarter, a little above.
    static func camera() -> PerspectiveCamera {
        let camera = PerspectiveCamera()
        camera.camera.fieldOfViewInDegrees = 26
        camera.look(at: [0, -0.04, 0], from: [0, 0.62, 2.35], relativeTo: nil)
        return camera
    }

    /// A key light from the upper front left, for crisp shading on top of the studio reflections.
    static func keyLight() -> DirectionalLight {
        let light = DirectionalLight()
        light.light.intensity = 2200
        light.look(at: .zero, from: [-1.2, 2.2, 1.6], relativeTo: nil)
        return light
    }

    /// Soft studio environment: a bright sky with two softboxes over a dark floor, drawn once.
    static func environment() throws -> EnvironmentResource {
        if let studio { return studio }
        let w = 512, h = 256
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
            throw CocoaError(.featureUnsupported)
        }
        // CoreGraphics' origin is bottom left; the equirectangular image's top rows are the sky.
        let colors = [CGColor(gray: 0.08, alpha: 1), CGColor(gray: 0.3, alpha: 1), CGColor(gray: 0.82, alpha: 1),
                      CGColor(gray: 0.95, alpha: 1)] as CFArray
        let gradient = CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB), colors: colors,
                                  locations: [0, 0.48, 0.56, 1])!
        ctx.drawLinearGradient(gradient, start: .zero, end: CGPoint(x: 0, y: h), options: [])
        ctx.setFillColor(CGColor(gray: 1, alpha: 1))
        ctx.fill(CGRect(x: 60, y: 175, width: 110, height: 50))
        ctx.fill(CGRect(x: 300, y: 185, width: 150, height: 34))
        let env = try EnvironmentResource(equirectangular: ctx.makeImage()!, withName: "campi.studio")
        studio = env
        return env
    }

    /// Lights `root` and everything under it with the studio.
    static func light(_ root: Entity) throws {
        let ibl = Entity()
        ibl.components.set(ImageBasedLightComponent(source: .single(try environment()), intensityExponent: 0.3))
        root.addChild(ibl)
        receive(ibl, root)
        root.addChild(keyLight())
    }

    private static func receive(_ ibl: Entity, _ e: Entity) {
        if e.components.has(ModelComponent.self) {
            e.components.set(ImageBasedLightReceiverComponent(imageBasedLight: ibl))
        }
        for child in e.children { receive(ibl, child) }
    }
}

/// Turns the car slowly and leans it with the card's tilt, for depth.
struct TurntableComponent: Component {
    var yaw: Float = CarScene.restingYaw
    /// Radians per second; 0 holds the resting view.
    var speed: Float = 0.35
    /// The card's tilt, -1...1 on each axis.
    var tilt: SIMD2<Float> = .zero
}

struct TurntableSystem: System {
    private static let query = EntityQuery(where: .has(TurntableComponent.self))

    init(scene: RealityKit.Scene) {}

    func update(context: SceneUpdateContext) {
        for e in context.entities(matching: Self.query, updatingSystemWhen: .rendering) {
            guard var t = e.components[TurntableComponent.self] else { continue }
            t.yaw += Float(context.deltaTime) * t.speed
            e.components.set(t)
            e.orientation = simd_quatf(angle: -t.tilt.y * 0.35, axis: [1, 0, 0])
                * simd_quatf(angle: t.yaw + t.tilt.x * 0.6, axis: [0, 1, 0])
        }
    }
}

/// The live 3D car on an inspected card.
struct CarModelView: View {
    let art: CarArt
    var tilt: CGPoint = .zero
    var spins = true

    private static let registered: Void = {
        TurntableComponent.registerComponent()
        TurntableSystem.registerSystem()
    }()

    var body: some View {
        RealityView { content in
            _ = Self.registered
            content.camera = .virtual
            let root = Entity()
            guard let car = try? CarScene.car(art) else { return }
            car.name = "car"
            car.components.set(TurntableComponent(speed: spins ? 0.35 : 0))
            root.addChild(car)
            try? CarScene.light(root)
            content.add(root)
            content.add(CarScene.camera())
        } update: { content in
            for e in content.entities {
                guard let car = e.findEntity(named: "car"), var t = car.components[TurntableComponent.self] else { continue }
                t.tilt = SIMD2(Float(tilt.x), Float(tilt.y))
                car.components.set(t)
            }
        }
        .id(art)
        .accessibilityHidden(true)
    }
}

/// Still renders of each car for binder thumbnails, so a grid of cards never runs live 3D scenes.
@MainActor
final class CarSnapshots {
    static let shared = CarSnapshots()

    private var images: [CarArt.Key: UIImage] = [:]
    private var inflight: [CarArt.Key: Task<UIImage?, Never>] = [:]
    /// Renders run one at a time on one renderer, instead of a renderer (and GPU work) per visible card at once.
    private var renderer: RealityRenderer?
    private var rendering = false
    private var waiting: [CheckedContinuation<Void, Never>] = []

    /// The art-window size the snapshot is rendered for, in pixels.
    static let pixelSize = CGSize(width: 720, height: 480)

    func cached(_ art: CarArt) -> UIImage? { images[art.key] }

    func image(_ art: CarArt) async -> UIImage? {
        let key = art.key
        if let image = images[key] { return image }
        if let running = inflight[key] { return await running.value }
        let task = Task { @MainActor in
            await self.acquire()
            defer { self.release() }
            return await self.render(art)
        }
        inflight[key] = task
        let image = await task.value
        inflight[key] = nil
        images[key] = image
        return image
    }

    private func acquire() async {
        if !rendering {
            rendering = true
            return
        }
        await withCheckedContinuation { waiting.append($0) }
    }

    private func release() {
        if waiting.isEmpty { rendering = false } else { waiting.removeFirst().resume() }
    }

    private func render(_ art: CarArt) async -> UIImage? {
        let w = Int(Self.pixelSize.width), h = Int(Self.pixelSize.height)
        guard let device = MTLCreateSystemDefaultDevice() else { return nil }
        let desc = MTLTextureDescriptor.texture2DDescriptor(pixelFormat: .rgba8Unorm_srgb, width: w, height: h,
                                                            mipmapped: false)
        desc.usage = [.renderTarget, .shaderRead]
        desc.storageMode = .shared
        guard let texture = device.makeTexture(descriptor: desc) else { return nil }
        do {
            let renderer = try self.renderer ?? RealityRenderer()
            self.renderer = renderer
            renderer.entities.removeAll()
            let car = try CarScene.car(art)
            car.orientation = simd_quatf(angle: CarScene.restingYaw, axis: [0, 1, 0])
            let camera = CarScene.camera()
            renderer.entities.append(contentsOf: [car, camera, CarScene.keyLight()])
            renderer.activeCamera = camera
            renderer.lighting.resource = try CarScene.environment()
            renderer.lighting.intensityExponent = 0.3
            renderer.cameraSettings.colorBackground = .color(CGColor(gray: 0, alpha: 0))
            renderer.cameraSettings.antialiasing = .multisample4X
            let output = try RealityRenderer.CameraOutput(.singleProjection(colorTexture: texture))
            try await withCheckedThrowingContinuation { (done: CheckedContinuation<Void, any Error>) in
                do {
                    try renderer.updateAndRender(deltaTime: 1 / 60, cameraOutput: output,
                                                 onComplete: { _ in done.resume() })
                } catch {
                    done.resume(throwing: error)
                }
            }
        } catch {
            return nil
        }
        var bytes = [UInt8](repeating: 0, count: w * h * 4)
        texture.getBytes(&bytes, bytesPerRow: w * 4, from: MTLRegionMake2D(0, 0, w, h), mipmapLevel: 0)
        guard let provider = CGDataProvider(data: Data(bytes) as CFData),
              let cg = CGImage(width: w, height: h, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: w * 4,
                               space: CGColorSpace(name: CGColorSpace.sRGB)!,
                               bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue),
                               provider: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent)
        else { return nil }
        return UIImage(cgImage: cg, scale: 3, orientation: .up)
    }
}
