import CampiKit
import Foundation
import Observation

/// A paged, filterable sightings list (`GET /api/sightings`).
@MainActor @Observable
final class SightingsModel {
    var query: SightingQuery
    private(set) var items: [Sighting] = []
    private(set) var nextCursor: String?
    private(set) var total: Int?
    private(set) var isLoading = false
    private(set) var error: APIError?
    private(set) var loadedOnce = false
    private var generation = 0

    static let pageSize = 60

    init(query: SightingQuery = SightingQuery()) {
        var q = query
        q.limit = Self.pageSize
        q.cursor = nil
        self.query = q
    }

    /// What to show: latest local versions, minus rows an action took out of these filters.
    func visible(_ store: SightingStore) -> [Sighting] {
        items.map(store.resolve).filter(query.admits)
    }

    func reload(app: AppModel) async {
        guard let client = app.client else { return }
        generation += 1
        let gen = generation
        isLoading = true
        defer { if gen == generation { isLoading = false } }
        do {
            let page = try await client.sightings(query.filtersOnly)
            guard gen == generation else { return }   // filters changed meanwhile
            items = page.items
            nextCursor = page.nextCursor
            total = page.total
            error = nil
            loadedOnce = true
        } catch {
            guard gen == generation else { return }
            self.error = error as? APIError ?? .unreachable(error.localizedDescription)
            app.report(error)
        }
    }

    /// Loads the next page when `item` is one of the last few shown.
    func loadMore(after item: Sighting, app: AppModel) async {
        guard let cursor = nextCursor, !isLoading, let client = app.client,
              let i = items.firstIndex(where: { $0.id == item.id }), i >= items.count - 8 else { return }
        let gen = generation
        isLoading = true
        defer { if gen == generation { isLoading = false } }
        var q = query
        q.cursor = cursor
        do {
            let page = try await client.sightings(q)
            guard gen == generation else { return }
            let known = Set(items.map(\.id))
            items += page.items.filter { !known.contains($0.id) }
            nextCursor = page.nextCursor
            total = page.total
        } catch {
            app.report(error)
        }
    }
}
