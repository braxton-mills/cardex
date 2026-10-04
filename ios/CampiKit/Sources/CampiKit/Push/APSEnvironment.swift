import Foundation

/// Which APNs environment this build's device token belongs to, for `PUT /api/devices/me/push` (api-contract §4.2).
///
/// It follows the signing, not the build configuration: a Release build installed from Xcode is still
/// development-signed, so its token is a sandbox token. The `aps-environment` entitlement in the embedded
/// provisioning profile says which (`development` → sandbox, `production` → production). App Store and TestFlight
/// builds have no embedded profile and are production; the simulator is sandbox.
public enum APSEnvironment {
    public static var current: Device.Environment {
        #if targetEnvironment(simulator)
        return .sandbox
        #else
        guard let url = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision"),
              let data = try? Data(contentsOf: url) else { return .production }
        return fromProvisioningProfile(data) ?? .sandbox
        #endif
    }

    /// Reads `Entitlements → aps-environment` from a provisioning profile (a CMS envelope around an XML plist).
    public static func fromProvisioningProfile(_ data: Data) -> Device.Environment? {
        guard let start = data.range(of: Data("<?xml".utf8)),
              let end = data.range(of: Data("</plist>".utf8), in: start.lowerBound..<data.endIndex),
              let plist = try? PropertyListSerialization.propertyList(
                  from: data[start.lowerBound..<end.upperBound], format: nil) as? [String: Any],
              let ents = plist["Entitlements"] as? [String: Any],
              let aps = ents["aps-environment"] as? String else { return nil }
        switch aps {
        case "development": return .sandbox
        case "production": return .production
        default: return nil
        }
    }
}

extension Data {
    /// APNs device token as the lowercase hex string APNs and the PC expect.
    public var apnsTokenString: String { map { String(format: "%02x", $0) }.joined() }
}
