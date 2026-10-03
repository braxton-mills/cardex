import Foundation

/// A paired client (api-contract §5.9).
public struct Device: Codable, Hashable, Sendable, Identifiable {
    public enum Platform: String, TolerantEnum { case ios, desktop, other, unknown }
    public enum Environment: String, TolerantEnum { case sandbox, production, unknown }

    public struct Push: Codable, Hashable, Sendable {
        public var enabled: Bool
        public var environment: Environment?
        public var prefs: PushPrefs
    }

    public var id: String
    public var name: String
    public var platform: Platform
    public var createdAt: PCDate
    public var lastSeenAt: PCDate?
    public var push: Push
}

public struct PushPrefs: Codable, Hashable, Sendable {
    public var newCatch: Bool
    public var rare: Bool
    public var discovered: Bool
    public var serviceAlerts: Bool

    public init(newCatch: Bool = true, rare: Bool = false, discovered: Bool = true, serviceAlerts: Bool = true) {
        self.newCatch = newCatch
        self.rare = rare
        self.discovered = discovered
        self.serviceAlerts = serviceAlerts
    }
}

public struct PairResult: Codable, Hashable, Sendable {
    public var token: String
    public var device: Device
    public var serverName: String
    public var apiVersion: Int
}

/// The `campi` part of a push payload (api-contract §9.3).
public struct PushInfo: Codable, Hashable, Sendable {
    public enum Kind: String, TolerantEnum { case newCatch = "new_catch", rare, discovered, service, unknown }
    public enum Alert: String, TolerantEnum {
        case captureDisconnected = "capture_disconnected", sightingsCrashLooping = "sightings_crash_looping",
             diskLow = "disk_low", renderFailing = "render_failing", recovered, unknown
    }

    public var type: Kind
    public var sightingId: String?
    public var label: String?
    public var day: PCDay?
    /// Unsigned path: fetch it with the bearer token.
    public var crop: MediaPath?
    public var alert: Alert?

    /// Reads `campi` from a notification's `userInfo`.
    public init?(userInfo: [AnyHashable: Any]) {
        guard let campi = userInfo["campi"],
              JSONSerialization.isValidJSONObject(campi),
              let data = try? JSONSerialization.data(withJSONObject: campi),
              let info = try? JSONDecoder.campi.decode(PushInfo.self, from: data) else { return nil }
        self = info
    }
}
