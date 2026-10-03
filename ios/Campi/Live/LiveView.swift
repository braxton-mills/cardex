import CampiKit
import SwiftUI

/// Full-screen Live view: MJPEG video on Wi-Fi, a snapshot every 2 s on cellular or Low Data Mode.
struct LiveView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.displayScale) private var displayScale
    @State private var model = LiveModel()
    @State private var network = NetworkPath()
    @State private var retries = 0

    /// What to show: the user's pick, else by network.
    private var mode: LiveMode {
        LiveMode.choose(isExpensive: network.isExpensive, isConstrained: network.isConstrained,
                        override: model.override)
    }

    private struct RunKey: Hashable {
        var mode: LiveMode
        var active: Bool
        var retries: Int
    }

    var body: some View {
        GeometryReader { geo in
            ZStack {
                Color.black.ignoresSafeArea()
                if let image = model.image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .opacity(model.phase == .showing ? 1 : 0.2)
                        .accessibilityLabel("Live camera view")
                        .accessibilityIdentifier("live.image")
                }
                stateOverlay
            }
            .overlay(alignment: .top) { topBar }
            .overlay(alignment: .bottom) { if model.phase == .showing { statusPill.padding(.bottom, 12) } }
            .task(id: RunKey(mode: mode, active: scenePhase == .active, retries: retries)) {
                // Only while visible and in the foreground: leaving either cancels this task, which closes the stream.
                guard scenePhase == .active, let client = app.client else { return }
                let longSide = max(geo.size.width, geo.size.height) * displayScale
                await model.run(mode: mode, client: client, app: app, maxPixelSize: Int(min(longSide, 1920)))
            }
        }
        .preferredColorScheme(.dark)
        .statusBarHidden()
    }

    // MARK: pieces

    private var topBar: some View {
        HStack {
            Button { dismiss() } label: {
                Image(systemName: "xmark").font(.headline).padding(10)
            }
            .buttonStyle(.glass)
            .accessibilityLabel("Close")
            .accessibilityIdentifier("live.close")
            Spacer()
            Picker("Mode", selection: Binding(get: { model.override }, set: { model.override = $0 })) {
                Text("Auto").tag(LiveMode?.none)
                Text("Video").tag(LiveMode?.some(.video))
                Text("Snapshots").tag(LiveMode?.some(.snapshots))
            }
            .pickerStyle(.segmented)
            .frame(maxWidth: 280)
            .accessibilityIdentifier("live.mode")
        }
        .padding(.horizontal)
        .padding(.top, 8)
    }

    private var statusPill: some View {
        HStack(spacing: 8) {
            switch model.imageMode {
            case .video?:
                Circle().fill(.red).frame(width: 8, height: 8)
                Text("LIVE")
                    .fontWeight(.semibold)
                Text("\(model.fps) fps").monospacedDigit().foregroundStyle(.secondary)
            case .snapshots?:
                Image(systemName: "camera.shutter.button")
                if let t = model.frameTime {
                    TimelineView(.periodic(from: .now, by: 1)) { ctx in
                        let age = ctx.date.timeIntervalSince(t.date)
                        Text("Frame from \(t.secondsText) · every 2 s")
                            .foregroundStyle(age > 30 ? .orange : .primary)
                    }
                } else {
                    Text("Snapshot every 2 s")
                }
            case nil:
                EmptyView()
            }
            if model.override == nil {
                Text(mode == .video ? "· Wi-Fi" : "· saving data").foregroundStyle(.secondary)
            }
        }
        .font(.footnote)
        .foregroundStyle(.white)
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(.black.opacity(0.7), in: .capsule)   // readable over any frame
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("live.status")
    }

    @ViewBuilder
    private var stateOverlay: some View {
        switch model.phase {
        case .connecting:
            ProgressView().controlSize(.large).tint(.white)
        case .showing:
            EmptyView()
        case .busy(let max):
            problem("Live is busy", symbol: "person.3.fill",
                    text: "\(max) viewers are already watching, the most the PC allows. Snapshots still work.",
                    offerSnapshots: true)
        case .piUnreachable:
            problem("The camera isn't answering", symbol: "video.slash",
                    text: "The Pi didn't respond to the PC. Snapshots show the newest frame the PC saved.",
                    offerSnapshots: mode == .video)
        case .noRecentFrame:
            problem("No recent frame", symbol: "moon.zzz",
                    text: "The PC hasn't saved a frame in the last 10 minutes. The camera may be off or disconnected.",
                    offerSnapshots: false)
        case .offline(let why):
            problem("Live unavailable", symbol: "wifi.exclamationmark", text: why, offerSnapshots: false)
        }
    }

    private func problem(_ title: String, symbol: String, text: String, offerSnapshots: Bool) -> some View {
        ContentUnavailableView {
            Label(title, systemImage: symbol)
        } description: {
            Text(text)
        } actions: {
            if offerSnapshots {
                Button("Show Snapshots Instead") { model.override = .snapshots }
                    .buttonStyle(.borderedProminent)
            }
            Button("Try Again") { retries += 1 }
                .buttonStyle(.bordered)
        }
        .accessibilityIdentifier("live.problem")
    }
}

/// The Today tile that opens Live.
struct LiveTile: View {
    let live: LiveStatus
    let open: () -> Void

    var body: some View {
        Button(action: open) {
            AdaptiveStack(spacing: 12) {
                Image(systemName: "dot.radiowaves.left.and.right")
                    .font(.title2)
                    .foregroundStyle(live.available ? .red : .secondary)
                    .frame(width: 36)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Live view").font(.headline)
                    Text(live.available ? "Watch the camera now" : "Camera not reachable · last saved frame")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: "chevron.right").foregroundStyle(.tertiary)
            }
            .padding()
            .background(.background.secondary, in: .rect(cornerRadius: 16))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("today.live")
    }
}

extension PCDate {
    /// "2:05:12 PM" on the PC's clock.
    var secondsText: String {
        date.formatted(Date.FormatStyle(date: .omitted, time: .standard, timeZone: timeZone))
    }
}
