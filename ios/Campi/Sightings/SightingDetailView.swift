import CampiKit
import SwiftUI

struct SightingDetailView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    let initial: Sighting

    @State private var showLabelPicker = false
    @State private var showFrame = false
    @State private var video: VideoTarget?
    @State private var message: String?
    @State private var seeking = false
    /// The collection item for the effective label, for its Cardex card.
    @State private var cardItem: CollectionItem?

    private var s: Sighting { app.sightings.resolve(initial) }
    private var actions: SightingActions { SightingActions(app: app) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                RemoteImage(path: s.media.crop, maxPixelSize: 1400, contentMode: .fit, label: "Photo of \(s.displayLabel)")
                    .aspectRatio(3 / 2, contentMode: .fit)
                    .clipShape(.rect(cornerRadius: 16))
                header
                if s.hidden { hiddenBanner }
                actionButtons
                verdict
                if let cardItem { CardexCardView(item: cardItem) }
                if !s.runnerUps.isEmpty { runnerUps }
                facts
                media
            }
            .padding()
        }
        .navigationTitle(s.displayLabel)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    run { try await actions.setStarred(!s.starred, s) }
                } label: {
                    Image(systemName: s.starred ? "star.fill" : "star")
                }
                .accessibilityLabel(s.starred ? "Unstar" : "Star")
                .accessibilityIdentifier("detail.star")
                .accessibilityValue(s.starred ? "starred" : "not starred")
            }
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button("Correct Label…", systemImage: "pencil") { showLabelPicker = true }
                    if s.correction != nil {
                        Button("Remove Correction", systemImage: "arrow.uturn.backward") {
                            run { try await actions.setLabel(nil, s) }
                        }
                    }
                    Divider()
                    if let crop = s.media.crop {
                        Button("Save Crop to Photos", systemImage: "photo.badge.arrow.down") {
                            app.saver.saveImage(crop, fileName: "campi_\(s.id.prefix(8))_crop.jpg", app: app)
                        }
                        Button("Share Crop…", systemImage: "square.and.arrow.up") {
                            app.saver.share(crop, fileName: "\(slugged)_crop.jpg", app: app)
                        }
                    }
                    if let clip = s.media.clip {
                        Button("Save Clip to Photos", systemImage: "square.and.arrow.down") {
                            app.saver.saveVideo(clip, fileName: "\(slugged).mp4", app: app)
                        }
                    }
                    if let cardItem {
                        Button("Regenerate Card", systemImage: "arrow.clockwise") {
                            Task { await app.cardex.regenerate(cardItem) }
                        }
                    }
                    Divider()
                    if s.hidden {
                        Button("Unhide", systemImage: "eye") { run { try await actions.setHidden(false, s) } }
                    } else {
                        Button("Hide", systemImage: "eye.slash", role: .destructive) {
                            run {
                                try await actions.setHidden(true, s)
                                dismiss()
                            }
                        }
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                .accessibilityLabel("More")
            }
        }
        .sheet(isPresented: $showLabelPicker) {
            LabelPickerView(current: s.label, corrected: s.correction != nil) { label in
                run { try await actions.setLabel(label, s) }
            }
        }
        .fullScreenCover(isPresented: $showFrame) { FrameViewer(path: s.media.frame) }
        .videoPlayer($video)
        .alert("Campi", isPresented: Binding(get: { message != nil }, set: { if !$0 { message = nil } })) {
            Button("OK") {}
        } message: {
            Text(message ?? "")
        }
        .task(id: s.id) { await refresh() }
        .task(id: s.label) {
            // a correction moves the sighting to another label's card
            guard let label = s.label else { cardItem = nil; return }
            cardItem = (try? await app.loadCollection())?.items.first { $0.label == label }
        }
    }

    // MARK: sections

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(s.displayLabel).font(.title.bold())
            if let make = s.make, let model = s.model {
                Text("\(make) · \(model)").foregroundStyle(.secondary)
            }
            FlowLayout(spacing: 6) {
                DecidedByBadge(sighting: s)
                Pill(text: percentText(s.confidence), symbol: "gauge.with.dots.needle.33percent")
                if s.unsure && s.decidedBy == .siglip { Pill(text: "unsure", color: .orange) }
                if s.stationary { Pill(text: "parked", symbol: "parkingsign") }
            }
            if s.isAwaitingCloud {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small).accessibilityHidden(true)
                    Text("Asking Gemini for a second opinion…").font(.subheadline).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var hiddenBanner: some View {
        HStack {
            Label("Hidden from lists and counts", systemImage: "eye.slash")
            Spacer()
            Button("Unhide") { run { try await actions.setHidden(false, s) } }
        }
        .font(.subheadline)
        .padding(12)
        .background(.orange.opacity(0.12), in: .rect(cornerRadius: 12))
    }

    private var actionButtons: some View {
        AdaptiveStack(spacing: 12) {
            Button {
                Task { await viewInTimelapse() }
            } label: {
                Label(seeking ? "Finding…" : "View in Timelapse", systemImage: "film")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(seeking)
            .accessibilityIdentifier("detail.timelapse")
            Button {
                showLabelPicker = true
            } label: {
                Label("Correct", systemImage: "pencil").frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .accessibilityIdentifier("detail.correct")
        }
        .controlSize(.large)
    }

    @ViewBuilder private var verdict: some View {
        if let c = s.correction {
            note("You corrected this \(c.at.agoText()). It was \(s.machine.label ?? "unlabeled") (\(s.machine.source == .cloud ? "Gemini" : "SigLIP") \(percentText(s.machine.confidence))).",
                 symbol: "person.fill")
        } else if s.decidedBy == .cloud, let sl = s.siglip.label, sl != s.label {
            note("Gemini named it. SigLIP guessed \(sl) (\(percentText(s.siglip.confidence))).", symbol: "sparkle")
        }
    }

    private var runnerUps: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Other SigLIP guesses").font(.headline)
            ForEach(s.runnerUps) { r in
                HStack {
                    Text(r.label)
                    Spacer()
                    Text(percentText(r.p)).monospacedDigit().foregroundStyle(.secondary)
                }
                .font(.subheadline)
                ProgressView(value: min(max(r.p, 0), 1)).tint(.secondary)
            }
        }
    }

    private var facts: some View {
        VStack(spacing: 0) {
            fact("Seen", "\(s.startedAt.dateTimeText) for \(durationText)")
            if let d = s.direction { fact("Direction", d == .leftToRight ? "Left to right" : d == .rightToLeft ? "Right to left" : "–") }
            if let c = s.yoloClass { fact("Detected as", c) }
            if let y = s.yearRange { fact("Years", y) }
            if let c = s.color { fact("Color", c.capitalized) }
            if let f = s.trackFrames { fact("Tracked frames", "\(f)") }
            if let px = s.maxBoxPx { fact("Largest box", "\(px) px") }
        }
        .background(.background.secondary, in: .rect(cornerRadius: 14))
    }

    @ViewBuilder private var media: some View {
        if let clip = s.media.clip, let url = app.client?.url(for: clip) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Clip").font(.headline)
                InlineClipPlayer(url: url)
                    .aspectRatio(4 / 3, contentMode: .fit)
                    .clipShape(.rect(cornerRadius: 14))
            }
        }
        if s.media.frame != nil {
            VStack(alignment: .leading, spacing: 8) {
                Text("Full frame").font(.headline)
                Button { showFrame = true } label: {
                    RemoteImage(path: s.media.frame, maxPixelSize: 1400, contentMode: .fit, label: "Full camera frame")
                        .aspectRatio(4 / 3, contentMode: .fit)
                        .clipShape(.rect(cornerRadius: 14))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Full frame, opens zoomable view")
            }
        }
    }

    // MARK: helpers

    /// File-name stem for saved media: "campi_toyota-camry_5f0c2a9e".
    private var slugged: String {
        let label = s.displayLabel.lowercased().map { $0.isLetter || $0.isNumber ? $0 : "-" }
        return "campi_\(String(label))_\(s.id.prefix(8))"
    }

    private var durationText: String {
        let secs = s.endedAt.date.timeIntervalSince(s.startedAt.date)
        return secs < 90 ? "\(Int(secs.rounded())) s" : "\(Int((secs / 60).rounded())) min"
    }

    private func fact(_ title: String, _ value: String) -> some View {
        LabeledContent(title, value: value)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
    }

    private func note(_ text: String, symbol: String) -> some View {
        Label(text, systemImage: symbol)
            .font(.subheadline)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.tint.opacity(0.08), in: .rect(cornerRadius: 12))
    }

    private func run(_ op: @escaping @MainActor () async throws -> Void) {
        Task {
            do {
                try await op()
            } catch let e as APIError where e.code == .unknownLabel {
                message = "That label isn't in the collection any more. Pull to refresh Collection and try again."
            } catch {
                message = (error as? APIError)?.localizedDescription ?? error.localizedDescription
            }
        }
    }

    private func viewInTimelapse() async {
        guard let client = app.client else { return }
        seeking = true
        defer { seeking = false }
        do {
            switch try await Timelapse.target(at: s.midpoint, client: client, timeZone: s.startedAt.timeZone) {
            case .play(let target): video = target
            case .message(let text): message = text
            }
        } catch {
            app.report(error)
            message = (error as? APIError)?.localizedDescription ?? error.localizedDescription
        }
    }

    /// Fetch the latest version; while Gemini is pending, check again every 5 s (for up to 2 minutes).
    private func refresh() async {
        guard let client = app.client else { return }
        for _ in 0..<24 {
            guard let fresh = try? await client.sighting(id: initial.id) else { return }
            app.sightings.record(fresh)
            guard fresh.isAwaitingCloud else { return }
            try? await Task.sleep(for: .seconds(5))
            if Task.isCancelled { return }
        }
    }
}

/// Zoomable full frame.
struct FrameViewer: View {
    let path: MediaPath?
    @Environment(\.dismiss) private var dismiss
    @State private var scale: CGFloat = 1
    @State private var base: CGFloat = 1

    var body: some View {
        NavigationStack {
            GeometryReader { geo in
                ScrollView([.horizontal, .vertical], showsIndicators: false) {
                    RemoteImage(path: path, maxPixelSize: 2400, contentMode: .fit)
                        .frame(width: geo.size.width * scale, height: geo.size.height * scale)
                }
                .gesture(MagnifyGesture()
                    .onChanged { scale = min(max(base * $0.magnification, 1), 6) }
                    .onEnded { _ in base = scale })
                .onTapGesture(count: 2) {
                    withAnimation { scale = scale > 1 ? 1 : 2.5; base = scale }
                }
            }
            .background(.black)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .toolbarBackground(.visible, for: .navigationBar)
        }
    }
}
