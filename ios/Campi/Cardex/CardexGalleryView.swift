#if DEBUG
import CampiKit
import SwiftUI

/// `-cardexGallery <page>` (debug builds): sample cards for every holo pattern and scene theme, four per page,
/// for reviewing the art without data that happens to produce each one.
struct CardexGalleryView: View {
    let page: Int

    static let page: Int? = {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "-cardexGallery"), i + 1 < args.count else { return nil }
        return Int(args[i + 1])
    }()

    private struct Sample {
        var label: String
        var finish: CardFinish
        var pattern: HoloPattern?
        var theme: SceneTheme?
    }

    private static let samples: [Sample] = {
        var out: [Sample] = []
        let labels = ["Toyota Camry", "Ford Mustang", "Jeep Wrangler", "Ford F-150", "Tesla Model 3", "Honda Odyssey",
                      "Tesla Cybertruck", "Porsche 911", "Toyota RAV4", "Chevrolet Tahoe", "school bus", "Subaru Outback",
                      "Rivian R1S"]
        for (i, finish) in CardFinish.allCases.enumerated() {
            for (j, pattern) in HoloPattern.options(for: finish).enumerated() {
                out.append(Sample(label: labels[(i * 3 + j) % labels.count], finish: finish, pattern: pattern))
            }
        }
        for (i, theme) in SceneTheme.allCases.enumerated() {
            out.append(Sample(label: labels[(i + 5) % labels.count], finish: .specialIllustration, theme: theme))
        }
        return out
    }()

    private static func item(_ label: String) -> CollectionItem {
        let json = """
            {"label": "\(label)", "make": null, "model": null, "generic": false, "origin": "labels_file", "count": 3,
             "first_seen_at": "2026-10-01T09:00:00-05:00", "last_seen_at": "2026-10-03T09:00:00-05:00",
             "tier": "rare", "cover": null}
            """
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return try! d.decode(CollectionItem.self, from: Data(json.utf8))
    }

    var body: some View {
        let slice = Self.samples.dropFirst(page * 4).prefix(4)
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
            ForEach(Array(slice.enumerated()), id: \.offset) { _, sample in
                VStack(spacing: 4) {
                    TradingCardView(item: Self.item(sample.label), number: 1, total: 46, tilt: CGPoint(x: 0.5, y: -0.35),
                                    forcedPattern: sample.pattern, forcedTheme: sample.theme,
                                    forcedFinishOverride: sample.finish)
                    Text("\(sample.finish.title) · \(sample.pattern?.title ?? sample.theme?.title ?? "")")
                        .font(.caption2)
                }
            }
        }
        .padding(12)
    }
}
#endif
