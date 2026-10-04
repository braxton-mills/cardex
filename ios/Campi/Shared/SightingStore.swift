import CampiKit
import Foundation
import Observation

/// The latest version of sightings the user changed (star, hide, label) or reloaded, so every list and detail
/// screen shows the same state without refetching.
@MainActor @Observable
final class SightingStore {
    private(set) var updated: [String: Sighting] = [:]

    func resolve(_ s: Sighting) -> Sighting { updated[s.id] ?? s }

    func record(_ s: Sighting) { updated[s.id] = s }

    func reset() { updated = [:] }
}

/// Star / hide / label actions with optimistic updates, shared by the detail screen and context menus.
@MainActor
struct SightingActions {
    let app: AppModel

    func setStarred(_ starred: Bool, _ s: Sighting) async throws {
        try await apply(s, optimistic: { $0.starred = starred }) { client in
            try await client.setStarred(starred, sightingID: s.id)
        }
    }

    func setHidden(_ hidden: Bool, _ s: Sighting) async throws {
        try await apply(s, optimistic: { $0.hidden = hidden }) { client in
            try await client.setHidden(hidden, sightingID: s.id)
        }
    }

    /// `nil` removes the correction.
    func setLabel(_ label: String?, _ s: Sighting) async throws {
        try await apply(s, optimistic: { _ in }) { client in
            try await client.setLabel(label, sightingID: s.id)
        }
        app.collectionStale = true
    }

    private func apply(_ s: Sighting, optimistic: (inout Sighting) -> Void,
                       call: (APIClient) async throws -> Sighting) async throws {
        guard let client = app.client else { return }
        let before = app.sightings.resolve(s)
        var guess = before
        optimistic(&guess)
        app.sightings.record(guess)
        do {
            app.sightings.record(try await call(client))
        } catch {
            app.sightings.record(before)
            app.report(error)
            throw error
        }
    }
}
