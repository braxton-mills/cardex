import CampiKit
import SwiftUI

@MainActor @Observable
final class HighlightsModel {
    var kind: Highlight.Kind?
    private(set) var items: [Highlight] = []
    private(set) var nextCursor: String?
    private(set) var isLoading = false
    private(set) var error: APIError?
    private(set) var loadedOnce = false
    private var generation = 0

    private var query: HighlightQuery { HighlightQuery(types: kind.map { [$0] } ?? [], limit: 40) }

    func reload(app: AppModel) async {
        guard let client = app.client else { return }
        generation += 1
        let gen = generation
        isLoading = true
        defer { if gen == generation { isLoading = false } }
        do {
            let page = try await client.highlights(query)
            guard gen == generation else { return }
            items = page.items
            nextCursor = page.nextCursor
            error = nil
            loadedOnce = true
        } catch {
            guard gen == generation else { return }
            self.error = error as? APIError ?? .unreachable(error.localizedDescription)
            app.report(error)
        }
    }

    func loadMore(after h: Highlight, app: AppModel) async {
        guard let cursor = nextCursor, !isLoading, let client = app.client,
              let i = items.firstIndex(where: { $0.id == h.id }), i >= items.count - 6 else { return }
        let gen = generation
        isLoading = true
        defer { if gen == generation { isLoading = false } }
        var q = query
        q.cursor = cursor
        if let page = try? await client.highlights(q), gen == generation {
            let known = Set(items.map(\.id))
            items += page.items.filter { !known.contains($0.id) }
            nextCursor = page.nextCursor
        }
    }
}

struct HighlightsView: View {
    @Environment(AppModel.self) private var app
    @State private var model = HighlightsModel()
    @State private var video: VideoTarget?
    @State private var message: String?

    static let kinds: [Highlight.Kind] = [.newCatch, .rare, .busiest, .daily, .starred]

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack {
                            Chip(title: "All", selected: model.kind == nil) { select(nil) }
                            ForEach(Self.kinds, id: \.self) { k in
                                Chip(title: k.title, symbol: k.symbol, selected: model.kind == k) { select(k) }
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    .listRowInsets(EdgeInsets(top: 0, leading: 16, bottom: 0, trailing: 16))
                    .listRowBackground(Color.clear)
                }
                let tz = model.items.first?.at.timeZone ?? .current
                ForEach(groupedByDay(model.items, day: \.day)) { group in
                    Section(group.day.text(timeZone: tz)) {
                        ForEach(group.items) { h in
                            HighlightRow(highlight: h, play: play)
                                .task { await model.loadMore(after: h, app: app) }
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .overlay {
                if model.items.isEmpty {
                    if let error = model.error {
                        UnreachableView(error: error, serverName: app.connection?.serverName) {
                            Task { await model.reload(app: app) }
                        }
                    } else if model.isLoading || !model.loadedOnce {
                        ProgressView()
                    } else {
                        ContentUnavailableView("No highlights", systemImage: "sparkles",
                                               description: Text(model.kind == nil ? "Nothing yet." : "Nothing of this kind yet."))
                    }
                }
            }
            .refreshable { await model.reload(app: app) }
            .navigationTitle("Highlights")
            .sightingDestinations()
            .videoPlayer($video)
            .alert("Campi", isPresented: Binding(get: { message != nil }, set: { if !$0 { message = nil } })) {
                Button("OK") {}
            } message: { Text(message ?? "") }
        }
        .task { if !model.loadedOnce { await model.reload(app: app) } }
    }

    private func select(_ kind: Highlight.Kind?) {
        model.kind = kind
        Task { await model.reload(app: app) }
    }

    private func play(_ h: Highlight) {
        Task {
            guard let client = app.client else { return }
            do {
                if let target = try await HighlightPlayback.target(for: h, client: client) {
                    video = target
                }
            } catch let e as HighlightPlayback.NoVideo {
                message = e.text
            } catch {
                app.report(error)
                message = (error as? APIError)?.localizedDescription ?? error.localizedDescription
            }
        }
    }
}

/// What tapping a non-sighting highlight plays.
@MainActor
enum HighlightPlayback {
    struct NoVideo: Error { let text: String }

    static func target(for h: Highlight, client: APIClient) async throws -> VideoTarget? {
        switch h.content {
        case .clip(let clip), .window(_, clip: .some(let clip)):
            guard let url = client.url(for: clip.media.video) else { return nil }
            return VideoTarget(url: url, title: "\(clip.windowStart.timeText)–\(clip.windowEnd.timeText)")
        case .daily(let d):
            guard let url = client.url(for: d.media.video) else { return nil }
            return VideoTarget(url: url, title: "Daily video, \(d.day.text(timeZone: h.at.timeZone))")
        case .window(let w, clip: nil):
            // the clip has expired: fall back to the daily video around the window
            let mid = w.start.date.addingTimeInterval(w.end.date.timeIntervalSince(w.start.date) / 2)
            switch try await Timelapse.target(at: mid, client: client, timeZone: w.start.timeZone) {
            case .play(let t): return t
            case .message(let text): throw NoVideo(text: text)
            }
        case .sighting, .unknown:
            return nil
        }
    }
}

struct HighlightRow: View {
    @Environment(AppModel.self) private var app
    let highlight: Highlight
    let play: (Highlight) -> Void

    var body: some View {
        switch highlight.content {
        case .sighting(let raw):
            let s = app.sightings.resolve(raw)
            NavigationLink(value: s) {
                row(image: s.media.crop, title: s.displayLabel,
                    subtitle: "\(s.startedAt.timeText) · \(percentText(s.confidence)) \(s.decidedBy.badge)")
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("highlight.row")
        case .clip(let c):
            Button { play(highlight) } label: {
                row(image: c.media.poster, title: "10-minute clip", subtitle: "\(c.windowStart.timeText)–\(c.windowEnd.timeText)",
                    playable: true)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("highlight.row")
        case .window(let w, let clip):
            Button { play(highlight) } label: {
                row(image: clip?.media.poster, title: "\(w.sightingsCount) sightings in 10 minutes",
                    subtitle: "\(w.start.timeText)–\(w.end.timeText)\(clip == nil ? " · clip expired" : "")",
                    playable: true)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("highlight.row")
        case .daily(let d):
            Button { play(highlight) } label: {
                row(image: d.media.poster, title: "Daily video",
                    subtitle: d.sightingsCount.map { "\($0) sightings that day" } ?? d.day.rawValue, playable: true)
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("highlight.row")
        case .unknown:
            EmptyView()
        }
    }

    private func row(image: MediaPath?, title: String, subtitle: String, playable: Bool = false) -> some View {
        AdaptiveStack(spacing: 12) {
            RemoteImage(path: image, maxPixelSize: 300)
                .frame(width: 84, height: 56)
                .clipShape(.rect(cornerRadius: 8))
                .overlay {
                    if playable {
                        Image(systemName: "play.circle.fill").font(.title2).foregroundStyle(.white).shadow(radius: 3)
                    }
                }
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(.primary).lineLimit(2)
                Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                FlowLayout {
                    ForEach(highlight.types, id: \.self) { k in Pill(text: k.title, symbol: k.symbol, color: k.color) }
                }
            }
        }
        .padding(.vertical, 2)
        .contentShape(.rect)
    }
}
