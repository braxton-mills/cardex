import CampiKit
import SwiftUI

struct SightingsView: View {
    @Environment(AppModel.self) private var app
    @State private var model = SightingsModel()
    @State private var showFilters = false

    var body: some View {
        NavigationStack {
            SightingGrid(model: model, emptyText: model.query.activeFilterCount > 0
                         ? "No sightings match these filters." : "No sightings yet.")
                .navigationTitle("Sightings")
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button {
                            showFilters = true
                        } label: {
                            Image(systemName: model.query.activeFilterCount > 0
                                  ? "line.3.horizontal.decrease.circle.fill" : "line.3.horizontal.decrease.circle")
                        }
                        .accessibilityLabel("Filters")
                        .badge(model.query.activeFilterCount)
                    }
                }
                .sheet(isPresented: $showFilters) {
                    SightingFilterView(query: model.query) { q in
                        model.query = q
                        Task { await model.reload(app: app) }
                    }
                }
                .sightingDestinations()
        }
        .task { if !model.loadedOnce { await model.reload(app: app) } }
    }
}

/// Day-sectioned grid of sighting cards with paging, pull-to-refresh and empty/error states.
struct SightingGrid: View {
    @Environment(AppModel.self) private var app
    let model: SightingsModel
    var emptyText = "No sightings."

    private let columns = [GridItem(.adaptive(minimum: 160), spacing: 12)]

    var body: some View {
        let visible = model.visible(app.sightings)
        let tz = visible.first?.startedAt.timeZone ?? .current
        ScrollView {
            if let total = model.total, !visible.isEmpty {
                Text("\(total.formatted()) sightings")
                    .font(.subheadline).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal)
            }
            LazyVGrid(columns: columns, spacing: 16, pinnedViews: [.sectionHeaders]) {
                ForEach(groupedByDay(visible, day: \.day)) { group in
                    Section {
                        ForEach(group.items) { s in
                            NavigationLink(value: s) { SightingGridCard(sighting: s) }
                                .buttonStyle(.plain)
                                .accessibilityIdentifier("sightings.card")
                                .task { await model.loadMore(after: s, app: app) }
                        }
                    } header: {
                        DayHeader(day: group.day, timeZone: tz)
                    }
                }
            }
            .padding(.horizontal)
            if model.isLoading && !visible.isEmpty {
                ProgressView().padding()
            }
        }
        .refreshable { await model.reload(app: app) }
        .overlay {
            if visible.isEmpty {
                if let error = model.error {
                    UnreachableView(error: error, serverName: app.connection?.serverName) {
                        Task { await model.reload(app: app) }
                    }
                } else if model.isLoading || !model.loadedOnce {
                    ProgressView()
                } else {
                    ContentUnavailableView(emptyText, systemImage: "car.side")
                }
            }
        }
    }
}

struct SightingGridCard: View {
    let sighting: Sighting

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            RemoteImage(path: sighting.media.crop, maxPixelSize: 500)
                .aspectRatio(3 / 2, contentMode: .fit)
                .clipShape(.rect(cornerRadius: 12))
                .overlay(alignment: .topTrailing) {
                    HStack(spacing: 4) {
                        if sighting.hidden { Image(systemName: "eye.slash.fill") }
                        if sighting.starred { Image(systemName: "star.fill").foregroundStyle(.yellow) }
                    }
                    .padding(6)
                    .shadow(radius: 2)
                }
                .overlay(alignment: .bottomLeading) {
                    if sighting.isAwaitingCloud {
                        Pill(text: "Asking Gemini", symbol: "sparkle", color: .white)
                            .background(.black.opacity(0.4), in: .capsule)
                            .padding(6)
                    }
                }
            Text(sighting.displayLabel)
                .font(.subheadline.weight(.semibold))
                .lineLimit(1)
                .foregroundStyle(sighting.unsure && sighting.decidedBy == .siglip ? .secondary : .primary)
            HStack(spacing: 6) {
                Text(sighting.startedAt.timeText)
                if let d = sighting.direction { Image(systemName: d.symbol) }
                Spacer(minLength: 0)
                Text(percentText(sighting.confidence)).monospacedDigit()
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            HStack(spacing: 4) {
                DecidedByBadge(sighting: sighting)
                if let year = sighting.yearRange { Pill(text: year) }
                if let color = sighting.color { Pill(text: color) }
                if sighting.stationary { Pill(text: "parked", symbol: "parkingsign") }
            }
            .lineLimit(1)
        }
        .contentShape(.rect)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(sighting.displayLabel), \(sighting.startedAt.timeText), \(percentText(sighting.confidence)) by \(sighting.decidedBy.badge)\(sighting.starred ? ", starred" : "")")
    }
}

extension View {
    /// Navigation destinations shared by every tab that links to sightings or collection items.
    func sightingDestinations() -> some View {
        navigationDestination(for: Sighting.self) { SightingDetailView(initial: $0) }
            .navigationDestination(for: CollectionItem.self) { CollectionItemView(item: $0) }
    }
}
