import CampiKit
import Metal
import RealityKit
import SwiftUI
import UIKit

/// The Cardex 3D car: a bundled body-style model (Resources/Cars, from tools/make_car_models.py) with its "Paint"
/// mesh painted the car's seen color, centered and scaled to a 1 m box, lit by a soft procedural studio.
@MainActor
enum CarScene {
    /// The three-quarter front view the binder snapshots use and the turntable starts from.
    nonisolated static let restingYaw: Float = .pi / 5

    private static var prototypes: [String: Entity] = [:]
    private static var studio: EnvironmentResource?

    /// A fresh painted copy of the style's car, centered on the origin.
    static func car(_ style: CarBodyStyle, paint: PaintColor) async throws -> Entity {
        let name = style.modelName
        let prototype: Entity
        if let p = prototypes[name] {
            prototype = p
        } else {
            prototype = try await Entity(named: name, in: .main)
            prototypes[name] = prototype
        }
        let model = prototype.clone(recursive: true)
        apply(paint, to: model)
        let holder = Entity()
        holder.addChild(model)
        let bounds = model.visualBounds(relativeTo: holder)
        let scale = 1 / max(bounds.extents.x, bounds.extents.y, bounds.extents.z, 0.001)
        model.scale = .init(repeating: scale)
        model.position = -bounds.center * scale
        return holder
    }

    private static func apply(_ paint: PaintColor, to car: Entity) {
        guard let body = car.findEntity(named: "Paint"), var model = body.components[ModelComponent.self] else { return }
        var m = PhysicallyBasedMaterial()
        m.baseColor = .init(tint: UIColor(red: paint.red, green: paint.green, blue: paint.blue, alpha: 1))
        m.metallic = .init(floatLiteral: paint.metallic ? 0.65 : 0.1)
        m.roughness = .init(floatLiteral: paint.metallic ? 0.3 : 0.38)
        m.clearcoat = .init(floatLiteral: 1)
        m.clearcoatRoughness = .init(floatLiteral: 0.06)
        model.materials = model.materials.map { _ in m }
        body.components.set(model)
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
    let style: CarBodyStyle
    let paint: PaintColor
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
            guard let car = try? await CarScene.car(style, paint: paint) else { return }
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
        .id("\(style.rawValue)-\(paint)")
        .accessibilityHidden(true)
    }
}

/// Still renders of each (style, paint) for binder thumbnails, so a grid of cards never runs live 3D scenes.
@MainActor
final class CarSnapshots {
    static let shared = CarSnapshots()

    private struct Key: Hashable {
        var style: CarBodyStyle
        var paint: PaintColor
    }

    private var images: [Key: UIImage] = [:]
    private var inflight: [Key: Task<UIImage?, Never>] = [:]

    /// The art-window size the snapshot is rendered for, in pixels.
    static let pixelSize = CGSize(width: 720, height: 480)

    func cached(_ style: CarBodyStyle, paint: PaintColor) -> UIImage? { images[Key(style: style, paint: paint)] }

    func image(_ style: CarBodyStyle, paint: PaintColor) async -> UIImage? {
        let key = Key(style: style, paint: paint)
        if let image = images[key] { return image }
        if let running = inflight[key] { return await running.value }
        let task = Task { @MainActor in await Self.render(style, paint: paint) }
        inflight[key] = task
        let image = await task.value
        inflight[key] = nil
        images[key] = image
        return image
    }

    private static func render(_ style: CarBodyStyle, paint: PaintColor) async -> UIImage? {
        let w = Int(pixelSize.width), h = Int(pixelSize.height)
        guard let device = MTLCreateSystemDefaultDevice() else { return nil }
        let desc = MTLTextureDescriptor.texture2DDescriptor(pixelFormat: .rgba8Unorm_srgb, width: w, height: h,
                                                            mipmapped: false)
        desc.usage = [.renderTarget, .shaderRead]
        desc.storageMode = .shared
        guard let texture = device.makeTexture(descriptor: desc) else { return nil }
        do {
            let renderer = try RealityRenderer()
            let car = try await CarScene.car(style, paint: paint)
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
