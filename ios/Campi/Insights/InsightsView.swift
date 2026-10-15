import Accessibility
import CampiKit
import Charts
import SwiftUI

/// Traffic patterns: when the street is busy, which way it flows, the most common colors and makes, and the
/// busiest day ever. Opened from Today.
struct InsightsView: View {
    @Environment(AppModel.self) private var app
    @State private var model = InsightsModel()
    @State private var video: VideoTarget?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Picker("Range", selection: $model.range) {
                    ForEach(InsightsModel.Range.allCases) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("insights.range")

                if let progress = model.progress {
                    ProgressView(value: Double(progress.done), total: Double(max(progress.total, 1))) {
                        Text("Counting sightings · \(progress.done) of \(progress.total) days")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                if let error = model.error {
                    Label(error.localizedDescription, systemImage: "exclamationmark.triangle")
                        .font(.subheadline).foregroundStyle(.orange)
                }
                content
            }
            .padding()
        }
        .navigationTitle("Insights")
        .navigationBarTitleDisplayMode(.large)
        .refreshable { await model.load(app: app) }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu("More", systemImage: "ellipsis") {
                    Button("Recount Every Day", systemImage: "arrow.counterclockwise") {
                        Task { await model.recount(app: app) }
                    }
                }
            }
        }
        .task(id: model.range) { await model.load(app: app) }
        .videoPlayer($video)
    }

    @ViewBuilder private var content: some View {
        if let status = model.status, !status.sightings.hasHistory {
            ContentUnavailableView("No sightings yet", systemImage: "chart.bar.xaxis",
                                   description: Text("Enable [sightings] on the PC to log passing vehicles."))
        } else if let insights = model.insights {
            if let busiest = model.busiestEver {
                BusiestDayCard(busiest: busiest, daily: model.dailies[busiest.day], timeZone: model.timeZone,
                               average: insights.days > 0 ? Double(insights.total) / Double(insights.days) : nil,
                               rangeTitle: model.range.averageTitle) { play($0) }
            }
            if insights.total == 0 {
                ContentUnavailableView("Nothing counted in this range", systemImage: "car.side",
                                       description: Text("Try a longer range."))
            } else {
                Text("\(insights.total.formatted()) sightings in \(insights.days) day\(insights.days == 1 ? "" : "s")")
                    .font(.subheadline).foregroundStyle(.secondary)
                HeatmapCard(insights: insights)
                DirectionCard(insights: insights)
                SharesCard(title: "Colors", symbol: "paintpalette", shares: insights.colors, known: insights.colorsKnown,
                           total: insights.total, note: "Colors come from Gemini, so only cloud-checked sightings have one.",
                           swatch: true)
                SharesCard(title: "Makes", symbol: "car.side", shares: insights.makes, known: insights.makesKnown,
                           total: insights.total, note: "Generic types like dump truck have no make.", swatch: false)
            }
        } else if model.error == nil {
            ProgressView().frame(maxWidth: .infinity, minHeight: 200)
        }
    }

    private func play(_ d: Daily) {
        guard let url = app.client?.url(for: d.media.video) else { return }
        video = VideoTarget(url: url, title: "Daily video, \(d.day.text(timeZone: d.modifiedAt.timeZone))",
                            file: SavableFile(path: d.media.video, fileName: d.fileName))
    }
}

// MARK: Cards

private struct InsightsCard<Content: View>: View {
    let title: String
    let symbol: String
    var subtitle: String?
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Label(title, systemImage: symbol).font(.headline)
                if let subtitle { Text(subtitle).font(.caption).foregroundStyle(.secondary) }
            }
            content
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: .rect(cornerRadius: 16))
    }
}

private struct BusiestDayCard: View {
    let busiest: DayCount
    let daily: Daily?
    let timeZone: TimeZone
    let average: Double?
    let rangeTitle: String
    let play: (Daily) -> Void

    var body: some View {
        InsightsCard(title: "Busiest day ever", symbol: "trophy") {
            VStack(alignment: .leading, spacing: 4) {
                Text(busiest.count.formatted())
                    .font(.system(.largeTitle, design: .rounded).bold())
                    .monospacedDigit()
                    + Text(" sightings").font(.title3).foregroundStyle(.secondary)
                Text(busiest.day.noon(in: timeZone).formatted(
                    Date.FormatStyle(timeZone: timeZone).weekday(.wide).month(.wide).day().year()))
                    .font(.subheadline.weight(.semibold))
                if let average, average > 0 {
                    Text("\((Double(busiest.count) / average).formatted(.number.precision(.fractionLength(1))))× the \(rangeTitle) average of \(average.formatted(.number.precision(.fractionLength(1)))) a day")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("insights.busiest")
            if let daily {
                Button { play(daily) } label: {
                    Label("Watch That Day", systemImage: "play.fill")
                }
                .buttonStyle(.bordered)
            }
        }
    }
}

private struct HeatmapCard: View {
    let insights: Insights
    /// (weekday, hour) of the tapped cell, kept across ranges.
    @State private var selectedKey: [Int]?
    private var selected: Insights.Cell? {
        selectedKey.flatMap { k in insights.heatmap.first { $0.weekday == k[0] && $0.hour == k[1] } }
    }
    @Environment(\.colorScheme) private var scheme

    private var weekdays: [Int] {
        let first = Calendar.current.firstWeekday
        return (0..<7).map { (first - 1 + $0) % 7 + 1 }
    }
    private var peak: Double { insights.heatmap.map(\.average).max() ?? 0 }
    /// The week's first day on the top row.
    private func row(of weekday: Int) -> Int { 6 - (weekdays.firstIndex(of: weekday) ?? 0) }

    var body: some View {
        InsightsCard(title: "When it's busy", symbol: "calendar",
                     subtitle: "Average sightings per hour, by day of the week") {
            // Numeric axes (hour 0–24 across, row 0–7 up), so labels sit beside the rows and only every 6 hours.
            Chart(insights.heatmap, id: \.self) { cell in
                let row = Double(row(of: cell.weekday))
                RectangleMark(xStart: .value("Hour", Double(cell.hour) + 0.07), xEnd: .value("Hour", Double(cell.hour) + 0.93),
                              yStart: .value("Day", row + 0.07), yEnd: .value("Day", row + 0.93))
                    .foregroundStyle(SequentialRamp.color(cell.average, peak: peak, scheme: scheme))
                    .opacity(selected == nil || selected == cell ? 1 : 0.45)
                    .cornerRadius(2)
            }
            .chartXScale(domain: 0...24)
            .chartYScale(domain: 0...7)
            .chartXAxis {
                AxisMarks(values: [0, 6, 12, 18]) { v in
                    AxisValueLabel(anchor: .topLeading) { if let h = v.as(Int.self) { Text(hourLabel(h)) } }
                }
            }
            .chartYAxis {
                AxisMarks(position: .leading, values: (0..<7).map { Double($0) + 0.5 }) { v in
                    AxisValueLabel {
                        if let y = v.as(Double.self) { Text(dayName(weekdays[6 - Int(y)])) }
                    }
                }
            }
            .chartGesture { proxy in
                SpatialTapGesture().onEnded { tap in
                    guard let (x, y) = proxy.value(at: tap.location, as: (Double, Double).self),
                          (0..<24).contains(Int(x)), (0..<7).contains(Int(y)),
                          let cell = insights.heatmap.first(where: { $0.hour == Int(x) && $0.weekday == weekdays[6 - Int(y)] })
                    else { return }
                    let key = [cell.weekday, cell.hour]
                    selectedKey = selectedKey == key ? nil : key
                }
            }
            .frame(height: 200)
            // One element, not 168 tiny ones: a spoken summary, plus the audio graph and data table.
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Average sightings per hour, by day of the week")
            .accessibilityValue(spokenSummary)
            .accessibilityChartDescriptor(HeatmapDescriptor(insights: insights, weekdays: weekdays))
            .accessibilityIdentifier("insights.heatmap")

            HStack {
                if let selected {
                    Text("\(dayName(selected.weekday, wide: true)), \(hourLabel(selected.hour))–\(hourLabel((selected.hour + 1) % 24)): ")
                        + Text(averageText(selected.average)).bold()
                } else if let busiest = insights.heatmap.max(by: { $0.average < $1.average }), busiest.average > 0 {
                    Text("Busiest: \(dayName(busiest.weekday, wide: true))s around \(hourLabel(busiest.hour)), ")
                        + Text(averageText(busiest.average)).bold()
                }
                Spacer(minLength: 8)
                RampLegend(scheme: scheme)
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }

    private var spokenSummary: String {
        guard let top = insights.heatmap.max(by: { $0.average < $1.average }), top.average > 0 else { return "No sightings" }
        let byDay = Dictionary(grouping: insights.heatmap, by: \.weekday).mapValues { $0.reduce(0) { $0 + $1.average } }
        var text = "Busiest: \(dayName(top.weekday, wide: true))s around \(hourLabel(top.hour)), \(averageText(top.average))."
        if let day = byDay.max(by: { $0.value < $1.value }) {
            text += " Busiest day of the week: \(dayName(day.key, wide: true)), \(day.value.formatted(.number.precision(.fractionLength(1)))) a day."
        }
        return text
    }

    private func dayName(_ weekday: Int, wide: Bool = false) -> String {
        let symbols = wide ? Calendar.current.weekdaySymbols : Calendar.current.shortWeekdaySymbols
        return symbols[weekday - 1]
    }

    private func averageText(_ v: Double) -> String {
        "\(v.formatted(.number.precision(.fractionLength(v < 10 ? 1 : 0)))) on average"
    }
}

/// The heatmap as data for VoiceOver's audio graph and chart table: one series per weekday.
private struct HeatmapDescriptor: AXChartDescriptorRepresentable {
    let insights: Insights
    let weekdays: [Int]

    func makeChartDescriptor() -> AXChartDescriptor {
        let peak = insights.heatmap.map(\.average).max() ?? 0
        let x = AXCategoricalDataAxisDescriptor(title: "Hour", categoryOrder: (0..<24).map(hourLabel))
        let y = AXNumericDataAxisDescriptor(title: "Average sightings", range: 0...max(peak, 1), gridlinePositions: []) {
            $0.formatted(.number.precision(.fractionLength(1)))
        }
        let series = weekdays.map { wd in
            AXDataSeriesDescriptor(name: Calendar.current.weekdaySymbols[wd - 1], isContinuous: false,
                                   dataPoints: insights.heatmap.filter { $0.weekday == wd }
                                       .map { AXDataPoint(x: hourLabel($0.hour), y: $0.average) })
        }
        return AXChartDescriptor(title: "Average sightings per hour, by day of the week", summary: nil,
                                 xAxis: x, yAxis: y, additionalAxes: [], series: series)
    }
}

private struct DirectionCard: View {
    let insights: Insights
    @State private var hour: Int?

    static let leftToRight = "Left to right"
    static let rightToLeft = "Right to left"

    var body: some View {
        InsightsCard(title: "Which way", symbol: "arrow.left.arrow.right",
                     subtitle: "Average sightings per hour on a typical day, by direction") {
            Chart {
                ForEach(insights.directions, id: \.hour) { d in
                    LineMark(x: .value("Hour", d.hour), y: .value("Sightings", d.leftToRight),
                             series: .value("Direction", Self.leftToRight))
                        .foregroundStyle(by: .value("Direction", Self.leftToRight))
                    LineMark(x: .value("Hour", d.hour), y: .value("Sightings", d.rightToLeft),
                             series: .value("Direction", Self.rightToLeft))
                        .foregroundStyle(by: .value("Direction", Self.rightToLeft))
                }
                .interpolationMethod(.monotone)
                .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round))

                if let hour, let d = insights.directions.first(where: { $0.hour == hour }) {
                    RuleMark(x: .value("Hour", hour))
                        .foregroundStyle(.secondary.opacity(0.5))
                        .lineStyle(StrokeStyle(lineWidth: 1))
                        .annotation(position: .top, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(hourLabel(hour))–\(hourLabel((hour + 1) % 24))").bold()
                                Text("→ \(per(d.leftToRight))")
                                Text("← \(per(d.rightToLeft))")
                            }
                            .font(.caption2)
                            .padding(6)
                            .background(.background, in: .rect(cornerRadius: 8))
                            .shadow(color: .black.opacity(0.12), radius: 3)
                        }
                    PointMark(x: .value("Hour", hour), y: .value("Sightings", d.leftToRight))
                        .foregroundStyle(DirectionPalette.leftToRight).symbolSize(60)
                    PointMark(x: .value("Hour", hour), y: .value("Sightings", d.rightToLeft))
                        .foregroundStyle(DirectionPalette.rightToLeft).symbolSize(60)
                }
            }
            .chartForegroundStyleScale([Self.leftToRight: DirectionPalette.leftToRight,
                                        Self.rightToLeft: DirectionPalette.rightToLeft])
            .chartLegend(position: .top, alignment: .leading)
            .chartXScale(domain: 0...23)
            .chartXAxis {
                AxisMarks(values: [0, 6, 12, 18, 23]) { v in
                    AxisGridLine().foregroundStyle(.quaternary)
                    AxisValueLabel { if let h = v.as(Int.self) { Text(hourLabel(h)) } }
                }
            }
            .chartYAxis {
                AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) {
                    AxisGridLine().foregroundStyle(.quaternary)
                    AxisValueLabel()
                }
            }
            .chartXSelection(value: $hour)
            .frame(height: 220)
            .accessibilityIdentifier("insights.direction")

            if let summary = commuteSummary {
                Text(summary).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func per(_ v: Double) -> String { v.formatted(.number.precision(.fractionLength(1))) }

    /// "Mornings lean right to left (64%); evenings lean left to right (58%)."
    private var commuteSummary: String? {
        let total = insights.leftToRightTotal + insights.rightToLeftTotal
        guard total > 0 else { return nil }
        func lean(_ hours: ClosedRange<Int>) -> (name: String, share: Int)? {
            let lr = hours.reduce(0.0) { $0 + insights.directions[$1].leftToRight }
            let rl = hours.reduce(0.0) { $0 + insights.directions[$1].rightToLeft }
            guard lr + rl > 0 else { return nil }
            let share = max(lr, rl) / (lr + rl)
            guard share >= 0.55 else { return nil }
            return ((lr > rl ? Self.leftToRight : Self.rightToLeft).lowercased(), Int((share * 100).rounded()))
        }
        let parts = [("Mornings", lean(6...9)), ("evenings", lean(15...18))].compactMap { label, l in
            l.map { "\(label) lean \($0.name) (\($0.share)%)" }
        }
        let overall = "\(insights.leftToRightTotal.formatted()) went left to right and \(insights.rightToLeftTotal.formatted()) right to left."
        guard !parts.isEmpty else { return overall + " Traffic is about even both ways." }
        return overall + " " + parts.joined(separator: "; ").capitalizedFirst + "."
    }
}

private struct SharesCard: View {
    let title: String
    let symbol: String
    let shares: [Insights.Share]
    let known: Int
    let total: Int
    let note: String
    let swatch: Bool

    var body: some View {
        InsightsCard(title: title, symbol: symbol,
                     subtitle: known == total ? nil : "\(known.formatted()) of \(total.formatted()) sightings have one") {
            if shares.isEmpty {
                Text(note).font(.subheadline).foregroundStyle(.secondary)
            } else {
                Chart(shares) { s in
                    BarMark(x: .value("Sightings", s.count), y: .value(title, s.name), height: .ratio(0.7))
                        .foregroundStyle(s.name == "Other" ? AnyShapeStyle(.gray.opacity(0.5)) : AnyShapeStyle(SequentialRamp.bar))
                        .cornerRadius(4)
                        .annotation(position: .trailing, spacing: 6) {
                            Text("\(s.count.formatted()) · \(percent(s.count))")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        .accessibilityLabel(display(s.name))
                        .accessibilityValue("\(s.count) sightings, \(percent(s.count))")
                }
                .chartYScale(domain: shares.map(\.name))
                .chartXAxis(.hidden)
                .chartXScale(range: .plotDimension(endPadding: 72))
                .chartYAxis {
                    AxisMarks(position: .leading) { v in
                        AxisValueLabel {
                            if let name = v.as(String.self) {
                                HStack(spacing: 6) {
                                    if swatch { PaintSwatch(name: name) }
                                    Text(display(name)).foregroundStyle(.primary)
                                }
                            }
                        }
                    }
                }
                .frame(height: CGFloat(shares.count) * 30)
                .accessibilityIdentifier("insights.\(title.lowercased())")
                if known < total {
                    Text(note).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private func display(_ name: String) -> String { swatch ? name.capitalizedFirst : name }

    private func percent(_ n: Int) -> String {
        (Double(n) / Double(max(known, 1))).formatted(.percent.precision(.fractionLength(0)))
    }
}

/// A dot in the car's paint color, from Gemini's color name.
private struct PaintSwatch: View {
    let name: String

    var body: some View {
        Group {
            if let color = Self.color(for: name) {
                Circle().fill(color).overlay(Circle().strokeBorder(.secondary.opacity(0.6), lineWidth: 0.5))
            } else {
                Circle().strokeBorder(.secondary, style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
            }
        }
        .frame(width: 12, height: 12)
        .accessibilityHidden(true)
    }

    /// Longest names first so "dark blue" wins over "blue".
    static let paints: [(String, UInt32)] = [
        ("dark blue", 0x1F3A68), ("navy", 0x1F2D4D), ("light blue", 0x8DB8E2), ("dark green", 0x24452F),
        ("dark gray", 0x45484D), ("dark grey", 0x45484D), ("light gray", 0xB9BCC0), ("light grey", 0xB9BCC0),
        ("maroon", 0x6B1F27), ("burgundy", 0x6B1F27), ("silver", 0xC4C8CD), ("white", 0xF7F7F5), ("black", 0x16171A),
        ("gray", 0x7D8187), ("grey", 0x7D8187), ("red", 0xC6302B), ("blue", 0x2F62B5), ("green", 0x3C7D4A),
        ("yellow", 0xE8C33A), ("orange", 0xE57A2E), ("brown", 0x6E4B32), ("beige", 0xD8C7A3), ("tan", 0xC9AE85),
        ("gold", 0xC9A646), ("purple", 0x6A4797), ("pink", 0xE58BB0), ("teal", 0x2A8C8C), ("bronze", 0x8C6A3F),
    ]

    static func color(for name: String) -> Color? {
        let n = name.lowercased()
        return paints.first { n.contains($0.0) }.map { Color(hex: $0.1) }
    }
}

// MARK: Palette

/// One hue, light to dark, for magnitude (the heatmap and bars).
enum SequentialRamp {
    static let light: [UInt32] = [0xCDE2FB, 0x86B6EF, 0x3987E5, 0x1C5CAB, 0x0D366B]
    static let dark: [UInt32] = [0x1A2E4A, 0x184F95, 0x2A78D6, 0x6DA7EC, 0xCDE2FB]
    static let bar = Color(light: 0x2A78D6, dark: 0x3987E5)

    /// Zero gets a recessive neutral so "nothing" reads differently from "a little".
    static func color(_ v: Double, peak: Double, scheme: ColorScheme) -> Color {
        guard v > 0, peak > 0 else { return Color.gray.opacity(scheme == .dark ? 0.18 : 0.12) }
        return interpolate(scheme == .dark ? dark : light, sqrt(min(v / peak, 1)))
    }

    static func interpolate(_ stops: [UInt32], _ t: Double) -> Color {
        let x = t * Double(stops.count - 1)
        let i = min(Int(x), stops.count - 2)
        let f = x - Double(i)
        func ch(_ hex: UInt32, _ shift: UInt32) -> Double { Double((hex >> shift) & 0xFF) / 255 }
        func mix(_ shift: UInt32) -> Double { ch(stops[i], shift) * (1 - f) + ch(stops[i + 1], shift) * f }
        return Color(red: mix(16), green: mix(8), blue: mix(0))
    }
}

enum DirectionPalette {
    static let leftToRight = Color(light: 0x2A78D6, dark: 0x3987E5)
    static let rightToLeft = Color(light: 0xEB6834, dark: 0xD95926)
}

private struct RampLegend: View {
    let scheme: ColorScheme

    var body: some View {
        HStack(spacing: 4) {
            Text("Fewer")
            HStack(spacing: 2) {
                ForEach([0.0, 0.1, 0.3, 0.6, 1.0], id: \.self) { v in
                    RoundedRectangle(cornerRadius: 2)
                        .fill(SequentialRamp.color(v, peak: 1, scheme: scheme))
                        .frame(width: 10, height: 10)
                }
            }
            Text("More")
        }
        .accessibilityHidden(true)
    }
}

extension Color {
    init(light: UInt32, dark: UInt32) {
        self.init(uiColor: UIColor { $0.userInterfaceStyle == .dark ? UIColor(Color(hex: dark)) : UIColor(Color(hex: light)) })
    }
}

private extension String {
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}

/// "6 AM", "12 PM"
private func hourLabel(_ hour: Int) -> String {
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = .gmt
    let d = cal.date(from: DateComponents(year: 2026, month: 1, day: 1, hour: hour)) ?? .now
    return d.formatted(Date.FormatStyle(timeZone: .gmt).hour(.defaultDigits(amPM: .abbreviated)))
}

// MARK: Entry point on Today

struct InsightsTile: View {
    var body: some View {
        NavigationLink {
            InsightsView()
        } label: {
            AdaptiveStack(spacing: 12) {
                Image(systemName: "chart.bar.xaxis")
                    .font(.title2)
                    .foregroundStyle(.tint)
                    .frame(width: 36)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Insights").font(.headline)
                    Text("Busy hours, which way traffic flows, top colors and makes")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                Spacer()
                Image(systemName: "chevron.right").foregroundStyle(.tertiary)
            }
            .padding()
            .background(.background.secondary, in: .rect(cornerRadius: 16))
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("today.insights")
    }
}
