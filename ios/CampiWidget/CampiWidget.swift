import CampiKit
import SwiftUI
import WidgetKit

@main
struct CampiWidgetBundle: WidgetBundle {
    var body: some Widget { CampiStatusWidget() }
}

/// Latest catch and today's count; "PC unreachable" with the last good data when the PC can't be reached.
struct CampiStatusWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "CampiStatus", provider: CampiProvider()) { entry in
            CampiWidgetView(entry: entry)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Campi")
        .description("The latest catch and today's sightings.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

struct CampiEntry: TimelineEntry {
    var date: Date = .now
    var display: WidgetDisplay
    var crop: UIImage?
}

struct CampiProvider: TimelineProvider {
    static let refresh: TimeInterval = 30 * 60
    static let cropPixels = 400

    func placeholder(in context: Context) -> CampiEntry { Self.sample }

    func getSnapshot(in context: Context, completion: @escaping @Sendable (CampiEntry) -> Void) {
        if context.isPreview {
            completion(Self.sample)
            return
        }
        Task { completion(await Self.load()) }
    }

    func getTimeline(in context: Context, completion: @escaping @Sendable (Timeline<CampiEntry>) -> Void) {
        Task {
            let entry = await Self.load()
            completion(Timeline(entries: [entry], policy: .after(.now.addingTimeInterval(Self.refresh))))
        }
    }

    /// Fetches status and the newest sighting; on failure falls back to the last saved snapshot.
    static func load() async -> CampiEntry {
        let store = WidgetStore()
        guard let (conn, token) = SharedConfig.connectionStore.load() else { return CampiEntry(display: .unpaired) }
        let client = APIClient(baseURL: conn.baseURL, token: token)
        let saved = store.load()
        do {
            let status = try await client.status()
            var q = SightingQuery(limit: 1)
            q.hideUnsure = false
            let newest = status.sightings.hasHistory ? try await client.sightings(q).items.first : nil
            var latest: WidgetSnapshot.Catch?
            if let s = newest {
                var file = saved?.latest?.sightingID == s.id ? saved?.latest?.cropFile : nil
                if file.map({ !FileManager.default.fileExists(atPath: store.cropURL($0).path()) }) ?? true,
                   let crop = s.media.crop, let data = try? await client.mediaData(crop),
                   let jpeg = ImageLoader.downsampledJPEG(data, maxPixelSize: cropPixels) {
                    file = try? store.saveCrop(jpeg, sightingID: s.id)
                }
                latest = .init(sightingID: s.id, label: s.displayLabel, at: s.startedAt, cropFile: file)
            }
            let snap = WidgetSnapshot(status: status, latest: latest)
            try? store.save(snap)
            return entry(.fresh(snap), store: store)
        } catch let e as APIError where e.needsPairing {
            return CampiEntry(display: .unpaired)
        } catch {
            return entry(WidgetDisplay.choose(paired: true, fetched: nil, saved: saved), store: store)
        }
    }

    static func entry(_ display: WidgetDisplay, store: WidgetStore) -> CampiEntry {
        var crop: UIImage?
        if case .fresh(let s) = display, let f = s.latest?.cropFile { crop = UIImage(contentsOfFile: store.cropURL(f).path()) }
        if case .unreachable(let s) = display, let f = s.latest?.cropFile { crop = UIImage(contentsOfFile: store.cropURL(f).path()) }
        return CampiEntry(display: display, crop: crop)
    }

    static var sample: CampiEntry {
        let now = PCDate(date: .now, utcOffsetSeconds: TimeZone.current.secondsFromGMT())
        let snap = WidgetSnapshot(serverName: "CAMPI-PC", fetchedAt: .now, day: now.day,
                                  utcOffsetSeconds: now.utcOffsetSeconds, todayCount: 42, healthLevel: 0,
                                  healthText: "All systems normal",
                                  latest: .init(sightingID: "sample", label: "Toyota GR86", at: now, cropFile: nil))
        return CampiEntry(display: .fresh(snap))
    }
}

struct CampiWidgetView: View {
    @Environment(\.widgetFamily) private var family
    let entry: CampiEntry

    var body: some View {
        Group {
            switch entry.display {
            case .unpaired:
                message("Open Campi to pair with your PC", symbol: "qrcode.viewfinder")
            case .unreachableNoData:
                message("Can't reach the PC. Is Tailscale on?", symbol: "wifi.exclamationmark")
            case .fresh(let s):
                content(s, unreachable: false)
            case .unreachable(let s):
                content(s, unreachable: true)
            }
        }
        .widgetURL(url)
    }

    private var url: URL {
        switch entry.display {
        case .fresh(let s), .unreachable(let s):
            s.latest.map { DeepLink.sighting(id: $0.sightingID).url } ?? DeepLink.today.url
        default: DeepLink.today.url
        }
    }

    @ViewBuilder
    private func content(_ s: WidgetSnapshot, unreachable: Bool) -> some View {
        if family == .systemMedium {
            HStack(spacing: 12) {
                crop.frame(width: 130).clipShape(.rect(cornerRadius: 12))
                summary(s, unreachable: unreachable)
            }
        } else {
            summary(s, unreachable: unreachable)
        }
    }

    private var crop: some View {
        Rectangle().fill(.quaternary)
            .overlay {
                if let img = entry.crop {
                    Image(uiImage: img).resizable().scaledToFill()
                } else {
                    Image(systemName: "car.side").font(.title).foregroundStyle(.secondary)
                }
            }
            .clipped()
            .accessibilityHidden(true)
    }

    private func summary(_ s: WidgetSnapshot, unreachable: Bool) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 5) {
                Circle().fill(unreachable ? .gray : color(s.health)).frame(width: 7, height: 7)
                Text(s.serverName).font(.caption2.weight(.semibold)).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 2)
            Text(s.todayCount(now: entry.date).map(String.init) ?? "–")
                .font(.system(size: 34, weight: .bold, design: .rounded))
                .contentTransition(.numericText())
                .minimumScaleFactor(0.6)
            Text("sightings today").font(.caption2).foregroundStyle(.secondary)
            Spacer(minLength: 2)
            if let c = s.latest {
                Text(c.label).font(.caption.weight(.semibold)).lineLimit(1)
            }
            if unreachable {
                Text("PC unreachable · as of \(time(PCDate(date: s.fetchedAt, utcOffsetSeconds: s.utcOffsetSeconds)))")
                    .font(.caption2).foregroundStyle(.orange).lineLimit(2)
            } else if let c = s.latest {
                Text(time(c.at)).font(.caption2).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    private func message(_ text: String, symbol: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Image(systemName: symbol).font(.title2).foregroundStyle(.tint)
            Text(text).font(.subheadline.weight(.medium))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    private func time(_ d: PCDate) -> String {
        d.date.formatted(Date.FormatStyle(date: .omitted, time: .shortened, timeZone: d.timeZone))
    }

    private func color(_ level: HealthReport.Level) -> Color {
        switch level {
        case .ok: .green
        case .warning: .orange
        case .error: .red
        }
    }
}
