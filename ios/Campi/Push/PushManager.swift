import CampiKit
import Foundation
import Observation
import UIKit
import UserNotifications

/// Notification permission, the APNs token, and this device's push settings on the PC (api-contract §4.2, §9).
@MainActor @Observable
final class PushManager {
    enum Permission { case unknown, notDetermined, denied, allowed }

    private(set) var permission: Permission = .unknown
    /// Hex APNs device token, once iOS hands it over.
    private(set) var token: String?
    /// This device's prefs as the PC has them.
    private(set) var prefs: PushPrefs?
    /// Whether the PC has a token for this device (`push.enabled`).
    private(set) var enabledOnPC = false
    private(set) var registrationError: String?
    let environment = APSEnvironment.current

    func refreshPermission() async {
        let status = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
        permission = switch status {
        case .authorized, .provisional, .ephemeral: .allowed
        case .denied: .denied
        case .notDetermined: .notDetermined
        @unknown default: .unknown
        }
        // a fresh token each launch: didRegister… then syncs it to the PC
        if permission == .allowed { UIApplication.shared.registerForRemoteNotifications() }
    }

    /// Asks once (after pairing, or from Settings).
    func requestPermission() async {
        _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
        await refreshPermission()
    }

    func didRegister(_ deviceToken: Data) {
        token = deviceToken.apnsTokenString
        registrationError = nil
    }

    func registrationFailed(_ error: any Error) {
        registrationError = error.localizedDescription
    }

    func loadPrefs(client: APIClient) async throws {
        let me = try await client.me()
        prefs = me.push.prefs
        enabledOnPC = me.push.enabled
    }

    /// Tells the PC our token (or `null` when notifications are off), environment and prefs.
    func sync(client: APIClient) async throws {
        if permission == .allowed && token == nil { return }   // registration still pending; it syncs when it lands
        if prefs == nil { try await loadPrefs(client: client) }
        guard let prefs else { return }
        let device = try await client.updatePush(apnsToken: permission == .allowed ? token : nil,
                                                 environment: environment, prefs: prefs)
        self.prefs = device.push.prefs
        enabledOnPC = device.push.enabled
    }

    /// Changes one pref optimistically; reverts if the PC doesn't take it.
    func set(_ key: WritableKeyPath<PushPrefs, Bool>, _ value: Bool, client: APIClient) async throws {
        guard var p = prefs else { return }
        let old = p
        p[keyPath: key] = value
        prefs = p
        do {
            try await sync(client: client)
        } catch {
            prefs = old
            throw error
        }
    }

    func reset() {
        prefs = nil
        enabledOnPC = false
    }
}
