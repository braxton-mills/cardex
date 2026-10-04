import CampiKit
import SwiftUI

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Group {
            #if DEBUG
            if let page = CardexGalleryView.page { CardexGalleryView(page: page) } else { content }
            #else
            content
            #endif
        }
        .overlay(alignment: .bottom) { SaveStatusBanner(saver: model.saver) }
        .sheet(item: Bindable(model.saver).shareItem) { item in
            ShareSheet(url: item.url).presentationDetents([.medium, .large])
        }
        .sheet(item: $model.pendingLink) { link in
            PairConfirmView(link: link)
                .presentationDetents([.medium, .large])
        }
    }

    @ViewBuilder private var content: some View {
        if model.isPaired {
            MainTabs()
        } else {
            PairingView()
        }
    }
}

struct MainTabs: View {
    enum Tab: Hashable { case today, highlights, sightings, collection, timelapse }
    @Environment(AppModel.self) private var app

    var body: some View {
        @Bindable var app = app
        TabView(selection: $app.tab) {
            SwiftUI.Tab("Today", systemImage: "sun.max", value: .today) {
                TodayView()
            }
            SwiftUI.Tab("Highlights", systemImage: "sparkles", value: .highlights) {
                HighlightsView()
            }
            SwiftUI.Tab("Sightings", systemImage: "car.side", value: .sightings) {
                SightingsView()
            }
            SwiftUI.Tab("Cardex", systemImage: "rectangle.portrait.on.rectangle.portrait.angled.fill", value: .collection) {
                CollectionView()
            }
            SwiftUI.Tab("Timelapse", systemImage: "film.stack", value: .timelapse) {
                TimelapseView()
            }
        }
        .sheet(item: $app.route) { route in
            switch route {
            case .sighting(let id): SightingLoaderView(id: id)
            case .status: StatusLoaderView()
            }
        }
    }
}

/// A sighting opened by id (notification or widget tap).
struct SightingLoaderView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let id: String
    @State private var sighting: Sighting?
    @State private var error: APIError?

    var body: some View {
        NavigationStack {
            Group {
                if let sighting {
                    SightingDetailView(initial: sighting)
                } else if let error {
                    UnreachableView(error: error, serverName: app.connection?.serverName) { Task { await load() } }
                } else {
                    ProgressView()
                }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
            }
            .sightingDestinations()
        }
        .task { await load() }
    }

    private func load() async {
        guard let client = app.client else { return }
        error = nil
        do {
            sighting = app.sightings.resolve(try await client.sighting(id: id))
        } catch {
            app.report(error)
            self.error = error as? APIError ?? .unreachable(error.localizedDescription)
        }
    }
}

/// The service status sheet, opened by a service-alert notification.
struct StatusLoaderView: View {
    @Environment(AppModel.self) private var app
    @State private var status: Status?
    @State private var error: APIError?

    var body: some View {
        if let status {
            StatusDetailView(status: status)
        } else {
            NavigationStack {
                Group {
                    if let error {
                        UnreachableView(error: error, serverName: app.connection?.serverName) { Task { await load() } }
                    } else {
                        ProgressView()
                    }
                }
                .navigationTitle("Status")
            }
            .task { await load() }
        }
    }

    private func load() async {
        guard let client = app.client else { return }
        error = nil
        do { status = try await client.status() } catch {
            app.report(error)
            self.error = error as? APIError ?? .unreachable(error.localizedDescription)
        }
    }
}

extension PairingLink: @retroactive Identifiable {
    public var id: String { baseURL.absoluteString + code }
}
