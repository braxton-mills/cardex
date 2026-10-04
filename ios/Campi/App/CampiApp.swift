import CampiKit
import SwiftUI
import WidgetKit

@main
struct CampiApp: App {
    @UIApplicationDelegateAdaptor private var delegate: AppDelegate
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(delegate.model)
                .onOpenURL { delegate.model.handle(url: $0) }
        }
        .onChange(of: scenePhase) { _, phase in
            // the widget picks up whatever changed while the app was open
            if phase == .background { WidgetCenter.shared.reloadAllTimelines() }
        }
    }
}
