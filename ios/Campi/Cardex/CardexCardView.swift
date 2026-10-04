import CampiKit
import SwiftUI

/// A label's Cardex card: generated name, type, ratings and flavor text, plus the real catch stats from the PC.
struct CardexCardView: View {
    @Environment(AppModel.self) private var app
    @Environment(\.dynamicTypeSize) private var typeSize
    let item: CollectionItem
    /// Off where the page already shows the catch stats (the collection item page).
    var showsStats = true

    var body: some View {
        let card = app.cardex.card(for: item.label)
        let writing = app.cardex.generating.contains(item.label)
        VStack(alignment: .leading, spacing: 12) {
            AdaptiveStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(card?.text.displayName ?? item.label)
                    .font(.title3.bold())
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 4)
                if let type = card?.text.type {
                    Pill(text: type.uppercased(), color: item.tier.color)
                }
            }
            if writing {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Writing card…")
                }
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .accessibilityIdentifier("cardex.writing")
            } else if let card {
                if !card.text.ratings.isEmpty { ratings(card.text.ratings) }
                if let flavor = card.text.flavor {
                    Text("“\(flavor)”").font(.subheadline.italic())
                }
                if let note = card.note {
                    Label(note, systemImage: "apple.intelligence")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            if showsStats {
                Divider()
                stats
            }
            if item.isCaught {
                NavigationLink(value: CardexCardRoute(item: item)) {
                    Label("View Trading Card", systemImage: "rectangle.portrait.on.rectangle.portrait.angled")
                        .font(.subheadline.weight(.semibold))
                }
                .accessibilityIdentifier("cardex.viewCard")
            }
        }
        .padding()
        .background(.background.secondary, in: .rect(cornerRadius: 18))
        .overlay { RoundedRectangle(cornerRadius: 18).strokeBorder(item.tier.color.opacity(0.7), lineWidth: 2) }
        .contextMenu {
            Button("Regenerate Card", systemImage: "arrow.clockwise") {
                Task { await app.cardex.regenerate(item) }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("cardex.card")
        .task(id: item.label) { await app.cardex.load(item) }
    }

    private func ratings(_ ratings: [CardexText.Rating]) -> some View {
        Group {
            if typeSize.isAccessibilitySize {
                // name on its own line, bar and score below: three columns don't fit
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(ratings) { r in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(r.name).font(.subheadline)
                            HStack(spacing: 10) {
                                RatingBar(value: r.value, color: item.tier.color)
                                Text("\(r.value)").font(.subheadline.bold()).monospacedDigit()
                            }
                        }
                    }
                }
            } else {
                Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 6) {
                    ForEach(ratings) { r in
                        GridRow {
                            Text(r.name).font(.subheadline)
                            RatingBar(value: r.value, color: item.tier.color)
                            Text("\(r.value)").font(.subheadline.bold()).monospacedDigit()
                                .gridColumnAlignment(.trailing)
                        }
                    }
                }
            }
        }
        // one element for all ratings: "Ratings: Speed, 7 of 10; ..."
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Ratings: " + ratings.map { "\($0.name), \($0.value) of 10" }.joined(separator: "; "))
    }

    private var stats: some View {
        HStack(spacing: 8) {
            TierBadge(tier: item.tier)
            if item.isCaught {
                Text("Seen \(item.count)×")
                if let first = item.firstSeenAt {
                    Text("since \(first.date.formatted(Date.FormatStyle(timeZone: first.timeZone).month(.abbreviated).day()))")
                        .foregroundStyle(.secondary)
                }
            } else {
                Text("Not caught yet").foregroundStyle(.secondary)
            }
        }
        .font(.subheadline)
        .accessibilityElement(children: .combine)
    }
}

/// A 1–10 bar.
struct RatingBar: View {
    let value: Int
    let color: Color

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(.quaternary)
                Capsule().fill(color.gradient)
                    .frame(width: geo.size.width * CGFloat(min(max(value, 0), 10)) / 10)
            }
        }
        .frame(height: 8)
        .frame(maxWidth: .infinity)
    }
}
