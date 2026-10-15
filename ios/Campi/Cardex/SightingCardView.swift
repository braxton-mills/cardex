import CampiKit
import SwiftUI

/// A sighting's own trading card on its detail page: the 3D car in this car's paint, text written for this car.
/// Tap it to hold it in the inspector.
struct SightingCardView: View {
    @Environment(AppModel.self) private var app
    let sighting: Sighting
    /// The sighting's label in the collection: the card's finish and catch count.
    let item: CollectionItem

    var body: some View {
        let card = app.cardex.card(for: sighting)
        VStack(spacing: 12) {
            NavigationLink(value: CardexCardRoute(item: item, sighting: sighting)) {
                TradingCardView(item: item, sighting: sighting)
                    .frame(maxWidth: 240)
                    .shadow(color: .black.opacity(0.3), radius: 10, y: 6)
            }
            .buttonStyle(.plain)
            .accessibilityHint("Opens the card to tilt it")
            .accessibilityIdentifier("cardex.sightingCard")
            if app.cardex.isGenerating(sighting) {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Writing card…")
                }
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .accessibilityIdentifier("cardex.writing")
            } else if let note = card?.note {
                Label(note, systemImage: "apple.intelligence")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            NavigationLink(value: CardexCardRoute(item: item)) {
                Label("View \(item.label) Card", systemImage: "rectangle.portrait.on.rectangle.portrait.angled")
                    .font(.subheadline.weight(.semibold))
            }
            .accessibilityIdentifier("cardex.viewCard")
        }
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("cardex.card")
    }
}
