import CampiKit
import SwiftUI

@MainActor @Observable
final class TimelapseModel {
    private(set) var clips: ClipList?
    private(set) var dailies: [Daily] = []
    private(set) var dailyCursor: String?
    private(set) var dailyTotal: Int?
    private(set) var archive: ArchiveList?
    private(set) var error: APIError?
    private(set) var isLoading = false

    func loadClips(app: AppModel) async {
        await load(app) { self.clips = try await $0.clips() }
    }

    func loadDailies(app: AppModel) async {
        await load(app) {
            let page = try await $0.dailies(limit: 30)
            self.dailies = page.items
            self.dailyCursor = page.nextCursor
            self.dailyTotal = page.total
        }
    }

    func loadMoreDailies(after d: Daily, app: AppModel) async {
        guard let cursor = dailyCursor, !isLoading, d.id == dailies.last?.id, let client = app.client else { return }
        isLoading = true
        defer { isLoading = false }
        if let page = try? await client.dailies(limit: 30, cursor: cursor) {
            dailies += page.items.filter { new in !dailies.contains { $0.id == new.id } }
            dailyCursor = page.nextCursor
        }
    }

    func loadArchive(app: AppModel) async {
        await load(app) { self.archive = try await $0.archive() }
    }

    func setStarred(_ starred: Bool, clip: Clip, app: AppModel) async {
        guard let client = app.client else { return }
        replace(clip) { $0.starred = starred }
        do { replace(try await client.setStarred(starred, clipID: clip.id)) { _ in } } catch {
            replace(clip) { $0.starred = !starred }
            app.report(error)
        }
    }

    func setStarred(_ starred: Bool, daily: Daily, app: AppModel) async {
        guard let client = app.client, let i = dailies.firstIndex(where: { $0.id == daily.id }) else { return }
        dailies[i].starred = starred
        do { dailies[i] = try await client.setStarred(starred, day: daily.day) } catch {
            dailies[i].starred = !starred
            app.report(error)
        }
    }

    private func replace(_ clip: Clip, _ edit: (inout Clip) -> Void) {
        guard let i = clips?.items.firstIndex(where: { $0.id == clip.id }) else { return }
        var c = clip
        edit(&c)
        clips?.items[i] = c
    }

    private func load(_ app: AppModel, _ op: (APIClient) async throws -> Void) async {
        guard let client = app.client else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            try await op(client)
            error = nil
        } catch {
            self.error = error as? APIError ?? .unreachable(error.localizedDescription)
            app.report(error)
        }
    }
}

struct TimelapseView: View {
    enum Part: String, CaseIterable, Identifiable {
        case clips = "Clips", daily = "Daily", archive = "Archive"
        var id: Self { self }
    }

    @Environment(AppModel.self) private var app
    @State private var part: Part = .clips
    @State private var model = TimelapseModel()
    @State private var video: VideoTarget?

    var body: some View {
        NavigationStack {
            List {
                Picker("Show", selection: $part) {
                    ForEach(Part.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets(top: 4, leading: 16, bottom: 4, trailing: 16))
                switch part {
                case .clips: clipsSection
                case .daily: dailySection
                case .archive: archiveSection
                }
            }
            .listStyle(.insetGrouped)
            .refreshable { await load(part, force: true) }
            .overlay { overlay }
            .navigationTitle("Timelapse")
            .videoPlayer($video)
        }
        .task(id: part) { await load(part, force: false) }
    }

    // MARK: clips

    @ViewBuilder private var clipsSection: some View {
        if let list = model.clips {
            ForEach(list.byHour) { group in
                Section(hourTitle(group)) {
                    ForEach(group.clips) { clip in
                        Button { play(clip) } label: { ClipRow(clip: clip, isLatest: clip.id == list.latestId) }
                            .buttonStyle(.plain)
                            .accessibilityIdentifier("timelapse.clip")
                            .contextMenu { clipMenu(clip) }
                            .swipeActions(edge: .leading) {
                                Button(clip.starred ? "Unstar" : "Star", systemImage: clip.starred ? "star.slash" : "star") {
                                    Task { await model.setStarred(!clip.starred, clip: clip, app: app) }
                                }
                                .tint(.yellow)
                            }
                            .swipeActions(edge: .trailing) {
                                Button("Save", systemImage: "square.and.arrow.down") {
                                    app.saver.saveVideo(clip.media.video, fileName: clip.fileName, app: app)
                                }
                                .tint(.blue)
                            }
                    }
                }
            }
            Section {
            } footer: {
                Text("10-minute clips are kept for \(list.retentionHours) hours on the PC. Save the ones you want to keep.")
            }
        }
    }

    @ViewBuilder private func clipMenu(_ clip: Clip) -> some View {
        Button(clip.starred ? "Unstar" : "Star", systemImage: clip.starred ? "star.slash" : "star") {
            Task { await model.setStarred(!clip.starred, clip: clip, app: app) }
        }
        Button("Save to Photos", systemImage: "square.and.arrow.down") {
            app.saver.saveVideo(clip.media.video, fileName: clip.fileName, app: app)
        }
        Button("Share…", systemImage: "square.and.arrow.up") {
            app.saver.share(clip.media.video, fileName: clip.fileName, app: app)
        }
    }

    private func hourTitle(_ g: HourGroup) -> String {
        let tz = g.clips.first?.windowStart.timeZone ?? .current
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = tz
        let start = cal.date(bySettingHour: g.hour, minute: 0, second: 0, of: g.day.noon(in: tz)) ?? .now
        let hour = start.formatted(Date.FormatStyle(timeZone: tz).hour())
        return "\(g.day.text(timeZone: tz)), \(hour)"
    }

    // MARK: daily

    @ViewBuilder private var dailySection: some View {
        if !model.dailies.isEmpty {
            Section {
                ForEach(model.dailies) { d in
                    Button { play(d) } label: { DailyRow(daily: d) }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("timelapse.daily")
                        .task { await model.loadMoreDailies(after: d, app: app) }
                        .contextMenu {
                            Button(d.starred ? "Unstar" : "Star", systemImage: d.starred ? "star.slash" : "star") {
                                Task { await model.setStarred(!d.starred, daily: d, app: app) }
                            }
                            Button("Save to Photos", systemImage: "square.and.arrow.down") {
                                app.saver.saveVideo(d.media.video, fileName: d.fileName, app: app)
                            }
                            Button("Share…", systemImage: "square.and.arrow.up") {
                                app.saver.share(d.media.video, fileName: d.fileName, app: app)
                            }
                        }
                        .swipeActions(edge: .leading) {
                            Button(d.starred ? "Unstar" : "Star", systemImage: d.starred ? "star.slash" : "star") {
                                Task { await model.setStarred(!d.starred, daily: d, app: app) }
                            }
                            .tint(.yellow)
                        }
                }
            } footer: {
                if let total = model.dailyTotal { Text("\(total) daily videos, kept forever.") }
            }
        }
    }

    // MARK: archive

    @ViewBuilder private var archiveSection: some View {
        if let a = model.archive {
            Section {
                ForEach(a.items) { part in
                    Button {
                        if let v = part.media.video, let url = app.client?.url(for: v) {
                            video = VideoTarget(url: url, title: "Archive part \(part.part)")
                        }
                    } label: {
                        ArchiveRow(part: part)
                    }
                    .buttonStyle(.plain)
                    .disabled(part.current)
                    .accessibilityIdentifier("timelapse.archive")
                }
            } footer: {
                Text(a.enabled
                     ? "Every clip appended into long videos (a new part every 2 GB). The current part is being appended to, so it can't be played. Parts stream, so Wi-Fi is best."
                     : "The archive is turned off on the PC.")
            }
        }
    }

    // MARK: shared

    @ViewBuilder private var overlay: some View {
        let empty = switch part {
        case .clips: model.clips?.items.isEmpty ?? true
        case .daily: model.dailies.isEmpty
        case .archive: model.archive?.items.isEmpty ?? true
        }
        if empty {
            if let error = model.error {
                UnreachableView(error: error, serverName: app.connection?.serverName) {
                    Task { await load(part, force: true) }
                }
            } else if model.isLoading {
                ProgressView()
            } else {
                switch part {
                case .clips: ContentUnavailableView("No clips in the last 24 hours", systemImage: "film",
                                                    description: Text("Clips are skipped at night."))
                case .daily: ContentUnavailableView("No daily videos yet", systemImage: "calendar")
                case .archive: ContentUnavailableView("No archive yet", systemImage: "archivebox")
                }
            }
        }
    }

    private func load(_ part: Part, force: Bool) async {
        switch part {
        case .clips: if force || model.clips == nil { await model.loadClips(app: app) }
        case .daily: if force || model.dailies.isEmpty { await model.loadDailies(app: app) }
        case .archive: if force || model.archive == nil { await model.loadArchive(app: app) }
        }
    }

    private func play(_ clip: Clip) {
        guard let url = app.client?.url(for: clip.media.video) else { return }
        video = VideoTarget(url: url, title: "\(clip.windowStart.timeText)–\(clip.windowEnd.timeText)")
    }

    private func play(_ d: Daily) {
        guard let url = app.client?.url(for: d.media.video) else { return }
        video = VideoTarget(url: url, title: "Daily video, \(d.day.text(timeZone: d.modifiedAt.timeZone))")
    }
}

struct ClipRow: View {
    let clip: Clip
    var isLatest = false

    var body: some View {
        HStack(spacing: 12) {
            Poster(path: clip.media.poster)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text("\(clip.windowStart.timeText)–\(clip.windowEnd.timeText)").font(.subheadline.weight(.semibold))
                    if isLatest { Pill(text: "Newest", color: .green) }
                    if clip.starred { Image(systemName: "star.fill").foregroundStyle(.yellow).font(.caption) }
                }
                Text(details).font(.caption).foregroundStyle(.secondary)
                Text(clip.expiryText())
                    .font(.caption2)
                    .foregroundStyle(clip.isExpiringSoon() ? .orange : .secondary)
            }
            Spacer(minLength: 0)
        }
        .contentShape(.rect)
        .accessibilityElement(children: .combine)
    }

    private var details: String {
        var parts: [String] = []
        if let n = clip.sightingsCount { parts.append("\(n) sighting\(n == 1 ? "" : "s")") }
        if let d = clip.duration { parts.append("\(Int(d.rounded())) s") }
        parts.append(byteText(clip.sizeBytes))
        return parts.joined(separator: " · ")
    }
}

struct DailyRow: View {
    let daily: Daily

    var body: some View {
        HStack(spacing: 12) {
            Poster(path: daily.media.poster)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(daily.day.text(timeZone: daily.modifiedAt.timeZone)).font(.subheadline.weight(.semibold))
                    if daily.starred { Image(systemName: "star.fill").foregroundStyle(.yellow).font(.caption) }
                }
                Text([daily.sightingsCount.map { "\($0) sightings" }, daily.duration.map { "\(Int($0.rounded())) s" },
                      byteText(daily.sizeBytes)].compactMap { $0 }.joined(separator: " · "))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .contentShape(.rect)
        .accessibilityElement(children: .combine)
    }
}

struct ArchiveRow: View {
    let part: ArchivePart

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: part.current ? "archivebox" : "archivebox.fill")
                .font(.title2).foregroundStyle(part.current ? Color.secondary : Color.accentColor)
                .frame(width: 44)
            VStack(alignment: .leading, spacing: 3) {
                Text("Part \(part.part)").font(.subheadline.weight(.semibold))
                Text(part.current
                     ? "Being appended to · \(byteText(part.sizeBytes))"
                     : [part.duration.map { "\(Int(($0 / 60).rounded())) min" }, byteText(part.sizeBytes),
                        "updated \(part.modifiedAt.agoText())"].compactMap { $0 }.joined(separator: " · "))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
            if !part.current { Image(systemName: "play.circle").foregroundStyle(.tint) }
        }
        .contentShape(.rect)
        .accessibilityElement(children: .combine)
    }
}

struct Poster: View {
    let path: MediaPath?

    var body: some View {
        RemoteImage(path: path, maxPixelSize: 320)
            .frame(width: 96, height: 72)
            .clipShape(.rect(cornerRadius: 8))
            .overlay {
                Image(systemName: "play.circle.fill").font(.title2).foregroundStyle(.white).shadow(radius: 3)
            }
    }
}
