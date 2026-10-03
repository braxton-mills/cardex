import CampiKit
import Foundation
import Observation

@MainActor @Observable
final class TodayModel {
    private(set) var status: Status?
    private(set) var latest: [Sighting] = []
    private(set) var highlights: [Highlight] = []
    private(set) var newestClip: Clip?
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
            async let latest = status.sightings.hasHistory ? client.sightings(query).items : []
            async let highlights = client.highlights(HighlightQuery(from: status.today, to: status.today, limit: 10)).items
            let (l, h) = try await (latest, highlights)
            // the newest clip can expire or be replaced between status and this call: not an error
            if let id = status.latestClipId, id != newestClip?.id {
                newestClip = try? await client.clip(id: id)
            } else if status.latestClipId == nil {
                newestClip = nil
            }
            self.status = status
            self.latest = l
            self.highlights = h
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
