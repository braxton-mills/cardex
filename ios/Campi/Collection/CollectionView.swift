import CampiKit
import SwiftUI

struct CollectionView: View {
    @Environment(AppModel.self) private var app
    @State private var filter: Filter = .all
    @State private var search = ""
    @State private var error: APIError?
    @AppStorage("collection.mode") private var mode: Mode = .cards
    @Namespace private var cardZoom

    enum Mode: String, CaseIterable, Identifiable {
        case cards = "Cards", grid = "Grid"
        var id: Self { self }
    }

    enum Filter: String, CaseIterable, Identifiable {
        case all = "All", caught = "Caught", missing = "Missing"
        var id: Self { self }
        var caught: Bool? { self == .all ? nil : self == .caught }
    }

    @ScaledMetric(relativeTo: .caption) private var tileWidth: CGFloat = 104
    private var columns: [GridItem] { [GridItem(.adaptive(minimum: min(tileWidth, 320)), spacing: 12)] }
    @ScaledMetric(relativeTo: .caption) private var cardWidth: CGFloat = 150
    private var cardColumns: [GridItem] { [GridItem(.adaptive(minimum: min(cardWidth, 300)), spacing: 14)] }

    var body: some View {
        NavigationStack {
            ScrollView {
                if let c = app.collection {
                    VStack(alignment: .leading, spacing: 18) {
                        progress(c)
                        AdaptiveStack(spacing: 10) {
                            Picker("Show", selection: $filter) {
                                ForEach(Filter.allCases) { Text($0.rawValue).tag($0) }
                            }
                            .pickerStyle(.segmented)
                            Picker("View", selection: $mode) {
                                ForEach(Mode.allCases) { Text($0.rawValue).tag($0) }
                            }
                            .pickerStyle(.segmented)
                            .fixedSize()
                            .accessibilityIdentifier("collection.mode")
                        }
                        let sections = c.sections(matching: search, caught: filter.caught)
                        ForEach(sections) { section in
                            VStack(alignment: .leading, spacing: 10) {
                                Text(section.title).font(.headline)
                                switch mode {
                                case .cards: binder(section.items, in: c)
                                case .grid: grid(section.items)
                                }
                            }
                        }
                        if sections.isEmpty {
                            ContentUnavailableView.search(text: search)
                        }
                    }
                    .padding()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)   // an empty ScrollView has no width for the overlay
            .overlay {
                if app.collection == nil {
                    if let error {
                        UnreachableView(error: error, serverName: app.connection?.serverName) {
                            Task { await load(force: true) }
                        }
                    } else {
                        ProgressView()
                    }
                }
            }
            .searchable(text: $search, prompt: "Make or model")
            .refreshable { await load(force: true) }
            .navigationTitle("Cardex")
            .sightingDestinations(cardZoom: cardZoom)
        }
        .task { await load(force: false) }
    }

    private func grid(_ items: [CollectionItem]) -> some View {
        LazyVGrid(columns: columns, spacing: 14) {
            ForEach(items) { item in
                if item.isCaught {
                    NavigationLink(value: item) { CollectionTile(item: item) }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("collection.tile")
                } else {
                    CollectionTile(item: item)
                }
            }
        }
    }

    /// Trading cards for caught cars, numbered slots for the rest.
    private func binder(_ items: [CollectionItem], in c: LabelCollection) -> some View {
        LazyVGrid(columns: cardColumns, spacing: 16) {
            ForEach(items) { item in
                let number = (c.items.firstIndex { $0.label == item.label } ?? -1) + 1
                if item.isCaught {
                    NavigationLink(value: CardexCardRoute(item: item)) {
                        TradingCardView(item: item, number: number, total: c.total)
                            .shadow(color: .black.opacity(0.25), radius: 4, y: 2)
                    }
                    .buttonStyle(.plain)
                    .matchedTransitionSource(id: item.label, in: cardZoom)
                    .accessibilityIdentifier("collection.card")
                } else {
                    EmptyCardSlot(item: item, number: number, total: c.total)
                }
            }
        }
    }

    private func progress(_ c: LabelCollection) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text("\(c.caught)").font(.largeTitle.bold()).monospacedDigit()
                Text("of \(c.total) caught").font(.title3).foregroundStyle(.secondary)
                Spacer()
                Text(c.progress.formatted(.percent.precision(.fractionLength(0))))
                    .font(.headline).foregroundStyle(.tint)
            }
            ProgressView(value: c.progress)
            FlowLayout(spacing: 6) {
                ForEach(c.tiers, id: \.tier) { t in
                    let n = c.items.filter { $0.tier == t.tier }.count
                    Pill(text: "\(n) \(t.tier.title.lowercased())", color: t.tier.color)
                }
            }
        }
        .padding()
        .background(.background.secondary, in: .rect(cornerRadius: 16))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("collection.progress")
    }

    private func load(force: Bool) async {
        do {
            _ = try await app.loadCollection(force: force)
            error = nil
        } catch {
            self.error = error as? APIError ?? .unreachable(error.localizedDescription)
            app.report(error)
        }
    }
}

struct CollectionTile: View {
    let item: CollectionItem

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            ZStack {
                if let cover = item.cover {
                    RemoteImage(path: cover.crop, maxPixelSize: 360)
                } else {
                    Rectangle().fill(.quaternary)
                    Image(systemName: item.generic ? "truck.box" : "car.side")
                        .font(.title)
                        .foregroundStyle(.tertiary)
                }
            }
            .aspectRatio(3 / 2, contentMode: .fit)
            .clipShape(.rect(cornerRadius: 10))
            .overlay(alignment: .topTrailing) {
                if item.origin == .discovered {
                    Image(systemName: "sparkle").font(.caption).padding(5)
                        .foregroundStyle(.white).shadow(radius: 2)
                }
            }
            Text(item.label)
                .font(.caption.weight(.semibold))
                .lineLimit(2, reservesSpace: true)
                .foregroundStyle(item.isCaught ? .primary : .secondary)
            HStack(spacing: 4) {
                if item.isCaught {
                    Text("×\(item.count)").font(.caption2).monospacedDigit().foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                TierBadge(tier: item.tier)
            }
        }
        .opacity(item.isCaught ? 1 : 0.7)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(item.isCaught ? "\(item.label), caught \(item.count) times, \(item.tier.title)"
                                          : "\(item.label), not caught yet")
    }
}

/// Every sighting of one label.
struct CollectionItemView: View {
    @Environment(AppModel.self) private var app
    let item: CollectionItem
    @State private var model: SightingsModel

    init(item: CollectionItem) {
        self.item = item
        var q = SightingQuery()
        q.label = item.label
        _model = State(initialValue: SightingsModel(query: q))
    }

    var body: some View {
        SightingGrid(model: model, emptyText: "No visible sightings of \(item.label).", header: AnyView(header))
            .navigationTitle(item.label)
            .navigationBarTitleDisplayMode(.inline)
            .task { if !model.loadedOnce { await model.reload(app: app) } }
    }

    private var header: some View {
        VStack(spacing: 12) {
            HStack(spacing: 14) {
                if let cover = item.cover {
                    RemoteImage(path: cover.crop, maxPixelSize: 300)
                        .frame(width: 96, height: 64)
                        .clipShape(.rect(cornerRadius: 10))
                }
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        TierBadge(tier: item.tier)
                        if item.origin == .discovered { Pill(text: "Discovered", symbol: "sparkle", color: .indigo) }
                    }
                    Text("Seen \(item.count) time\(item.count == 1 ? "" : "s")").font(.subheadline)
                    if let first = item.firstSeenAt {
                        Text("First \(first.dateTimeText)").font(.caption).foregroundStyle(.secondary)
                    }
                    if let last = item.lastSeenAt, item.count > 1 {
                        Text("Last \(last.dateTimeText)").font(.caption).foregroundStyle(.secondary)
                    }
                }
                Spacer()
            }
            CardexCardView(item: item, showsStats: false)
        }
        .padding()
    }
}

/// A binder slot for a car that hasn't been caught yet.
struct EmptyCardSlot: View {
    let item: CollectionItem
    let number: Int
    let total: Int

    var body: some View {
        RoundedRectangle(cornerRadius: 12, style: .continuous)
            .strokeBorder(.tertiary, style: StrokeStyle(lineWidth: 1.5, dash: [6, 4]))
            .background(.quaternary.opacity(0.4), in: .rect(cornerRadius: 12, style: .continuous))
            .aspectRatio(TradingCardView.size.width / TradingCardView.size.height, contentMode: .fit)
            .overlay {
                VStack(spacing: 6) {
                    Text("?").font(.system(.largeTitle, design: .rounded).weight(.heavy)).foregroundStyle(.tertiary)
                    Text(item.label).font(.caption.weight(.semibold)).multilineTextAlignment(.center)
                        .foregroundStyle(.secondary).lineLimit(3)
                    Text(String(format: "%03d/%03d", number, total)).font(.caption2).monospacedDigit()
                        .foregroundStyle(.tertiary)
                }
                .padding(10)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(item.label), not caught yet")
    }
}
