import Foundation

/// Values the app, the widget and the notification extension share. Each target's Info.plist carries
/// `CampiAppGroup` and `CampiKeychainGroup` (set in project.yml).
public enum SharedConfig {
    public static var appGroup: String? { Bundle.main.object(forInfoDictionaryKey: "CampiAppGroup") as? String }

    public static var keychainGroup: String? {
        guard let g = Bundle.main.object(forInfoDictionaryKey: "CampiKeychainGroup") as? String,
              !g.hasPrefix("$(") else { return nil }   // unsigned builds leave the prefix unexpanded
        return g
    }

    /// The pairing (base URL in App Group defaults, token in the shared Keychain group).
    public static var connectionStore: ConnectionStore {
        ConnectionStore(appGroup: appGroup, tokens: KeychainTokenStore(accessGroup: keychainGroup))
    }

    /// The App Group container (falls back to Caches when the group isn't available, e.g. unsigned builds).
    public static var containerURL: URL {
        if let g = appGroup, let url = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: g) {
            return url
        }
        return FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
    }

    public static var defaults: UserDefaults { appGroup.flatMap(UserDefaults.init(suiteName:)) ?? .standard }
}
