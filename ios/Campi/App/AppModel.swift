import CampiKit
import Foundation
import Observation
import UIKit
import WidgetKit

/// App-wide state: which PC we're paired with, and the client to talk to it.
@MainActor @Observable
final class AppModel {
    private(set) var connection: Connection?
    private(set) var client: APIClient?

    /// A pairing link opened from the Camera app, waiting for the user to confirm.
    var pendingLink: PairingLink?
    var isPairing = false
    var pairingError: String?
    /// Set when the PC rejected our token (revoked with `campi devices revoke`, or the PC's ui.db was reset).
    var unpairedReason: String?

    /// The selected tab, so deep links can switch it.
    var tab: MainTabs.Tab = .today
    /// A screen opened by a notification or widget tap, shown as a sheet over the tabs.
    var route: Route?

    enum Route: Identifiable, Hashable {
        case sighting(id: String)
        case status
        var id: Self { self }
    }

    /// Latest known version of every sighting the user acted on, so all screens agree (M2).
    let sightings = SightingStore()
    /// Save to Photos / Share downloads (M3).
    let saver = MediaSaver()
    /// Cardex card text per label, generated on device and cached (M4.5).
    let cardex = CardexStore()
    /// Notification permission, APNs token and push prefs (M5).
    let push = PushManager()
    /// The label collection, shared by Collection, the label picker and the make filter.
    private(set) var collection: LabelCollection?
    /// Set when a label correction changed counts; Collection reloads on next appearance.
    var collectionStale = false

    private let store: ConnectionStore

    init(store: ConnectionStore) {
        self.store = store
        #if DEBUG
        if ProcessInfo.processInfo.arguments.contains("-resetPairing") { store.clear() }   // UI tests
        #endif
        if let (conn, token) = store.load() {
            connection = conn
            client = APIClient(baseURL: conn.baseURL, token: token)
        }
    }

    var isPaired: Bool { client != nil }

    func handle(url: URL) {
        if let link = DeepLink(url: url) { open(link) }
    }

    /// Pairing links, and taps on notifications and the widget.
    func open(_ link: DeepLink) {
        switch link {
        case .pair(let p):
            pairingError = nil
            pendingLink = p
        case .sighting(let id):
            guard isPaired else { return }
            route = .sighting(id: id)
        case .today:
            tab = .today
            route = nil
        case .status:
            guard isPaired else { return }
            tab = .today
            route = .status
        }
    }

    // MARK: Push

    func pushTokenReceived(_ token: Data) {
        push.didRegister(token)
        Task { await syncPush() }
    }

    /// Sends the token, environment and prefs to the PC (each launch, on a new token, after a permission change).
    func syncPush() async {
        guard let client else { return }
        do { try await push.sync(client: client) } catch { report(error) }
    }

    func enablePush() async {
        await push.requestPermission()
        if push.permission != .allowed { await syncPush() }   // denied: tell the PC to stop (apns_token null)
    }

    static var defaultDeviceName: String { UIDevice.current.name }

    func pair(_ link: PairingLink, deviceName: String) async {
        isPairing = true
        pairingError = nil
        defer { isPairing = false }
        do {
            let result = try await APIClient.pair(baseURL: link.baseURL, code: link.code, deviceName: deviceName)
            let conn = Connection(baseURL: link.baseURL, serverName: result.serverName, deviceID: result.device.id,
                                  deviceName: result.device.name)
            try store.save(conn, token: result.token)
            connection = conn
            client = APIClient(baseURL: link.baseURL, token: result.token)
            pendingLink = nil
            unpairedReason = nil
            WidgetCenter.shared.reloadAllTimelines()
            Task { await enablePush() }   // asks once; later from Settings
        } catch let e as APIError {
            pairingError = switch e.code {
            case .invalidCode: "That code is wrong, already used, or expired. Run campi pair again on the PC."
            case .rateLimited: "Too many attempts. Wait a few minutes and try again."
            default: e.isUnreachable
                ? "Can't reach \(link.baseURL.host() ?? "the PC"). Is Tailscale connected on this iPhone?"
                : e.localizedDescription
            }
        } catch {
            pairingError = error.localizedDescription
        }
    }

    /// "Unpair this phone": revoke on the PC (best effort), then forget locally.
    func unpair() async {
        try? await client?.unpair()
        forget(reason: nil)
    }

    /// Call when any request fails; forgets the pairing if the PC no longer accepts our token.
    func report(_ error: Error) {
        if let e = error as? APIError, e.needsPairing {
            forget(reason: "\(connection?.serverName ?? "The PC") no longer accepts this iPhone. Pair it again.")
        }
    }

    /// Loads the collection once (or again when `force` or stale).
    func loadCollection(force: Bool = false) async throws -> LabelCollection {
        if let collection, !force, !collectionStale { return collection }
        guard let client else { throw APIError.unreachable("not paired") }
        let c = try await client.collection()
        collection = c
        collectionStale = false
        return c
    }

    private func forget(reason: String?) {
        store.clear()
        connection = nil
        client = nil
        collection = nil
        sightings.reset()
        push.reset()
        route = nil
        tab = .today
        WidgetStore().clear()
        WidgetCenter.shared.reloadAllTimelines()
        unpairedReason = reason
        Task { await ImageLoader.shared.clear() }
    }
}
