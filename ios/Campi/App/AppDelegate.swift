import CampiKit
import UIKit
import UserNotifications

/// Owns the app model, so a notification tap that launches the app has somewhere to go before any view exists.
@MainActor
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    let model = AppModel(store: SharedConfig.connectionStore)

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        if model.isPaired { Task { await model.push.refreshPermission() } }
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        model.pushTokenReceived(deviceToken)
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: any Error) {
        model.push.registrationFailed(error)
    }

    // Show Campi pushes while the app is open too.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                            withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list, .sound])
    }

    // Tapping a push opens its sighting (or the status, for service alerts).
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                            withCompletionHandler completionHandler: @escaping () -> Void) {
        let link = PushInfo(userInfo: response.notification.request.content.userInfo).flatMap(DeepLink.init(push:))
        if let link { Task { @MainActor in self.model.open(link) } }
        completionHandler()
    }
}
