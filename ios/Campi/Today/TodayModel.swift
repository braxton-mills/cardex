import CampiKit
import Foundation
import Observation

@MainActor @Observable
final class TodayModel {
    private(set) var status: Status?
    private(set) var latest: [Sighting] = []
    private(set) var error: APIError?
    private(set) var lastRefresh: Date?
    private(set) var isLoading = false

    static let refreshInterval: Duration = .seconds(30)

    func refresh(client: APIClient, app: AppModel) async {
        isLoading = true
        defer { isLoading = false }
        do {
            let status = try await client.status()
            var query = SightingQuery(from: status.today, to: status.today, limit: 12)
            query.hideUnsure = false
            let latest = status.sightings.hasHistory ? try await client.sightings(query).items : []
            self.status = status
            self.latest = latest
            error = nil
            lastRefresh = .now
        } catch {
            self.error = error as? APIError ?? .unreachable(error.localizedDescription)
            app.report(error)
        }
    }

    /// Refreshes now, then every 30 s until cancelled (the view's task ends when it disappears or the app
    /// leaves the foreground).
    func keepFresh(client: APIClient, app: AppModel) async {
        while !Task.isCancelled {
            await refresh(client: client, app: app)
            try? await Task.sleep(for: Self.refreshInterval)
        }
    }
}
