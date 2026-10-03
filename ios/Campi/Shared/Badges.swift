import CampiKit
import SwiftUI

/// A small tag. The text stays primary for contrast; the color tints the background and the symbol.
struct Pill: View {
    let text: String
    var symbol: String?
    var color: Color = .secondary

    var body: some View {
        HStack(spacing: 3) {
            if let symbol { Image(systemName: symbol).foregroundStyle(color) }
            Text(text).foregroundStyle(.primary)
        }
        .lineLimit(1)
        .fixedSize()   // never break inside a tag; containers wrap tags instead (FlowLayout)
        .font(.caption2.weight(.semibold))
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .background(color.opacity(0.2), in: .capsule)
    }
}

/// A row that becomes a column at accessibility text sizes, where side-by-side content gets crushed.
struct AdaptiveStack<Content: View>: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    var alignment: VerticalAlignment = .center
    var spacing: CGFloat? = nil
    @ViewBuilder var content: Content

    var body: some View {
        let layout = typeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: spacing))
            : AnyLayout(HStackLayout(alignment: alignment, spacing: spacing))
        layout { content }
    }
}

/// Lays out children left to right, wrapping to new lines (pills on narrow cards and at large text sizes).
struct FlowLayout: Layout {
    var spacing: CGFloat = 4

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(subviews, width: proposal.width ?? .infinity)
        return CGSize(width: rows.map(\.width).max() ?? 0,
                      height: rows.map(\.height).reduce(0, +) + spacing * CGFloat(max(rows.count - 1, 0)))
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(subviews, width: bounds.width) {
            var x = bounds.minX
            for i in row.items {
                let size = subviews[i].sizeThatFits(.init(width: bounds.width, height: nil))
                subviews[i].place(at: CGPoint(x: x, y: y), proposal: .init(size))
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }

    private struct Row { var items: [Int] = []; var width: CGFloat = 0; var height: CGFloat = 0 }

    private func arrange(_ subviews: Subviews, width: CGFloat) -> [Row] {
        var rows: [Row] = [Row()]
        for i in subviews.indices {
            let size = subviews[i].sizeThatFits(.init(width: width, height: nil))
            if !rows[rows.count - 1].items.isEmpty, rows[rows.count - 1].width + spacing + size.width > width {
                rows.append(Row())
            }
            var row = rows[rows.count - 1]
            row.width += (row.items.isEmpty ? 0 : spacing) + size.width
            row.height = max(row.height, size.height)
            row.items.append(i)
            rows[rows.count - 1] = row
        }
        return rows.filter { !$0.items.isEmpty }
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
