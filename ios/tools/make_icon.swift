// Draws the Campi app icon (light, dark and tinted variants) into the asset catalog.
//
//     swift tools/make_icon.swift
//
// CoreGraphics only: a camera-lens ring around a car, with a red "recording" dot. No SF Symbols (their license
// doesn't allow use in app icons).
import CoreGraphics
import Foundation
import ImageIO

let size = 1024
let out = URL(fileURLWithPath: "Campi/Resources/Assets.xcassets/AppIcon.appiconset")

struct Palette {
    var top: CGColor, bottom: CGColor   // background gradient
    var glyph: CGColor                  // ring and car
    var cutout: CGColor                 // windows and hubs
    var dot: CGColor
}

func rgb(_ hex: UInt32, _ a: CGFloat = 1) -> CGColor {
    CGColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: a)
}

let variants: [(file: String, palette: Palette)] = [
    ("AppIcon.png", Palette(top: rgb(0x0B3D91), bottom: rgb(0x1E9BFF), glyph: rgb(0xFFFFFF), cutout: rgb(0x1466C8),
                            dot: rgb(0xFF3B30))),
    ("AppIcon-Dark.png", Palette(top: rgb(0x0A0F1C), bottom: rgb(0x1A2440), glyph: rgb(0x5AB0FF), cutout: rgb(0x131B30),
                                 dot: rgb(0xFF453A))),
    // tinted: grayscale on black; iOS applies the tint
    ("AppIcon-Tinted.png", Palette(top: rgb(0x000000), bottom: rgb(0x000000), glyph: rgb(0xFFFFFF), cutout: rgb(0x000000),
                                   dot: rgb(0xB0B0B0))),
]

func draw(_ p: Palette) -> CGImage {
    let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpace(name: CGColorSpace.sRGB)!,
                        bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!   // app icons have no alpha
    // top-left origin, like the numbers below
    ctx.translateBy(x: 0, y: CGFloat(size))
    ctx.scaleBy(x: 1, y: -1)

    let gradient = CGGradient(colorsSpace: nil, colors: [p.top, p.bottom] as CFArray, locations: [0, 1])!
    ctx.drawLinearGradient(gradient, start: .zero, end: CGPoint(x: size, y: size), options: [])

    // lens ring
    ctx.setStrokeColor(p.glyph)
    ctx.setLineWidth(48)
    ctx.strokeEllipse(in: CGRect(x: 512 - 330, y: 540 - 330, width: 660, height: 660))

    // car: body, cabin, windows, wheels
    ctx.setFillColor(p.glyph)
    ctx.addPath(CGPath(roundedRect: CGRect(x: 292, y: 520, width: 440, height: 92), cornerWidth: 34, cornerHeight: 34,
                       transform: nil))
    ctx.fillPath()
    let cabin = CGMutablePath()
    cabin.move(to: CGPoint(x: 360, y: 530))
    cabin.addLine(to: CGPoint(x: 428, y: 448))
    cabin.addQuadCurve(to: CGPoint(x: 450, y: 438), control: CGPoint(x: 436, y: 438))
    cabin.addLine(to: CGPoint(x: 590, y: 438))
    cabin.addQuadCurve(to: CGPoint(x: 614, y: 450), control: CGPoint(x: 604, y: 438))
    cabin.addLine(to: CGPoint(x: 684, y: 530))
    cabin.closeSubpath()
    ctx.addPath(cabin)
    ctx.fillPath()
    ctx.setFillColor(p.cutout)
    let windows = CGMutablePath()
    windows.move(to: CGPoint(x: 398, y: 516))
    windows.addLine(to: CGPoint(x: 449, y: 462))
    windows.addLine(to: CGPoint(x: 512, y: 462))
    windows.addLine(to: CGPoint(x: 512, y: 516))
    windows.closeSubpath()
    windows.move(to: CGPoint(x: 528, y: 462))
    windows.addLine(to: CGPoint(x: 592, y: 462))
    windows.addLine(to: CGPoint(x: 643, y: 516))
    windows.addLine(to: CGPoint(x: 528, y: 516))
    windows.closeSubpath()
    ctx.addPath(windows)
    ctx.fillPath()
    for x in [392.0, 632.0] {
        ctx.setFillColor(p.glyph)
        ctx.fillEllipse(in: CGRect(x: x - 52, y: 612 - 52, width: 104, height: 104))
        ctx.setFillColor(p.cutout)
        ctx.fillEllipse(in: CGRect(x: x - 22, y: 612 - 22, width: 44, height: 44))
    }

    // recording dot, on the ring at about 1:30
    ctx.setFillColor(p.dot)
    ctx.fillEllipse(in: CGRect(x: 745 - 50, y: 307 - 50, width: 100, height: 100))
    return ctx.makeImage()!
}

for v in variants {
    let url = out.appendingPathComponent(v.file)
    let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil)!
    CGImageDestinationAddImage(dest, draw(v.palette), nil)
    guard CGImageDestinationFinalize(dest) else { fatalError("couldn't write \(url.path)") }
    print("wrote \(url.path)")
}
