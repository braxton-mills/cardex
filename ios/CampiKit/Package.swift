// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "CampiKit",
    platforms: [.iOS("26.0"), .macOS("26.0")],
    products: [.library(name: "CampiKit", targets: ["CampiKit"])],
    targets: [
        .target(name: "CampiKit"),
        .testTarget(name: "CampiKitTests", dependencies: ["CampiKit"]),
    ]
)
