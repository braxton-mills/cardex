import CampiKit
import SwiftUI

/// One trading card, held: drag it to tilt it, and the foil and glare follow; let go and it settles flat.
struct CardInspectorView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let item: CollectionItem

    @State private var tilt: CGPoint = Self.restingTilt
    @State private var held = false

    /// Flat, or for screenshots a fixed tilt from `-cardexTilt x,y` (debug builds).
    private static let restingTilt: CGPoint = {
        #if DEBUG
        let args = ProcessInfo.processInfo.arguments
        if let i = args.firstIndex(of: "-cardexTilt"), i + 1 < args.count {
            let xy = args[i + 1].split(separator: ",").compactMap { Double($0) }
            if xy.count == 2 { return CGPoint(x: xy[0], y: xy[1]) }
        }
        #endif
        return .zero
    }()

    private var finish: CardFinish { CardFinish(for: item) ?? .plain }
    private var maxTilt: Double { reduceMotion ? 6 : 18 }

    var body: some View {
        let collection = app.collection
        let number = (collection?.items.firstIndex { $0.label == item.label } ?? -1) + 1
        ScrollView {
            VStack(spacing: 22) {
                TradingCardView(item: item, number: number, total: collection?.total ?? 0, tilt: tilt, live: true,
                                spins: !reduceMotion)
                    .frame(maxWidth: 380)
                    .rotation3DEffect(.degrees(tilt.x * maxTilt), axis: (x: 0, y: 1, z: 0), perspective: 0.6)
                    .rotation3DEffect(.degrees(-tilt.y * maxTilt), axis: (x: 1, y: 0, z: 0), perspective: 0.6)
                    .scaleEffect(held ? 1.03 : 1)
                    .shadow(color: .black.opacity(0.45), radius: held ? 24 : 14, x: -tilt.x * 12, y: 14 - tilt.y * 8)
                    .gesture(drag)
                    .sensoryFeedback(.impact(weight: .light), trigger: held) { _, new in new && finish >= .holo }
                    .padding(.horizontal, 28)
                    .padding(.top, 12)
                Text("Drag the card to tilt it")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .accessibilityHidden(true)
                details
                    .padding(.horizontal)
            }
            .padding(.bottom, 24)
        }
        .scrollDisabled(held)
        .background {
            RadialGradient(colors: [Color(white: 0.22), Color(white: 0.05)], center: .center, startRadius: 0,
                           endRadius: 600)
                .ignoresSafeArea()
        }
        .environment(\.colorScheme, .dark)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .navigationTitle(app.cardex.card(for: item.label)?.text.displayName ?? item.label)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button("Regenerate Card", systemImage: "arrow.clockwise") {
                        Task { await app.cardex.regenerate(item) }
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
                .accessibilityLabel("More")
            }
        }
    }

    private var drag: some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { g in
                if !held { withAnimation(.spring(duration: 0.25)) { held = true } }
                // a quarter of the card's width tilts it all the way
                let x = g.translation.width / 110, y = g.translation.height / 150
                tilt = CGPoint(x: max(-1, min(1, x)), y: max(-1, min(1, y)))
            }
            .onEnded { _ in
                withAnimation(.spring(response: 0.5, dampingFraction: 0.55)) {
                    tilt = Self.restingTilt
                    held = false
                }
            }
    }

    private var details: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                TierBadge(tier: item.tier)
                Pill(text: finish.title, symbol: "sparkles", color: finish >= .holo ? .yellow : .secondary)
                if item.origin == .discovered { Pill(text: "Discovered", symbol: "sparkle", color: .indigo) }
            }
            Text("Seen \(item.count) time\(item.count == 1 ? "" : "s")").font(.headline)
            if let first = item.firstSeenAt {
                Text("First \(first.dateTimeText)").font(.subheadline).foregroundStyle(.secondary)
            }
            if let last = item.lastSeenAt, item.count > 1 {
                Text("Last \(last.dateTimeText)").font(.subheadline).foregroundStyle(.secondary)
            }
            if let note = app.cardex.card(for: item.label)?.note {
                Label(note, systemImage: "apple.intelligence").font(.caption).foregroundStyle(.secondary)
            }
            NavigationLink(value: item) {
                Label("See Sightings", systemImage: "car.side")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .accessibilityIdentifier("cardex.seeSightings")
        }
        .frame(maxWidth: 420, alignment: .leading)
    }
}
