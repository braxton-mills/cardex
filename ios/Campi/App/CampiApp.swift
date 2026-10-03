import CampiKit
import SwiftUI

@main
struct CampiApp: App {
    @State private var model = AppModel(store: AppConfig.connectionStore)

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .onOpenURL { model.handle(url: $0) }
        }
    }
}

/// Values shared with the widget and notification extension (set in project.yml → Info.plist).
enum AppConfig {
    static let appGroup = Bundle.main.object(forInfoDictionaryKey: "CampiAppGroup") as? String
    static let keychainGroup: String? = {
        guard let g = Bundle.main.object(forInfoDictionaryKey: "CampiKeychainGroup") as? String,
              !g.hasPrefix("$(") else { return nil }   // unsigned builds leave the prefix unexpanded
        return g
    }()

    static var connectionStore: ConnectionStore {
        ConnectionStore(appGroup: appGroup, tokens: KeychainTokenStore(accessGroup: keychainGroup))
    }
}
