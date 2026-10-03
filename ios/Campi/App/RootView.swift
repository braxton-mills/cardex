import CampiKit
import SwiftUI

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Group {
            if model.isPaired {
                MainTabs()
            } else {
                PairingView()
            }
        }
        .sheet(item: $model.pendingLink) { link in
            PairConfirmView(link: link)
                .presentationDetents([.medium, .large])
        }
    }
}

struct MainTabs: View {
    enum Tab: Hashable { case today, highlights, sightings, collection, timelapse }
    @State private var selection: Tab = .today

    var body: some View {
        TabView(selection: $selection) {
            SwiftUI.Tab("Today", systemImage: "sun.max", value: .today) {
                TodayView()
            }
            SwiftUI.Tab("Highlights", systemImage: "sparkles", value: .highlights) {
                ComingSoonView(title: "Highlights", systemImage: "sparkles", milestone: "M2")
            }
            SwiftUI.Tab("Sightings", systemImage: "car.side", value: .sightings) {
                ComingSoonView(title: "Sightings", systemImage: "car.side", milestone: "M2")
            }
            SwiftUI.Tab("Collection", systemImage: "square.grid.3x3.fill", value: .collection) {
                ComingSoonView(title: "Collection", systemImage: "square.grid.3x3.fill", milestone: "M2")
            }
            SwiftUI.Tab("Timelapse", systemImage: "film.stack", value: .timelapse) {
                ComingSoonView(title: "Timelapse", systemImage: "film.stack", milestone: "M3")
            }
        }
    }
}

struct ComingSoonView: View {
    let title: String
    let systemImage: String
    let milestone: String

    var body: some View {
        NavigationStack {
            ContentUnavailableView(title, systemImage: systemImage,
                                   description: Text("Coming in \(milestone)."))
                .navigationTitle(title)
        }
    }
}

extension PairingLink: @retroactive Identifiable {
    public var id: String { baseURL.absoluteString + code }
}
