import CampiKit
import SwiftUI

struct TodayView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.scenePhase) private var scenePhase
    @State private var model = TodayModel()
    @State private var showStatus = false
    @State private var showSettings = false
    @State private var showLive = false
    @State private var video: VideoTarget?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if let error = model.error, model.status == nil {
                        UnreachableView(error: error, serverName: app.connection?.serverName) {
                            Task { await refresh() }
                        }
                    } else if let status = model.status {
                        Button { showStatus = true } label: {
                            StatusCard(status: status, stale: model.error != nil)
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("today.status")
                        CountsRow(status: status)
                        LiveTile(live: status.live) { showLive = true }
                        if let clip = model.newestClip {
                            NewestClipCard(clip: clip) {
                                if let url = app.client?.url(for: clip.media.video) {
                                    video = VideoTarget(url: url, title: "\(clip.windowStart.timeText)–\(clip.windowEnd.timeText)")
                                }
                            }
                        }
                        LatestSightings(status: status, sightings: model.latest.map(app.sightings.resolve))
                        if !model.highlights.isEmpty {
                            TodayHighlights(highlights: model.highlights, play: play)
                        }
                    } else {
                        ProgressView().frame(maxWidth: .infinity, minHeight: 200)
                    }
                }
                .padding()
            }
            .refreshable { await refresh() }
            .navigationTitle(app.connection?.serverName ?? "Today")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Settings", systemImage: "gearshape") { showSettings = true }
                }
            }
            .sheet(isPresented: $showStatus) {
                if let status = model.status { StatusDetailView(status: status) }
            }
            .sheet(isPresented: $showSettings) { SettingsView(status: model.status) }
            .fullScreenCover(isPresented: $showLive) { LiveView() }
            .sightingDestinations()
            .videoPlayer($video)
        }
        // Poll only while visible and in the foreground.
        .task(id: scenePhase) {
            guard scenePhase == .active, let client = app.client else { return }
            await model.keepFresh(client: client, app: app)
        }
    }

    private func play(_ h: Highlight) {
        Task {
            guard let client = app.client else { return }
            if let target = try? await HighlightPlayback.target(for: h, client: client) { video = target }
        }
    }

    private func refresh() async {
        guard let client = app.client else { return }
        await model.refresh(client: client, app: app)
    }
}

struct StatusCard: View {
    let status: Status
    var stale = false

    var body: some View {
        let health = status.health()
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Circle().fill(health.level.color).frame(width: 12, height: 12)
                    .accessibilityHidden(true)
                Text(health.headline).font(.headline)
                Spacer()
                Image(systemName: "chevron.right").foregroundStyle(.tertiary)
            }
            Group {
                if let last = status.capture.lastFrameAt {
                    Text("Last frame \(last.agoText())\(status.clips.nextAt.map { " · next clip \($0.timeText)" } ?? "")")
                } else {
                    Text("No frames yet")
                }
                if stale {
                    Text("Showing the last known status: the PC didn't answer the latest refresh.")
                        .foregroundStyle(.orange)
                }
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)
        }
        .padding()
        .background(.background.secondary, in: .rect(cornerRadius: 16))
        .accessibilityElement(children: .combine)
        .accessibilityHint("Shows service details")
    }
}

struct CountsRow: View {
    let status: Status

    var body: some View {
        HStack(spacing: 12) {
            tile(value: "\(status.sightings.today)", label: "sightings today", symbol: "car.side")
            tile(value: status.sightings.lastSightingAt?.timeText ?? "–", label: "last sighting", symbol: "clock")
            tile(value: "\(status.sightings.total)", label: "all time", symbol: "sum")
        }
    }

    private func tile(value: String, label: String, symbol: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Image(systemName: symbol).foregroundStyle(.tint)
            Text(value).font(.title2.bold()).monospacedDigit().lineLimit(1).minimumScaleFactor(0.6)
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.background.secondary, in: .rect(cornerRadius: 14))
        .accessibilityElement(children: .combine)
    }
}

struct LatestSightings: View {
    let status: Status
    let sightings: [Sighting]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Latest sightings").font(.title3.bold())
            if !status.sightings.enabled && !status.sightings.hasHistory {
                ContentUnavailableView("Sightings is off", systemImage: "car.side",
                                       description: Text("Enable [sightings] on the PC to log passing vehicles."))
            } else if sightings.isEmpty {
                Text(status.sightings.enabled ? "Nothing yet today." : "Sightings is off on the PC.")
                    .foregroundStyle(.secondary)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    LazyHStack(spacing: 12) {
                        ForEach(sightings) { s in
                            NavigationLink(value: s) { SightingCard(sighting: s) }
                                .buttonStyle(.plain)
                                .accessibilityIdentifier("today.sighting")
                        }
                    }
                }
                .scrollClipDisabled()
            }
        }
    }
}

struct SightingCard: View {
    let sighting: Sighting

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            RemoteImage(path: sighting.media.crop, maxPixelSize: 400)
                .frame(width: 168, height: 112)
                .clipShape(.rect(cornerRadius: 12))
                .overlay(alignment: .topTrailing) {
                    if sighting.starred {
                        Image(systemName: "star.fill").foregroundStyle(.yellow).padding(6)
                            .shadow(radius: 2)
                    }
                }
            Text(sighting.displayLabel).font(.subheadline.weight(.semibold)).lineLimit(1)
            HStack(spacing: 6) {
                Text(sighting.startedAt.timeText)
                if let d = sighting.direction { Image(systemName: d.symbol) }
                Spacer(minLength: 0)
                Label(percentText(sighting.confidence), systemImage: sighting.decidedBy.symbol)
                    .labelStyle(.titleAndIcon)
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .frame(width: 168)
        .accessibilityElement(children: .combine)
    }
}

struct UnreachableView: View {
    let error: APIError
    let serverName: String?
    let retry: () -> Void

    var body: some View {
        ContentUnavailableView {
            Label(error.isUnreachable ? "Can't reach \(serverName ?? "the PC")" : "Something went wrong",
                  systemImage: error.isUnreachable ? "wifi.exclamationmark" : "exclamationmark.triangle")
        } description: {
            Text(error.isUnreachable
                 ? "Check that Tailscale is connected on this iPhone and the PC is on. If Campi was stopped on the PC, it can't answer either."
                 : (error.localizedDescription))
        } actions: {
            Button("Try Again", action: retry).buttonStyle(.borderedProminent)
        }
        .frame(minHeight: 400)
    }
}

struct TodayHighlights: View {
    let highlights: [Highlight]
    let play: (Highlight) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Today's highlights").font(.title3.bold())
            VStack(spacing: 0) {
                ForEach(highlights) { h in
                    HighlightRow(highlight: h, play: play)
                        .buttonStyle(.plain)
                        .padding(.vertical, 6)
                    if h.id != highlights.last?.id { Divider() }
                }
            }
        }
    }
}

struct NewestClipCard: View {
    let clip: Clip
    let play: () -> Void

    var body: some View {
        Button(action: play) {
            HStack(spacing: 14) {
                RemoteImage(path: clip.media.poster, maxPixelSize: 480)
                    .frame(width: 128, height: 96)
                    .clipShape(.rect(cornerRadius: 12))
                    .overlay {
                        Image(systemName: "play.circle.fill").font(.largeTitle).foregroundStyle(.white).shadow(radius: 4)
                    }
                VStack(alignment: .leading, spacing: 4) {
                    Text("Newest clip").font(.caption).foregroundStyle(.secondary)
                    Text("\(clip.windowStart.timeText)–\(clip.windowEnd.timeText)").font(.headline)
                    if let n = clip.sightingsCount {
                        Text("\(n) sighting\(n == 1 ? "" : "s")").font(.subheadline).foregroundStyle(.secondary)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(12)
            .background(.background.secondary, in: .rect(cornerRadius: 16))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("today.newestClip")
    }
}
