import CampiKit
import SwiftUI

struct Pill: View {
    let text: String
    var symbol: String?
    var color: Color = .secondary

    var body: some View {
        HStack(spacing: 3) {
            if let symbol { Image(systemName: symbol) }
            Text(text)
        }
        .font(.caption2.weight(.semibold))
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .foregroundStyle(color)
        .background(color.opacity(0.14), in: .capsule)
    }
}

extension Tier {
    var title: String {
        switch self {
        case .uncaught: "Not caught"
        case .rare: "Rare"
        case .uncommon: "Uncommon"
        case .common: "Common"
        case .unknown: "?"
        }
    }

    var color: Color {
        switch self {
        case .rare: .purple
        case .uncommon: .blue
        case .common: .gray
        case .uncaught, .unknown: .secondary
        }
    }
}

struct TierBadge: View {
    let tier: Tier
    var body: some View { Pill(text: tier.title, color: tier.color) }
}

extension Highlight.Kind {
    var title: String {
        switch self {
        case .newCatch: "New catch"
        case .rare: "Rare"
        case .busiest: "Busiest"
        case .daily: "Daily video"
        case .starred: "Starred"
        case .unknown: "Highlight"
        }
    }

    var symbol: String {
        switch self {
        case .newCatch: "sparkles"
        case .rare: "diamond.fill"
        case .busiest: "flame.fill"
        case .daily: "calendar"
        case .starred: "star.fill"
        case .unknown: "circle"
        }
    }

    var color: Color {
        switch self {
        case .newCatch: .green
        case .rare: .purple
        case .busiest: .orange
        case .daily: .blue
        case .starred: .yellow
        case .unknown: .secondary
        }
    }
}

struct DecidedByBadge: View {
    let sighting: Sighting
    var body: some View {
        Pill(text: sighting.decidedBy.badge, symbol: sighting.decidedBy.symbol,
             color: sighting.decidedBy == .cloud ? .indigo : (sighting.decidedBy == .user ? .teal : .secondary))
    }
}

/// Selectable capsule for filter rows.
struct Chip: View {
    let title: String
    var symbol: String?
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                if let symbol { Image(systemName: symbol) }
                Text(title)
            }
            .font(.subheadline.weight(.medium))
            .padding(.horizontal, 12)
            .padding(.vertical, 7)
            .foregroundStyle(selected ? Color.white : Color.primary)
            .background(selected ? AnyShapeStyle(.tint) : AnyShapeStyle(.quaternary), in: .capsule)
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

struct DayHeader: View {
    let day: PCDay
    let timeZone: TimeZone
    var count: Int?

    var body: some View {
        HStack {
            Text(day.text(timeZone: timeZone)).font(.headline)
            Spacer()
            if let count { Text("\(count)").font(.subheadline).foregroundStyle(.secondary) }
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 4)
        .background(.bar)
    }
}
